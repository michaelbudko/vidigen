"""Final assembly: fit every shot to 1080x1920 at its exact beat-synced length,
cut them together, and lay the song clip underneath."""
from __future__ import annotations

from pathlib import Path

from . import ffmpeg
from .audio import Window
from .config import OUT_WIDTH, OUT_HEIGHT, OUT_FPS, Settings

ENCODE = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(OUT_FPS)]


def fit_clip(settings: Settings, src: Path, duration: float, out: Path) -> Path:
    """Scale/crop to 9:16 and make the clip exactly `duration` seconds.

    Short generations (AI models top out around 5 s) are slowed down up to 2x,
    then the last frame is held for whatever is left.
    """
    have = ffmpeg.probe_duration(settings.ffprobe, src)
    stretch = min(2.0, max(1.0, duration / have)) if have > 0 else 1.0
    vf = (
        f"setpts={stretch:.4f}*PTS,"
        f"scale={OUT_WIDTH}:{OUT_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={OUT_WIDTH}:{OUT_HEIGHT},setsar=1,fps={OUT_FPS},"
        f"tpad=stop_mode=clone:stop_duration={duration:.3f},"
        f"trim=duration={duration:.3f},setpts=PTS-STARTPTS"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run([settings.ffmpeg, "-y", "-v", "error", "-i", str(src), "-vf", vf, "-an", *ENCODE, str(out)])
    return out


def concat(settings: Settings, clips: list[Path], out: Path) -> Path:
    listing = out.with_suffix(".txt")
    listing.write_text("".join(f"file '{c.resolve().as_posix()}'\n" for c in clips), encoding="utf-8")
    ffmpeg.run([
        settings.ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
        "-c", "copy", str(out),
    ])
    return out


def mux(
    settings: Settings, video: Path, song: Path | None, window: Window, out: Path, metadata: dict | None = None
) -> Path:
    """Lay the song clip under the video. Without a song, add a silent track so
    every player and uploader treats the file the same way."""
    if song is not None:
        fade_out = min(1.5, window.duration / 10)
        audio_in = ["-i", str(song)]
        af = (
            f"atrim=start={window.start:.3f}:end={window.end:.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:d=0.15,afade=t=out:st={window.duration - fade_out:.3f}:d={fade_out:.3f}"
        )
    else:
        audio_in = ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
        af = "anull"
    meta = []
    for k, v in (metadata or {}).items():
        if v:
            meta += ["-metadata", f"{k}={v}"]
    ffmpeg.run([
        settings.ffmpeg, "-y", "-v", "error", "-i", str(video), *audio_in,
        "-map", "0:v:0", "-map", "1:a:0", "-af", af, "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-shortest",
        "-movflags", "+faststart", *meta, str(out),
    ])
    return out


def thumbnail(settings: Settings, video: Path, at: float, out: Path) -> Path:
    ffmpeg.run([
        settings.ffmpeg, "-y", "-v", "error", "-ss", f"{at:.3f}", "-i", str(video),
        "-frames:v", "1", "-vf", "scale=360:-2", str(out),
    ])
    return out
