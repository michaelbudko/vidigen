"""Local media library: phonk tracks and high-energy stock footage.

Everything lives in one SQLite file next to the downloaded media. Tracks are
analysed once on import (tempo, beats, drop); footage gets a motion profile
(how much the picture changes every half second) so the editor can cut to the
most dynamic moments.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np

from . import audio, ffmpeg
from .config import Settings

AUDIO_EXT = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
MOTION_STEP = 0.5  # seconds per motion sample

# Footage categories and the searches that fill them.
CATEGORIES: dict[str, dict] = {
    "drift": {"label": "Drifting", "queries": ["car drift", "drifting", "tire smoke", "burnout"], "tags": "#drift #carsoftiktok"},
    "racing": {"label": "Racing", "queries": ["car race", "race track", "racing car", "street racing", "supercar"], "tags": "#racing #cars"},
    "moto": {"label": "Motorbikes", "queries": ["motorcycle wheelie", "motocross", "motorbike racing", "dirt bike"], "tags": "#moto #bikelife"},
    "fight": {"label": "Fight training", "queries": ["boxing", "mma training", "kickboxing", "punching bag", "sparring"], "tags": "#boxing #mma"},
    "extreme": {"label": "Extreme sports", "queries": ["skateboarding trick", "parkour", "snowboard jump", "bmx", "surfing big wave"], "tags": "#extreme #adrenaline"},
    "night": {"label": "Night city", "queries": ["night city driving", "neon city", "highway night", "tunnel driving"], "tags": "#nightdrive #cityvibes"},
    "gym": {"label": "Gym", "queries": ["weightlifting", "gym workout", "deadlift", "athlete training"], "tags": "#gym #motivation"},
}

SCHEMA = """
create table if not exists tracks (
  id text primary key, source text, source_id text, title text, artist text,
  license text, license_url text, page_url text, path text, duration real,
  tempo real, beats text, downbeats text, energy text, drop_at real,
  added real, used integer default 0, last_used real default 0,
  unique(source, source_id)
);
create table if not exists clips (
  id text primary key, source text, source_id text, author text, page_url text,
  path text, thumb text, category text, tags text, duration real, width integer, height integer,
  motion text, motion_mean real, added real, used integer default 0, last_used real default 0,
  unique(source, source_id)
);
create table if not exists cursors (key text primary key, page integer);
create table if not exists skips (source text, source_id text, primary key(source, source_id));
"""


@dataclass
class Track:
    id: str
    source: str
    title: str
    artist: str
    license: str
    license_url: str
    page_url: str
    path: str
    duration: float
    tempo: float
    beats: list[float]
    downbeats: list[float]
    energy: list[float]
    drop_at: float
    used: int
    last_used: float

    def analysis(self) -> audio.Analysis:
        return audio.Analysis(self.duration, self.tempo, self.beats, self.downbeats, self.energy)

    @property
    def non_commercial(self) -> bool:
        return "nc" in self.license.lower().split("-") or "nc" in self.license_url.lower()

    def credit(self) -> str:
        who = f'"{self.title}" by {self.artist}' if self.artist else f'"{self.title}"'
        lic = self.license.upper() if self.license else ""
        return " · ".join(p for p in [f"Music: {who}", lic, self.page_url] if p)

    def summary(self) -> dict:
        return {
            "id": self.id, "source": self.source, "title": self.title, "artist": self.artist,
            "license": self.license, "license_url": self.license_url, "page_url": self.page_url,
            "path": self.path, "duration": self.duration, "tempo": self.tempo, "drop_at": self.drop_at,
            "non_commercial": self.non_commercial, "used": self.used, "missing": not Path(self.path).is_file(),
        }


@dataclass
class Clip:
    id: str
    source: str
    author: str
    page_url: str
    path: str
    thumb: str
    category: str
    tags: str
    duration: float
    width: int
    height: int
    motion: list[float]
    motion_mean: float
    used: int
    last_used: float

    def best_window(self, length: float) -> float:
        """Start time of the `length`-second stretch with the most motion."""
        if length >= self.duration or not self.motion:
            return 0.0
        n = max(1, int(round(length / MOTION_STEP)))
        m = np.asarray(self.motion)
        if len(m) <= n:
            return 0.0
        sums = np.convolve(m, np.ones(n), mode="valid")
        start = int(np.argmax(sums)) * MOTION_STEP
        return float(min(start, max(0.0, self.duration - length - 0.05)))

    def summary(self) -> dict:
        return {
            "id": self.id, "source": self.source, "author": self.author, "page_url": self.page_url,
            "category": self.category, "duration": self.duration, "width": self.width, "height": self.height,
            "motion_mean": self.motion_mean, "used": self.used, "missing": not Path(self.path).is_file(),
        }


def motion_profile(ffmpeg_bin: str, path: Path) -> tuple[list[float], float]:
    """Mean absolute frame difference per half second on a tiny greyscale copy."""
    fps, w, h = 8, 64, 36
    proc = subprocess.run(
        [ffmpeg_bin, "-v", "error", "-i", str(path), "-vf", f"fps={fps},scale={w}:{h},format=gray",
         "-f", "rawvideo", "-"],
        capture_output=True,
    )
    frames = np.frombuffer(proc.stdout, dtype=np.uint8)
    n = len(frames) // (w * h)
    if n < 2:
        return [], 0.0
    frames = frames[: n * w * h].reshape(n, h, w).astype(np.float32)
    diff = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2)) / 255.0
    per = max(1, int(fps * MOTION_STEP))
    chunks = [float(diff[i : i + per].mean()) for i in range(0, len(diff), per)]
    # Scale so typical action footage lands around 0.5-1.0.
    scaled = [round(min(1.0, c * 8), 4) for c in chunks]
    return scaled, round(float(np.mean(scaled)), 4)


def video_size(ffmpeg_bin: str, path: Path) -> tuple[int, int]:
    proc = subprocess.run([ffmpeg_bin, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    import re

    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", proc.stderr)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


class Library:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.data_dir / "library"
        (self.root / "music").mkdir(parents=True, exist_ok=True)
        (self.root / "footage").mkdir(parents=True, exist_ok=True)
        (self.root / "thumbs").mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(self.root / "library.db", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self._lock:
            self.db.executescript(SCHEMA)

    # ---- queries -------------------------------------------------------

    def _q(self, sql: str, args: Iterable = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.db.execute(sql, tuple(args)).fetchall()

    def _w(self, sql: str, args: Iterable = ()) -> None:
        with self._lock:
            self.db.execute(sql, tuple(args))
            self.db.commit()

    @staticmethod
    def _track(r: sqlite3.Row) -> Track:
        return Track(
            id=r["id"], source=r["source"], title=r["title"], artist=r["artist"], license=r["license"],
            license_url=r["license_url"], page_url=r["page_url"], path=r["path"], duration=r["duration"],
            tempo=r["tempo"], beats=json.loads(r["beats"]), downbeats=json.loads(r["downbeats"]),
            energy=json.loads(r["energy"]), drop_at=r["drop_at"], used=r["used"], last_used=r["last_used"],
        )

    @staticmethod
    def _clip(r: sqlite3.Row) -> Clip:
        return Clip(
            id=r["id"], source=r["source"], author=r["author"], page_url=r["page_url"], path=r["path"],
            thumb=r["thumb"], category=r["category"], tags=r["tags"], duration=r["duration"], width=r["width"],
            height=r["height"], motion=json.loads(r["motion"]), motion_mean=r["motion_mean"], used=r["used"],
            last_used=r["last_used"],
        )

    def tracks(self) -> list[Track]:
        return [self._track(r) for r in self._q("select * from tracks order by added desc")]

    def track(self, tid: str) -> Track:
        rows = self._q("select * from tracks where id=?", [tid])
        if not rows:
            raise KeyError(tid)
        return self._track(rows[0])

    def clips(self, categories: list[str] | None = None) -> list[Clip]:
        if categories:
            marks = ",".join("?" * len(categories))
            rows = self._q(f"select * from clips where category in ({marks}) order by added desc", categories)
        else:
            rows = self._q("select * from clips order by added desc")
        return [self._clip(r) for r in rows]

    def clip(self, cid: str) -> Clip:
        rows = self._q("select * from clips where id=?", [cid])
        if not rows:
            raise KeyError(cid)
        return self._clip(rows[0])

    def has(self, table: str, source: str, source_id: str) -> bool:
        """True when the item is in the library, or failed to download before."""
        assert table in ("tracks", "clips")
        return bool(self._q(f"select 1 from {table} where source=? and source_id=? union all "
                            "select 1 from skips where source=? and source_id=?",
                            [source, source_id, source, source_id]))

    def skip(self, source: str, source_id: str) -> None:
        self._w("insert or ignore into skips(source, source_id) values(?, ?)", [source, source_id])

    def stats(self) -> dict:
        per_cat = {r["category"]: r["n"] for r in self._q("select category, count(*) n from clips group by category")}
        size = 0
        for sub in ("music", "footage"):
            for f in (self.root / sub).glob("*"):
                if f.is_file():
                    size += f.stat().st_size
        return {
            "tracks": self._q("select count(*) n from tracks")[0]["n"],
            "clips": self._q("select count(*) n from clips")[0]["n"],
            "per_category": per_cat,
            "disk_gb": round(size / 2**30, 2),
        }

    def mark_used(self, track_id: str, clip_ids: list[str]) -> None:
        now = time.time()
        self._w("update tracks set used=used+1, last_used=? where id=?", [now, track_id])
        for cid in set(clip_ids):
            self._w("update clips set used=used+1, last_used=? where id=?", [now, cid])

    def cursor(self, key: str) -> int:
        rows = self._q("select page from cursors where key=?", [key])
        return rows[0]["page"] if rows else 1

    def set_cursor(self, key: str, page: int) -> None:
        self._w("insert into cursors(key, page) values(?, ?) on conflict(key) do update set page=excluded.page",
                [key, page])

    # ---- adding media ----------------------------------------------------

    def add_track(self, path: Path, *, source: str, source_id: str, title: str, artist: str = "",
                  license: str = "", license_url: str = "", page_url: str = "") -> Track:
        a = audio.analyze(path, self.settings)
        if a.duration < 15:
            raise ValueError(f"{path.name} is too short ({a.duration:.0f}s)")
        drop = audio.find_drop(a)
        tid = uuid.uuid4().hex[:10]
        self._w(
            "insert into tracks(id, source, source_id, title, artist, license, license_url, page_url, path,"
            " duration, tempo, beats, downbeats, energy, drop_at, added) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [tid, source, source_id, title, artist, license, license_url, page_url, str(path), a.duration, a.tempo,
             json.dumps(a.beats), json.dumps(a.downbeats), json.dumps(a.energy), drop, time.time()],
        )
        return self.track(tid)

    def add_clip(self, path: Path, *, source: str, source_id: str, category: str, author: str = "",
                 page_url: str = "", tags: str = "") -> Clip:
        ff = self.settings.ffmpeg
        duration = ffmpeg.probe_duration(ff, path)
        if duration < 2:
            raise ValueError(f"{path.name} is too short")
        w, h = video_size(ff, path)
        motion, mean = motion_profile(ff, path)
        cid = uuid.uuid4().hex[:10]
        thumb = self.root / "thumbs" / f"{cid}.jpg"
        peak = (int(np.argmax(motion)) * MOTION_STEP) if motion else duration / 2
        ffmpeg.run([ff, "-y", "-v", "error", "-ss", f"{min(peak, duration - 0.1):.2f}", "-i", str(path),
                    "-frames:v", "1", "-vf", "scale=-2:320", str(thumb)])
        self._w(
            "insert into clips(id, source, source_id, author, page_url, path, thumb, category, tags, duration,"
            " width, height, motion, motion_mean, added) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [cid, source, source_id, author, page_url, str(path), str(thumb), category, tags, duration, w, h,
             json.dumps(motion), mean, time.time()],
        )
        return self.clip(cid)

    def import_folder(self, folder: Path, kind: str, *, license: str = "", category: str = "",
                      progress: Optional[Callable[[int, int, str], None]] = None) -> dict:
        """Add every audio or video file under `folder`, in place (no copy)."""
        exts = AUDIO_EXT if kind == "music" else VIDEO_EXT
        files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in exts and p.is_file())
        added, skipped, errors = 0, 0, []
        for i, f in enumerate(files):
            if progress:
                progress(i, len(files), f.name)
            key = str(f.resolve())
            table = "tracks" if kind == "music" else "clips"
            if self.has(table, "local", key):
                skipped += 1
                continue
            try:
                if kind == "music":
                    self.add_track(f, source="local", source_id=key, title=f.stem, license=license)
                else:
                    self.add_clip(f, source="local", source_id=key, category=category or "racing")
                added += 1
            except Exception as e:
                errors.append(f"{f.name}: {e}")
        return {"found": len(files), "added": added, "skipped": skipped, "errors": errors[:20]}

    def remove(self, kind: str, item_id: str, delete_file: bool = True) -> None:
        if kind == "track":
            t = self.track(item_id)
            self._w("delete from tracks where id=?", [item_id])
            path = Path(t.path)
        else:
            c = self.clip(item_id)
            self._w("delete from clips where id=?", [item_id])
            Path(c.thumb).unlink(missing_ok=True)
            path = Path(c.path)
        # Only delete files the app downloaded; imported folders stay untouched.
        if delete_file and self.root in path.resolve().parents:
            path.unlink(missing_ok=True)

    def update_track(self, tid: str, **fields) -> Track:
        allowed = {k: v for k, v in fields.items() if k in ("title", "artist", "license", "license_url") and v is not None}
        for k, v in allowed.items():
            self._w(f"update tracks set {k}=? where id=?", [v, tid])
        return self.track(tid)

    def media_path(self, kind: str, item_id: str, what: str) -> Path:
        if kind == "track":
            return Path(self.track(item_id).path)
        c = self.clip(item_id)
        return Path(c.thumb if what == "thumb" else c.path)

    def footage_bytes(self) -> int:
        d = self.root / "footage"
        return sum(f.stat().st_size for f in d.glob("*") if f.is_file())


def safe_name(s: str) -> str:
    keep = "".join(c if c.isalnum() or c in "-_" else "-" for c in s)
    return keep.strip("-")[:60] or uuid.uuid4().hex[:8]


def free_gb(path: Path) -> float:
    import shutil

    return shutil.disk_usage(path).free / 2**30


__all__ = ["Library", "Track", "Clip", "CATEGORIES", "safe_name", "free_gb", "os"]
