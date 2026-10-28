"""Phonk edits: stock footage cut to the beat of a phonk track.

The edit opens a couple of bars before the drop with slower, longer shots,
hits the drop with a white flash, zoom punch, shake and RGB split, then cuts
every beat or two with short bursts in the loudest parts. Each shot uses the
most dynamic stretch of its clip, found from the library's motion profiles.
"""
from __future__ import annotations

import json
import random
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

from . import compose, ffmpeg
from .audio import Window
from .config import OUT_FPS, OUT_HEIGHT, OUT_WIDTH, Settings
from .library import CATEGORIES, MOTION_STEP, Clip, Library, Track

ENCODE = ["-c:v", "libx264", "-preset", "fast", "-crf", "19", "-pix_fmt", "yuv420p", "-r", str(OUT_FPS)]
EFFECTS = ("flash", "punch", "shake", "rgb", "slowmo")
LOOKS: dict[str, dict] = {
    "dark": {"label": "Dark", "vf": "eq=contrast=1.18:saturation=0.8:brightness=-0.04,vignette=PI/4.5"},
    "teal": {"label": "Teal & orange",
             "vf": "colorbalance=rs=-0.06:gs=-0.02:bs=0.08:rh=0.08:gh=0.02:bh=-0.06,eq=contrast=1.12:saturation=1.1"},
    "mono": {"label": "Black & white", "vf": "hue=s=0,eq=contrast=1.3:brightness=-0.03,vignette=PI/4"},
    "warm": {"label": "Warm", "vf": "colorbalance=rs=0.06:bs=-0.06:rm=0.04,eq=contrast=1.1:saturation=1.15"},
    "none": {"label": "Original", "vf": ""},
}
# Beats per shot before and after the drop, and how often loud parts cut on every beat.
INTENSITY = {
    "chill": {"label": "Chill", "pre": 8, "post": 4, "burst": 0.0},
    "hard": {"label": "Hard", "pre": 4, "post": 2, "burst": 0.3},
    "insane": {"label": "Insane", "pre": 2, "post": 1, "burst": 0.0},
}
MIN_SHOT = 0.25
SLOWMO = 0.5


class EditRequest(BaseModel):
    track_id: Optional[str] = None  # None picks the least used track
    categories: list[str] = Field(default_factory=list)  # empty picks a theme
    duration: float = Field(default=20.0, ge=8, le=90)
    intensity: Literal["chill", "hard", "insane"] = "hard"
    look: str = "dark"  # a LOOKS key, or "auto"
    effects: list[str] = Field(default_factory=lambda: list(EFFECTS))
    hook: str = Field(default="", max_length=120)  # text shown over the first seconds
    allow_nc: bool = True  # may use non-commercial (CC BY-NC) tracks
    seed: Optional[int] = None
    auto: bool = False  # made by the autopilot


@dataclass
class Shot:
    start: float  # in the edit
    duration: float
    fx: list[str]
    speed: float = 1.0
    clip: str = ""
    source: str = ""
    clip_start: float = 0.0

    def to_dict(self) -> dict:
        return {"start": round(self.start, 3), "duration": round(self.duration, 3), "fx": self.fx,
                "speed": self.speed, "clip": self.clip, "source": self.source, "clip_start": round(self.clip_start, 3)}


@dataclass
class Edit:
    id: str
    request: EditRequest
    dir: Path
    status: str = "queued"  # queued | running | done | failed | cancelled
    progress: float = 0.0
    message: str = ""
    error: str = ""
    track: dict = field(default_factory=dict)
    theme: list[str] = field(default_factory=list)
    look: str = ""
    window: dict = field(default_factory=dict)
    drop: float = 0.0
    shots: list[dict] = field(default_factory=list)
    caption: str = ""
    credits: list[str] = field(default_factory=list)
    output: str = ""
    thumbnail: str = ""
    created: float = field(default_factory=time.time)
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def update(self, progress: float, message: str) -> None:
        if self._cancel.is_set():
            raise Cancelled()
        self.progress, self.message = round(progress, 4), message

    def cancel(self) -> None:
        self._cancel.set()

    def to_dict(self) -> dict:
        return {
            "id": self.id, "status": self.status, "progress": self.progress, "message": self.message,
            "error": self.error, "track": self.track, "theme": self.theme, "look": self.look,
            "window": self.window, "drop": self.drop, "shots": self.shots, "caption": self.caption,
            "credits": self.credits, "output": self.output, "thumbnail": self.thumbnail,
            "created": self.created, "day": time.strftime("%Y-%m-%d", time.localtime(self.created)),
            "request": self.request.model_dump(mode="json"),
        }

    def save(self) -> None:
        (self.dir / "edit.json").write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")


