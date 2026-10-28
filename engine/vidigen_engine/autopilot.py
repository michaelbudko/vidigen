"""Daily autopilot: once a day, top up the library and make a batch of edits.

The day's run starts at the configured time, or as soon as the app is running
after that time, so a PC that was off at 9:00 still gets its edits that day.
On Windows it can also be registered with Task Scheduler, so the edits get
made even when the app is closed. A lock file keeps the two from overlapping.
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Callable, Optional

from pydantic import BaseModel, Field

from . import sources
from .autoedit import Edit, EditRequest
from .config import Settings
from .library import Library


class AutopilotConfig(BaseModel):
    enabled: bool = False
    per_day: int = Field(default=3, ge=1, le=20)
    at: str = Field(default="09:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    duration: float = Field(default=20.0, ge=8, le=90)
    intensity: str = "hard"
    look: str = "auto"
    categories: list[str] = Field(default_factory=list)  # empty rotates through all themes
    hooks: list[str] = Field(default_factory=list)  # one is picked per edit; empty means no text
    allow_nc: bool = True
    refresh: bool = True  # download new footage and music before editing
    clips_per_day: int = Field(default=20, ge=0, le=200)
    tracks_per_day: int = Field(default=5, ge=0, le=50)
    max_footage_gb: float = Field(default=40.0, ge=1, le=4000)
    background: bool = False  # Windows: run from Task Scheduler even when the app is closed


TASK_NAME = "Vidigen daily edits"
LOCK_STALE = 4 * 3600


def task_xml(at: str, python: str, workdir: str, today: Optional[str] = None) -> str:
    """Task Scheduler definition: daily at `at`, and as soon as the PC is on
    if that time was missed. Runs as the signed-in user, without a console."""
    day = today or datetime.now().strftime("%Y-%m-%d")
    esc = lambda v: v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")  # noqa: E731
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Makes the day's phonk edits with Vidigen.</Description></RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{day}T{at}:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT4H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{esc(python)}</Command>
      <Arguments>-m vidigen_engine autopilot</Arguments>
      <WorkingDirectory>{esc(workdir)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def background_python() -> str:
    """pythonw.exe beside the running interpreter, so no console window opens."""
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.is_file() else exe)


def schedule_background(at: Optional[str], run=subprocess.run) -> str:
    """Register (or with at=None, remove) the Task Scheduler entry. Returns '' or an error."""
    if os.name != "nt":
        return "Running while the app is closed needs Windows Task Scheduler." if at else ""
    flags = {"capture_output": True, "text": True, "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    if at is None:
        run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], **flags)
        return ""
    workdir = str(Path(__file__).resolve().parent.parent)
    fd, path = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    try:
        Path(path).write_text(task_xml(at, background_python(), workdir), encoding="utf-16")
        r = run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", path, "/F"], **flags)
        return "" if r.returncode == 0 else (r.stderr or r.stdout or "schtasks failed").strip()
    finally:
        os.unlink(path)


class Autopilot:
    def __init__(self, settings: Settings, lib: Library, submit: Callable[[EditRequest], Edit],
                 library_lock: threading.Lock):
        self.settings, self.lib, self.submit, self.library_lock = settings, lib, submit, library_lock
        self.file = settings.data_dir / "autopilot.json"
        data = self._read()
        self.config = AutopilotConfig(**data.get("config", {}))
        self.last_day: str = data.get("last_day", "")
        self.last_result: dict = data.get("last_result", {})
        self.running = False
        self.message = ""
        self._thread: Optional[threading.Thread] = None
        self._cancel = threading.Event()

    def _read(self) -> dict:
        try:
            return json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        data = {"config": self.config.model_dump(), "last_day": self.last_day, "last_result": self.last_result}
        self.file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def set_config(self, cfg: AutopilotConfig, schedule=schedule_background) -> str:
        """Save the settings and keep the Task Scheduler entry in step. Returns '' or an error."""
        old = self.config
        want = cfg.enabled and cfg.background
        had = old.enabled and old.background
        error = ""
        if want and (not had or old.at != cfg.at):
            error = schedule(cfg.at)
            if error:
                cfg = cfg.model_copy(update={"background": False})
        elif had and not want:
            schedule(None)
        now = datetime.now()
        if cfg.enabled and not old.enabled and now >= self._today_at_for(cfg, now):
            # Switched on after today's time: start tomorrow rather than right away.
            # "Run today's batch now" is there for today.
            self.last_day = now.strftime("%Y-%m-%d")
        self.config = cfg
        self._save()
        return error

    # ---- cross-process lock (the app and the scheduled task share the data folder)

    @property
    def _lock(self) -> Path:
        return self.settings.data_dir / "autopilot.lock"

    def _acquire(self) -> bool:
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self._lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                stale = time.time() - self._lock.stat().st_mtime > LOCK_STALE
            except OSError:
                stale = True
            if not stale:
                return False
            self._lock.unlink(missing_ok=True)
            return self._acquire()
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True

    def busy_elsewhere(self) -> bool:
        if self.running or not self._lock.exists():
            return False
        try:
            return time.time() - self._lock.stat().st_mtime < LOCK_STALE
        except OSError:
            return False

    def _reload(self) -> None:
        data = self._read()
        self.last_day = data.get("last_day", self.last_day)
        self.last_result = data.get("last_result", self.last_result)

    # ---- schedule

    @staticmethod
    def _today_at_for(cfg: AutopilotConfig, now: datetime) -> datetime:
        h, m = (int(x) for x in cfg.at.split(":"))
        return now.replace(hour=h, minute=m, second=0, microsecond=0)

    def _today_at(self, now: datetime) -> datetime:
        return self._today_at_for(self.config, now)

    def due(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now()
        return (self.config.enabled and not self.running and self.last_day != now.strftime("%Y-%m-%d")
                and now >= self._today_at(now))

    def next_run(self, now: Optional[datetime] = None) -> Optional[str]:
        if not self.config.enabled:
            return None
        now = now or datetime.now()
        at = self._today_at(now)
        if self.last_day == now.strftime("%Y-%m-%d") or (now >= at and self.running):
            at += timedelta(days=1)
        return max(at, now).isoformat(timespec="minutes")

    def start(self) -> None:
        if self.config.enabled and self.config.background and os.name == "nt":
            # Re-register in case the engine's Python moved (a rebuilt environment).
            threading.Thread(target=schedule_background, args=(self.config.at,), daemon=True).start()

        def loop():
            while True:
                try:
                    if self.due():
                        self.run(scheduled=True)
                except Exception as e:  # keep the scheduler alive whatever happens
                    self.message = f"Autopilot error: {e}"
                time.sleep(30)

        threading.Thread(target=loop, daemon=True).start()

    # ---- a run

    def run_async(self) -> bool:
        if self.running:
            return False
        self._thread = threading.Thread(target=self.run, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._cancel.set()

    def run(self, scheduled: bool = False, client=None) -> dict:
        if self.running or not self._acquire():
            return self.last_result
        day = datetime.now().strftime("%Y-%m-%d")
        self._reload()
        if scheduled and self.last_day == day:  # another process got there first
            self._lock.unlink(missing_ok=True)
            return self.last_result
        self.running = True
        self._cancel.clear()
        cfg = self.config
        result = {"day": day, "started": time.time(), "edits": [], "failed": 0, "clips_added": 0,
                  "tracks_added": 0, "errors": [], "scheduled": scheduled}
        try:
            if cfg.refresh and (cfg.clips_per_day or cfg.tracks_per_day):
                keys = sources.load_keys(self.settings)
                if any(keys.values()):
                    with self.library_lock:
                        def progress(f, msg):
                            self.message = f"Updating the library: {msg}"

                        r = sources.refresh(
                            self.lib, clips=cfg.clips_per_day, tracks=cfg.tracks_per_day,
                            categories=cfg.categories or None, max_footage_gb=cfg.max_footage_gb,
                            client=client, progress=progress, cancel=self._cancel,
                        )
                    result["clips_added"], result["tracks_added"] = r.clips_added, r.tracks_added
                    result["errors"] += r.to_dict()["errors"]
            rng = random.Random()
            for i in range(cfg.per_day):
                if self._cancel.is_set():
                    break
                self.message = f"Making edit {i + 1} of {cfg.per_day}"
                req = EditRequest(
                    categories=cfg.categories, duration=cfg.duration, intensity=cfg.intensity, look=cfg.look,
                    hook=rng.choice(cfg.hooks).strip()[:120] if cfg.hooks else "", allow_nc=cfg.allow_nc, auto=True,
                )
                edit = self.submit(req)
                while edit.status in ("queued", "running"):
                    if self._cancel.is_set():
                        edit.cancel()
                    time.sleep(0.5)
                if edit.status == "done":
                    result["edits"].append(edit.id)
                else:
                    result["failed"] += 1
                    if edit.error:
                        result["errors"].append(edit.error)
                    if edit.error and "library" in edit.error.lower():
                        break  # nothing to edit with; the other edits would fail the same way
        finally:
            result["finished"] = time.time()
            self.last_day = day
            self.last_result = result
            self.running = False
            made = len(result["edits"])
            self.message = f"Made {made} edit{'s' if made != 1 else ''}" if made else "No edits made"
            self._save()
            self._lock.unlink(missing_ok=True)
        return result

    def to_dict(self) -> dict:
        if not self.running:
            self._reload()  # the scheduled task may have run while the app was closed
        elsewhere = self.busy_elsewhere()
        return {"config": self.config.model_dump(), "running": self.running or elsewhere,
                "message": "Making today's edits in the background" if elsewhere else self.message,
                "last_day": self.last_day, "last_result": self.last_result, "next_run": self.next_run(),
                "background_supported": os.name == "nt"}
