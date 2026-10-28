"""Thin wrappers around the ffmpeg binary (no ffprobe needed, so the bundled
imageio-ffmpeg build is enough)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np


class FFmpegError(RuntimeError):
    pass


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise FFmpegError(f"ffmpeg failed ({proc.returncode}):\n{tail}")


def probe_duration(ffmpeg: str, path: Path) -> float:
    # `ffmpeg -i` with no output exits non-zero but prints the container duration.
    proc = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr)
    if not m:
        raise FFmpegError(f"could not read {path}")
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


def decode_mono(ffmpeg: str, path: Path, sr: int) -> np.ndarray:
    """Decode any audio file to a mono float32 array at the given sample rate."""
    proc = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg could not decode {path}: {proc.stderr.decode(errors='replace').strip()}")
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()