class Cancelled(Exception):
    pass


# ---- planning -------------------------------------------------------------------

def pulse(beats: list[float]) -> list[float]:
    """Beats at roughly 0.3-0.6 s apart. Trackers often lock onto half time in
    phonk, which would make every cut twice as slow as intended."""
    b = list(beats)
    while len(b) > 2 and float(np.median(np.diff(b))) > 0.6:
        b = sorted(b + [(x + y) / 2 for x, y in zip(b, b[1:])])
    return b


def pick_track(lib: Library, req: EditRequest, rng: random.Random) -> Track:
    if req.track_id:
        t = lib.track(req.track_id)
        if not Path(t.path).is_file():
            raise ValueError(f'The file for "{t.title}" is missing.')
        return t
    tracks = [t for t in lib.tracks() if Path(t.path).is_file() and t.duration >= req.duration + 2]
    if not req.allow_nc:
        tracks = [t for t in tracks if not t.non_commercial]
    if not tracks:
        raise ValueError("No tracks in the library that are long enough. Add music on the Library tab.")
    tracks.sort(key=lambda t: (t.used, t.last_used))
    least = [t for t in tracks if t.used == tracks[0].used]
    return rng.choice(least[:5])


def pick_theme(lib: Library, req: EditRequest, rng: random.Random) -> list[str]:
    counts = lib.stats()["per_category"]
    if req.categories:
        cats = [c for c in req.categories if counts.get(c)]
        if not cats:
            raise ValueError("No footage in the chosen categories yet. Add some on the Library tab.")
        return cats
    stocked = [c for c, n in counts.items() if n >= 4] or [c for c, n in counts.items() if n]
    if not stocked:
        raise ValueError("The footage library is empty. Add footage on the Library tab.")
    # Rotate themes: the category least used lately goes next.
    usage = {c: 0.0 for c in stocked}
    for c in lib.clips(stocked):
        usage[c.category] = max(usage[c.category], c.last_used)
    order = sorted(stocked, key=lambda c: usage[c] + rng.random())
    return order[:1] if counts[order[0]] >= 8 or len(order) == 1 else order[:2]


def place_window(track: Track, duration: float) -> tuple[Window, float]:
    """Window of `duration` seconds that reaches the drop after a short build-up.
    Returns the window and the drop's time inside it."""
    beats = pulse(track.beats) or [0.0]
    period = float(np.median(np.diff(beats))) if len(beats) > 2 else 0.45
    lead = min(16 * period, 0.3 * duration)  # about two bars of build-up
    latest = max(0.0, track.duration - duration - 0.05)
    target = min(max(0.0, track.drop_at - lead), latest)
    anchors = [d for d in (track.downbeats or beats) if d <= latest] or [0.0]
    start = min(anchors, key=lambda d: abs(d - target))
    drop = track.drop_at - start
    if not 0 <= drop < duration - 1:
        drop = 0.0
    return Window(round(start, 3), round(start + duration, 3)), round(drop, 3)


