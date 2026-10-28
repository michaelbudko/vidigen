"""Thin wrappers around the ffmpeg and ffprobe binaries."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np


class FFmpegError(RuntimeError):
    pass


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise FFmpegError(f"{cmd[0]} failed ({proc.returncode}):\n{tail}")


def probe_duration(ffprobe: str, path: Path) -> float:
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe could not read {path}: {proc.stderr.strip()}")
    return float(json.loads(proc.stdout)["format"]["duration"])


def decode_mono(ffmpeg: str, path: Path, sr: int) -> np.ndarray:
    """Decode any audio file to a mono float32 array at the given sample rate."""
    proc = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg could not decode {path}: {proc.stderr.decode(errors='replace').strip()}")
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()
