"""Render job orchestration: analyse -> plan -> keyframes -> motion -> assemble."""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from . import audio, compose, ffmpeg, planner
from .backends import make_image_backend, make_video_backend
from .config import MIN_DURATION, MAX_DURATION, PRESETS, Settings
from .licensing import SongRights


class RenderRequest(BaseModel):
    # Only the prompt is required. Without a song the cuts follow a beat grid at
    # `bpm` and the video is exported silent, ready for a sound added in TikTok.
    prompt: str = Field(min_length=3)
    song_path: Optional[str] = None
    style: str = ""
    rights: SongRights = Field(default_factory=SongRights)
    bpm: float = Field(default=120.0, ge=60, le=200)
    duration: float = Field(default=45.0, ge=MIN_DURATION, le=MAX_DURATION)
    start: Optional[float] = None  # seconds into the song; None picks the most energetic part
    preset: str = "mock"
    use_llm: bool = True
    seed: int = 42
    # Optional footage for video-to-video: each shot restyles the matching part of this clip.
    source_video: Optional[str] = None


class Cancelled(Exception):
    pass


@dataclass
class Job:
    id: str
    request: RenderRequest
    dir: Path
    status: str = "queued"  # queued | running | done | failed | cancelled
    stage: str = ""
    progress: float = 0.0
    message: str = ""
    error: str = ""
    planner: str = ""
    shots: list[dict] = field(default_factory=list)
    window: dict = field(default_factory=dict)
    output: str = ""
    thumbnail: str = ""
    caption: str = ""
    created: float = field(default_factory=time.time)
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def update(self, stage: str, progress: float, message: str = "") -> None:
        if self._cancel.is_set():
            raise Cancelled()
        self.stage, self.progress, self.message = stage, round(progress, 4), message

    def cancel(self) -> None:
        self._cancel.set()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "message": self.message,
            "error": self.error,
            "planner": self.planner,
            "shots": self.shots,
            "window": self.window,
            "output": self.output,
            "thumbnail": self.thumbnail,
            "caption": self.caption,
            "created": self.created,
            "request": self.request.model_dump(mode="json"),
        }


def new_job(req: RenderRequest, settings: Settings) -> Job:
    jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    d = settings.jobs_dir / jid
    d.mkdir(parents=True, exist_ok=True)
    return Job(id=jid, request=req, dir=d)


def build_caption(req: RenderRequest) -> str:
    credit = req.rights.credit_line() if req.song_path else ""
    tags = "#musicvideo #aiart #fyp"
    return "\n".join(p for p in [req.prompt.strip(), credit, tags] if p)


def run(job: Job, settings: Settings) -> Job:
    req = job.request
    if req.preset not in PRESETS:
        raise ValueError(f"unknown preset {req.preset}")
    preset = PRESETS[req.preset]
    song = Path(req.song_path) if req.song_path else None
    job.status = "running"
    try:
        if song:
            job.update("analyze", 0.02, "Finding the beat")
            analysis = audio.analyze(song, settings)
            window = audio.choose_window(analysis, req.duration, req.start)
        else:
            job.update("analyze", 0.02, f"Using a {req.bpm:.0f} BPM beat grid")
            analysis = audio.beat_grid(req.duration, req.bpm)
            window = audio.Window(0.0, req.duration)
        job.window = {"start": window.start, "end": window.end, "tempo": analysis.tempo}

        job.update("plan", 0.06, "Planning shots")
        shots, job.planner = planner.plan(
            analysis, window, req.prompt, req.style, settings, use_llm=req.use_llm, seed=req.seed
        )
        job.shots = [s.to_dict() for s in shots]
        n = len(shots)

        raw: list[Path] = []
        video = make_video_backend(
            preset.video_backend, preset.video_model, preset.gen_width, preset.gen_height, settings.ffmpeg
        )
        if req.source_video:
            raw = _restyle_source(job, shots, video, settings)
        else:
            images = make_image_backend(preset.image_backend, preset.image_model, preset.gen_width, preset.gen_height)
            keyframes = []
            for i, s in enumerate(shots):
                job.update("keyframes", 0.08 + 0.3 * i / n, f"Keyframe {i + 1} of {n}")
                keyframes.append(images.generate(s.prompt, req.seed + i, job.dir / "keyframes" / f"{i:03d}.png"))
            images.unload()
            for i, (s, kf) in enumerate(zip(shots, keyframes)):
                job.update("motion", 0.38 + 0.45 * i / n, f"Animating shot {i + 1} of {n}")
                raw.append(video.animate(
                    kf, s.prompt, s.motion, s.intensity, s.duration, req.seed + i, job.dir / "raw" / f"{i:03d}.mp4"
                ))
        video.unload()

        fitted = []
        for i, (s, clip) in enumerate(zip(shots, raw)):
            job.update("assemble", 0.83 + 0.12 * i / n, f"Cutting shot {i + 1} of {n}")
            fitted.append(compose.fit_clip(settings, clip, s.duration, job.dir / "fit" / f"{i:03d}.mp4"))

        job.update("mux", 0.96, "Adding the song" if song else "Finishing")
        silent = compose.concat(settings, fitted, job.dir / "video_only.mp4")
        job.caption = build_caption(req)
        out = compose.mux(
            settings, silent, song, window, job.dir / "vidigen.mp4",
            metadata={"comment": req.rights.credit_line() if song else "", "description": req.prompt},
        )
        job.output = str(out)
        job.thumbnail = str(compose.thumbnail(settings, out, min(1.0, window.duration / 2), job.dir / "thumb.jpg"))
        job.update("done", 1.0, "Done")
        job.status = "done"
    except Cancelled:
        job.status, job.message = "cancelled", "Cancelled"
    except ModuleNotFoundError as e:
        job.status = "failed"
        job.error = (
            f"The {req.preset} preset needs the AI packages ({e.name} is missing). "
            "Run setup.ps1 to install them, or pick the mock preset."
        )
    except Exception as e:  # surfaced to the UI
        job.status, job.error = "failed", str(e)
    finally:
        (job.dir / "job.json").write_text(json.dumps(job.to_dict(), indent=2), encoding="utf-8")
    return job


def _restyle_source(job: Job, shots: list[planner.Shot], video, settings: Settings) -> list[Path]:
    src = Path(job.request.source_video)
    length = ffmpeg.probe_duration(settings.ffprobe, src)
    out = []
    n = len(shots)
    for i, s in enumerate(shots):
        job.update("motion", 0.08 + 0.75 * i / n, f"Restyling shot {i + 1} of {n}")
        seg = job.dir / "source" / f"{i:03d}.mp4"
        seg.parent.mkdir(parents=True, exist_ok=True)
        # Loop the source if the song window is longer than the footage.
        offset = s.start % max(length - 0.5, 0.5)
        ffmpeg.run([
            settings.ffmpeg, "-y", "-v", "error", "-stream_loop", "-1", "-ss", f"{offset:.3f}", "-i", str(src),
            "-t", f"{s.duration:.3f}", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(seg),
        ])
        out.append(video.restyle(seg, s.prompt, s.duration, job.request.seed + i, job.dir / "raw" / f"{i:03d}.mp4"))
    return out