def plan_shots(track: Track, window: Window, drop: float, req: EditRequest, rng: random.Random) -> list[Shot]:
    style = INTENSITY[req.intensity]
    beats = [b - window.start for b in pulse(track.beats) if window.start <= b < window.end - MIN_SHOT]
    if not beats or beats[0] > 0.05:
        beats = [0.0] + beats
    energy = track.energy
    loud = np.percentile(energy, 75) if energy else 1.0

    cuts = [0.0]
    i = 0
    while i < len(beats) - 1:
        t = beats[i]
        before_drop = t < drop - 0.05
        step = style["pre"] if before_drop else style["post"]
        if not before_drop and style["burst"] and energy:
            e = energy[min(len(energy) - 1, int(window.start + t))]
            if e >= loud and rng.random() < style["burst"]:
                step = 1
        nxt = i + step
        # Never step over the drop: it always gets its own cut.
        if before_drop and drop > 0:
            k = next((j for j in range(i + 1, len(beats)) if beats[j] >= drop - 0.05), len(beats))
            nxt = min(nxt, k)
        if nxt >= len(beats):
            break
        cuts.append(beats[nxt])
        i = nxt
    if drop > 0 and all(abs(c - drop) > 0.05 for c in cuts):
        cuts.append(drop)
    cuts = sorted(c for c in cuts if c < window.duration - MIN_SHOT)
    # Drop slivers.
    merged = [cuts[0]]
    for c in cuts[1:]:
        if c - merged[-1] >= MIN_SHOT:
            merged.append(c)
    cuts = merged + [window.duration]

    downbeats = [d - window.start for d in track.downbeats if window.start <= d < window.end]
    fx_on = set(req.effects)
    shots: list[Shot] = []
    for n, (a, b) in enumerate(zip(cuts, cuts[1:])):
        fx: list[str] = []
        at_drop = drop > 0 and abs(a - drop) < 0.06
        after = a >= drop - 0.05
        on_bar = any(abs(a - d) < 0.06 for d in downbeats)
        if at_drop or (drop == 0 and n == 0):
            fx += ["flash", "punch", "shake", "rgb"]
        elif after and on_bar:
            fx.append("punch")
            bar = sum(1 for d in downbeats if drop - 0.05 <= d < a - 0.05)
            if bar % 4 == 0 or rng.random() < 0.2:
                fx.append("flash")
            if rng.random() < 0.4:
                fx.append("shake")
        elif after and b - a < 0.4:
            fx.append("shake")
        speed = SLOWMO if (not after and "slowmo" in fx_on) else 1.0
        shots.append(Shot(start=a, duration=b - a, fx=[f for f in fx if f in fx_on], speed=speed))
    return shots


def window_options(clip: Clip, length: float) -> list[float]:
    """Start times of the clip's most dynamic `length`-second stretches, best first."""
    if length >= clip.duration - 0.1 or not clip.motion:
        return [0.0]
    n = max(1, int(round(length / MOTION_STEP)))
    m = np.asarray(clip.motion)
    if len(m) <= n:
        return [0.0]
    sums = np.convolve(m, np.ones(n), mode="valid")
    order = np.argsort(-sums)
    latest = max(0.0, clip.duration - length - 0.05)
    out: list[float] = []
    for idx in order:
        s = min(float(idx) * MOTION_STEP, latest)
        if all(abs(s - o) >= length for o in out):
            out.append(s)
        if len(out) >= 4:
            break
    return out or [0.0]


def cast_clips(lib: Library, shots: list[Shot], theme: list[str], drop: float, rng: random.Random) -> None:
    pool = [c for c in lib.clips(theme) if Path(c.path).is_file()]
    if not pool:
        raise ValueError("No footage files found for this theme.")
    now = time.time()
    used: dict[str, list[float]] = {}
    last = ""
    for s in shots:
        need = s.duration * s.speed + 0.05

        def score(c: Clip) -> float:
            fresh = 0.0 if c.id in used else 1.0
            recent = 0.5 if now - c.last_used < 3 * 86400 else 0.0
            fit = 1.0 if c.duration >= need else 0.3
            hype = c.motion_mean * (1.5 if s.start >= drop else 0.8)
            return fresh * 2 + fit + hype - recent - 0.05 * min(c.used, 10) + rng.random() * 0.6

        options = sorted((c for c in pool if c.id != last or len(pool) == 1), key=score, reverse=True)
        clip = options[0]
        starts = window_options(clip, need)
        taken = used.setdefault(clip.id, [])
        start = next((o for o in starts if all(abs(o - t) >= need for t in taken)), rng.choice(starts))
        taken.append(start)
        s.clip, s.source, s.clip_start = clip.id, clip.path, start
        last = clip.id


# ---- rendering --------------------------------------------------------------------

def _font(size: int) -> ImageFont.ImageFont:
    for f in ("C:/Windows/Fonts/impact.ttf", "C:/Windows/Fonts/arialbd.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/System/Library/Fonts/Supplemental/Impact.ttf"):
        if Path(f).is_file():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default(size=size)


