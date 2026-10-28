"""Detect what the local machine can run."""
from __future__ import annotations

import importlib.util
import shutil
import subprocess

from .config import PRESETS, Settings, preset_for_vram
from .planner import llm_available


def gpus() -> list[dict]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        proc = subprocess.run(
            [exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = []
    for line in proc.stdout.strip().splitlines():
        name, _, mem = line.rpartition(",")
        try:
            out.append({"name": name.strip(), "vram_gb": round(float(mem) / 1024, 1)})
        except ValueError:
            continue
    return out


def report(settings: Settings) -> dict:
    cards = gpus()
    vram = max((g["vram_gb"] for g in cards), default=0)
    has_ml = all(importlib.util.find_spec(m) for m in ("torch", "diffusers"))
    rec = preset_for_vram(vram if has_ml else 0)
    return {
        "gpus": cards,
        "ffmpeg": shutil.which(settings.ffmpeg) is not None,
        "ml_runtime": has_ml,
        "llm": {"url": settings.ollama_url, "model": settings.llm_model, "available": llm_available(settings)},
        "recommended_preset": rec.name,
        "presets": [vars(p) for p in PRESETS.values()],
    }
