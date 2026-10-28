"""First-run setup: install the GPU runtime, download a preset's models, and
optionally install a local LLM. Runs in the background and reports per-step
progress for the Welcome screen."""
from __future__ import annotations

import fnmatch
import importlib
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Optional

import httpx

from . import hardware
from .config import (
    FLUX_FACE_ADAPTER, FLUX_FACE_ENCODER, PRESETS, SDXL_FACE_ADAPTER, SDXL_FACE_ENCODER, Preset, Settings,
)

TORCH_CUDA_INDEX = "https://download.pytorch.org/whl/cu128"  # CUDA 12.8 covers RTX 50-series (Blackwell)
ML_PACKAGES = [
    "diffusers>=0.35",
    "transformers>=4.46",
    "accelerate>=1.0",
    "huggingface_hub>=0.26",
    "safetensors",
    "sentencepiece",
    "protobuf",
    "ftfy",
    "imageio[ffmpeg]",
    "opencv-python-headless",
]
SKIP = ["*.bin", "*.pth", "*.ckpt", "*.onnx", "*.onnx_data", "*.msgpack", "*.h5", "*.gguf"]


@dataclass
class Download:
    repo: str
    label: str
    allow: list[str]


def downloads_for(p: Preset) -> list[Download]:
    if p.image_backend != "diffusers":
        return []
    out = []
    name = p.image_model.split("/")[-1]
    if "flux" in p.image_model.lower():
        out.append(Download(p.image_model, name, ["model_index.json", "*/*"]))
        out.append(Download(FLUX_FACE_ADAPTER[0], "Face adapter", [FLUX_FACE_ADAPTER[1]]))
        out.append(Download(FLUX_FACE_ENCODER, "Face encoder", ["*.json", "*.txt", "model.safetensors"]))
    else:
        # fp16 weights only; SDXL repos also carry fp32 copies we do not need.
        out.append(Download(p.image_model, name, [
            "model_index.json", "*/config.json", "*/*.txt", "*/*.json", "*/*.fp16.safetensors",
        ]))
        repo, sub, weight = SDXL_FACE_ADAPTER
        out.append(Download(repo, "Face adapter", [f"{sub}/{weight}", f"{SDXL_FACE_ENCODER[1]}/*"]))
    if p.video_model:
        out.append(Download(p.video_model, p.video_model.split("/")[-1], ["model_index.json", "*/*"]))
    return out


def models_present(p: Preset) -> bool:
    """True when every repo the preset needs has at least its index file cached."""
    if p.image_backend != "diffusers":
        return True
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return False
    for d in downloads_for(p):
        probe = next((a for a in d.allow if "*" not in a), d.allow[0])
        if "*" in probe:
            probe = "model_index.json"
        hit = try_to_load_from_cache(d.repo, probe)
        if not isinstance(hit, str):
            return False
    return True


@dataclass
class Step:
    key: str
    label: str
    status: str = "pending"  # pending | running | done | skipped | failed
    progress: Optional[float] = None  # None = indeterminate
    detail: str = ""


@dataclass
class SetupTask:
    preset: str
    llm: Optional[str]
    steps: list[Step] = field(default_factory=list)
    status: str = "running"  # running | done | failed
    error: str = ""
    log: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.time)

    def step(self, key: str) -> Step:
        return next(s for s in self.steps if s.key == key)

    def say(self, line: str) -> None:
        line = line.rstrip()
        if line:
            self.log = (self.log + [line])[-40:]

    def to_dict(self) -> dict:
        return asdict(self)


def _pip(task: SetupTask, step: Step, args: list[str]) -> None:
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off", *args]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    assert proc.stdout
    for line in proc.stdout:
        task.say(line)
        if line.startswith(("Collecting", "Downloading", "Installing", "Successfully")):
            step.detail = line.strip()[:140]
    if proc.wait() != 0:
        raise RuntimeError(f"pip failed while installing {' '.join(args[:2])}. See the log for details.")
    importlib.invalidate_caches()


def _cache_bytes(repo: str) -> int:
    from huggingface_hub.constants import HF_HUB_CACHE

    blobs = Path(HF_HUB_CACHE) / f"models--{repo.replace('/', '--')}" / "blobs"
    if not blobs.is_dir():
        return 0
    return sum(f.stat().st_size for f in blobs.iterdir() if f.is_file())