def hook_image(text: str, out: Path) -> Path:
    """White text with a black outline in the upper third, on a transparent frame."""
    img = Image.new("RGBA", (OUT_WIDTH, OUT_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _font(84)
    words, lines, line = text.upper().split(), [], ""
    for w in words:
        trial = f"{line} {w}".strip()
        if draw.textlength(trial, font=font) > OUT_WIDTH * 0.84 and line:
            lines.append(line)
            line = w
        else:
            line = trial
    if line:
        lines.append(line)
    y = int(OUT_HEIGHT * 0.2)
    for ln in lines[:4]:
        w = draw.textlength(ln, font=font)
        draw.text(((OUT_WIDTH - w) / 2, y), ln, font=font, fill="white", stroke_width=7, stroke_fill="black")
        y += 104
    img.save(out)
    return out


def shot_filter(shot: Shot, look: str) -> str:
    T = f"(in/{OUT_FPS})"
    parts = [
        f"setpts={1 / shot.speed:.4f}*(PTS-STARTPTS)",
        f"fps={OUT_FPS}",
        f"scale={OUT_WIDTH}:{OUT_HEIGHT}:force_original_aspect_ratio=increase",
        f"crop={OUT_WIDTH}:{OUT_HEIGHT}",
        "setsar=1",
    ]
    zoom, sx, sy = [], "0", "0"
    if "punch" in shot.fx:
        zoom.append(f"0.2*max(0,1-{T}/0.28)")
    if "shake" in shot.fx:
        decay = f"max(0,1-{T}/0.45)"
        zoom.append(f"0.06*{decay}")
        sx = f"iw*0.02*sin(in*2.4)*{decay}"
        sy = f"ih*0.012*cos(in*2.1)*{decay}"
    if shot.speed < 1:
        zoom.append(f"0.1*{T}/{max(shot.duration, 0.1):.3f}")  # slow push-in during the build-up
    if zoom:
        z = "1+" + "+".join(zoom)
        parts.append(
            f"zoompan=z='{z}':x='iw/2-iw/zoom/2+{sx}':y='ih/2-ih/zoom/2+{sy}':d=1:s={OUT_WIDTH}x{OUT_HEIGHT}:fps={OUT_FPS}"
        )
    if LOOKS.get(look, {}).get("vf"):
        parts.append(LOOKS[look]["vf"])
    if "rgb" in shot.fx:
        parts.append("rgbashift=rh=-12:bh=12:enable='lt(t,0.2)'")
    if "flash" in shot.fx:
        parts.append("fade=t=in:st=0:d=0.14:color=white")
    d = f"{shot.duration:.3f}"
    parts += [f"tpad=stop_mode=clone:stop_duration={d}", f"trim=duration={d}", "setpts=PTS-STARTPTS"]
    return ",".join(parts)


def render_shot(settings: Settings, shot: Shot, look: str, out: Path, hook: Optional[Path], hook_until: float) -> Path:
    need = shot.duration * shot.speed + 0.1
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [settings.ffmpeg, "-y", "-v", "error", "-ss", f"{shot.clip_start:.3f}", "-t", f"{need:.3f}",
           "-i", shot.source]
    vf = shot_filter(shot, look)
    if hook is not None and shot.start < hook_until:
        cmd += ["-i", str(hook)]
        graph = f"[0:v]{vf}[v];[v][1:v]overlay=0:0:enable='lt(t,{hook_until - shot.start:.3f})'[out]"
        cmd += ["-filter_complex", graph, "-map", "[out]"]
    else:
        cmd += ["-vf", vf]
    ffmpeg.run([*cmd, "-an", *ENCODE, str(out)])
    return out


def caption_for(track: Track, theme: list[str], hook: str) -> str:
    tags = " ".join(dict.fromkeys(" ".join(CATEGORIES[c]["tags"] for c in theme if c in CATEGORIES).split()))
    lines = [hook.strip(), track.credit(), f"{tags} #phonk #edit #fyp".strip()]
    return "\n\n".join(p for p in lines if p)


def build(edit: Edit, lib: Library) -> Edit:
    req = edit.request
    settings = lib.settings
    rng = random.Random(req.seed if req.seed is not None else uuid.uuid4().int)
    edit.status = "running"
    try:
        edit.update(0.02, "Picking a track")
        track = pick_track(lib, req, rng)
        theme = pick_theme(lib, req, rng)
        look = req.look if req.look in LOOKS else rng.choice(["dark", "dark", "teal", "mono", "warm"])
        window, drop = place_window(track, min(req.duration, track.duration))
        shots = plan_shots(track, window, drop, req, rng)
        cast_clips(lib, shots, theme, drop, rng)
        edit.track = {**track.summary(), "credit": track.credit()}
        edit.theme, edit.look, edit.drop = theme, look, drop
        edit.window = {"start": window.start, "end": window.end}
        edit.shots = [s.to_dict() for s in shots]
        edit.save()

        hook = hook_image(req.hook, edit.dir / "hook.png") if req.hook.strip() else None
        hook_until = min(3.0, max(1.5, drop)) if drop else 2.5
        parts = []
        for i, s in enumerate(shots):
            edit.update(0.05 + 0.85 * i / len(shots), f"Cutting shot {i + 1} of {len(shots)}")
            parts.append(render_shot(settings, s, look, edit.dir / "shots" / f"{i:03d}.mp4", hook, hook_until))
        edit.update(0.92, "Mixing the track")
        silent = compose.concat(settings, parts, edit.dir / "shots" / "all.mp4")
        out = edit.dir / f"{safe_title(track.title)}-{edit.id[-6:]}.mp4"
        compose.mux(settings, silent, Path(track.path), window, out,
                    {"title": track.title, "artist": track.artist, "comment": track.credit()})
        thumb = compose.thumbnail(settings, out, min(drop + 0.3, window.duration - 0.1), edit.dir / "thumb.jpg")
        shutil.rmtree(edit.dir / "shots", ignore_errors=True)

        clips = {s.clip for s in shots}
        lib.mark_used(track.id, list(clips))
        sources = sorted({(c.source, c.author) for c in lib.clips(theme) if c.id in clips})
        edit.credits = [track.credit()] + [f"Footage: {a or 'unknown'} ({s})" for s, a in sources]
        edit.caption = caption_for(track, theme, req.hook)
        (edit.dir / "caption.txt").write_text(edit.caption + "\n", encoding="utf-8")
        edit.output, edit.thumbnail = str(out), str(thumb)
        edit.status, edit.progress, edit.message = "done", 1.0, "Ready"
    except Cancelled:
        edit.status, edit.message = "cancelled", "Cancelled"
    except Exception as e:
        edit.status, edit.error = "failed", str(e) or type(e).__name__
    edit.save()
    return edit


def safe_title(s: str) -> str:
    keep = "".join(c if c.isalnum() else "-" for c in s.lower())
    return "-".join(p for p in keep.split("-") if p)[:40] or "edit"


# ---- store ---------------------------------------------------------------------------

class EditStore:
    """Edits live in edits/<day>/<id>/ with an edit.json beside the video."""

    def __init__(self, settings: Settings):
        self.root = settings.data_dir / "edits"
        self.live: dict[str, Edit] = {}

    def new(self, req: EditRequest) -> Edit:
        day = time.strftime("%Y-%m-%d")
        eid = time.strftime("%H%M%S-") + uuid.uuid4().hex[:6]
        d = self.root / day / eid
        d.mkdir(parents=True, exist_ok=True)
        e = Edit(id=f"{day}_{eid}", request=req, dir=d)
        self.live[e.id] = e
        e.save()
        return e

    def _dir(self, eid: str) -> Path:
        day, _, rest = eid.partition("_")
        d = (self.root / day / rest).resolve()
        if self.root.resolve() not in d.parents:
            raise KeyError(eid)
        return d

    def get(self, eid: str) -> dict:
        if eid in self.live:
            return self.live[eid].to_dict()
        f = self._dir(eid) / "edit.json"
        if not f.is_file():
            raise KeyError(eid)
        data = json.loads(f.read_text(encoding="utf-8"))
        if data.get("status") in ("queued", "running"):
            data["status"], data["error"] = "failed", "The app closed before this edit finished."
        return data

    def list(self, limit: int = 300) -> list[dict]:
        out = []
        if self.root.is_dir():
            for f in sorted(self.root.glob("*/*/edit.json"), reverse=True)[:limit]:
                eid = f"{f.parent.parent.name}_{f.parent.name}"
                try:
                    out.append(self.get(eid))
                except (KeyError, ValueError, OSError):
                    continue
        return sorted(out, key=lambda e: e.get("created", 0), reverse=True)

    def delete(self, eid: str) -> None:
        if eid in self.live and self.live[eid].status in ("queued", "running"):
            self.live[eid].cancel()
        self.live.pop(eid, None)
        d = self._dir(eid)
        if d.is_dir():
            shutil.rmtree(d)

    def file(self, eid: str, kind: str) -> Path:
        e = self.get(eid)
        p = Path(e["output"] if kind == "video" else e["thumbnail"]) if e.get("output") else None
        if p is None or not p.is_file() or self._dir(eid) not in p.resolve().parents:
            raise KeyError(eid)
        return p
