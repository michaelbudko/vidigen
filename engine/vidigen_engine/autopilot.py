"""Daily autopilot: once a day, top up the library and make a batch of edits.

The day's run starts at the configured time, or as soon as the app is running
after that time, so a PC that was off at 9:00 still gets its edits that day.
"""
from __future__ import annotations

import json
import random
import threading
import time
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

    def set_config(self, cfg: AutopilotConfig) -> None:
        self.config = cfg
        self._save()

    # ---- schedule

    def _today_at(self, now: datetime) -> datetime:
        h, m = (int(x) for x in self.config.at.split(":"))
        return now.replace(hour=h, minute=m, second=0, microsecond=0)

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
        if self.running:
            return self.last_result
        self.running = True
        self._cancel.clear()
        cfg = self.config
        day = datetime.now().strftime("%Y-%m-%d")
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
        return result

    def to_dict(self) -> dict:
        return {"config": self.config.model_dump(), "running": self.running, "message": self.message,
                "last_day": self.last_day, "last_result": self.last_result, "next_run": self.next_run()}