def _download(task: SetupTask, step: Step, items: list[Download]) -> None:
    from huggingface_hub import HfApi, snapshot_download

    api = HfApi()
    totals = []
    for d in items:
        info = api.model_info(d.repo, files_metadata=True)
        size = sum(
            (s.size or 0) for s in info.siblings
            if any(fnmatch.fnmatch(s.rfilename, a) for a in d.allow)
            and not any(fnmatch.fnmatch(s.rfilename, x) for x in SKIP)
        )
        totals.append(size)
    grand = max(1, sum(totals))
    done_before = 0
    for d, size in zip(items, totals):
        start = _cache_bytes(d.repo)
        stop = threading.Event()

        def watch(d=d, size=size, start=start, base=done_before):
            while not stop.wait(1.0):
                got = min(size, max(0, _cache_bytes(d.repo) - start))
                step.progress = (base + got) / grand
                step.detail = f"{d.label}: {got / 2**30:.1f} of {size / 2**30:.1f} GB"

        t = threading.Thread(target=watch, daemon=True)
        t.start()
        try:
            task.say(f"Downloading {d.repo}")
            snapshot_download(d.repo, allow_patterns=d.allow, ignore_patterns=SKIP)
        finally:
            stop.set()
            t.join()
        done_before += size
        step.progress = done_before / grand


def _ensure_ollama(task: SetupTask, step: Step, settings: Settings) -> None:
    exe = hardware.ollama_path()
    if not exe:
        if os.name != "nt":
            raise RuntimeError("Install Ollama from https://ollama.com, then run setup again.")
        step.detail = "Installing Ollama"
        proc = subprocess.run(
            ["winget", "install", "-e", "--id", "Ollama.Ollama", "--silent",
             "--accept-package-agreements", "--accept-source-agreements"],
            capture_output=True, text=True,
        )
        task.say(proc.stdout[-2000:])
        exe = hardware.ollama_path()
        if not exe:
            raise RuntimeError("Could not install Ollama with winget. Install it from https://ollama.com and retry.")
    try:
        httpx.get(f"{settings.ollama_url}/api/tags", timeout=2)
    except httpx.HTTPError:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        for _ in range(30):
            time.sleep(1)
            try:
                httpx.get(f"{settings.ollama_url}/api/tags", timeout=2)
                break
            except httpx.HTTPError:
                continue
        else:
            raise RuntimeError("Ollama is installed but did not start.")


def _pull_llm(task: SetupTask, step: Step, settings: Settings, model: str) -> None:
    with httpx.stream("POST", f"{settings.ollama_url}/api/pull", json={"model": model}, timeout=None) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            msg = json.loads(line)
            if msg.get("error"):
                raise RuntimeError(msg["error"])
            if msg.get("total"):
                step.progress = msg.get("completed", 0) / msg["total"]
                step.detail = f"{model}: {msg.get('completed', 0) / 2**30:.1f} of {msg['total'] / 2**30:.1f} GB"
            elif msg.get("status"):
                step.detail = f"{model}: {msg['status']}"


def plan(preset: str, llm: Optional[str]) -> SetupTask:
    p = PRESETS[preset]
    needs_ml = p.image_backend == "diffusers"
    task = SetupTask(preset=preset, llm=llm)
    task.steps = [
        Step("runtime", "GPU runtime (PyTorch, CUDA 12.8)", status="pending" if needs_ml else "skipped"),
        Step("packages", "AI libraries", status="pending" if needs_ml else "skipped"),
        Step("models", f"{p.label} models (~{p.download_gb:.0f} GB)", status="pending" if needs_ml else "skipped"),
        Step("llm", f"Local director LLM ({llm})" if llm else "Local director LLM", status="pending" if llm else "skipped"),
    ]
    return task


def run(task: SetupTask, settings: Settings, on_done: Callable[[], None] | None = None) -> SetupTask:
    p = PRESETS[task.preset]
    current: Step | None = None
    try:
        for current in task.steps:
            if current.status == "skipped":
                continue
            current.status = "running"
            if current.key == "runtime":
                info = hardware.torch_info()
                if info.get("cuda_available") and hardware.cuda_at_least(info, 12, 8):
                    current.detail = f"Already installed: torch {info['version']}"
                elif hardware.gpus():
                    _pip(task, current, ["torch", "torchvision", "--index-url", TORCH_CUDA_INDEX])
                else:
                    _pip(task, current, ["torch", "torchvision"])
            elif current.key == "packages":
                _pip(task, current, ML_PACKAGES)
            elif current.key == "models":
                current.progress = 0.0
                _download(task, current, downloads_for(p))
            elif current.key == "llm":
                _ensure_ollama(task, current, settings)
                _pull_llm(task, current, settings, task.llm or settings.llm_model)
                settings.llm_model = task.llm or settings.llm_model
            current.status, current.progress = "done", 1.0
        state = {"complete": True, "preset": task.preset, "completed_at": time.time()}
        if task.llm:
            state["llm_model"] = task.llm
        settings.save_state(**state)
        task.status = "done"
    except Exception as e:
        if current:
            current.status = "failed"
        task.status, task.error = "failed", str(e)
        task.say(f"ERROR: {e}")
    finally:
        if on_done:
            on_done()
    return task
