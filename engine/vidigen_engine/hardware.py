"""Detect what the local machine can run, for the Welcome/setup screen."""
from __future__ import annotations

import importlib
import json
import importlib.util
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .config import PRESETS, Settings, preset_for_vram
from .planner import llm_available


def _run(cmd: list[str], timeout: float = 8) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def gpus() -> list[dict]:
    exe = shutil.which("nvidia-smi")
    if not exe and os.name == "nt":
        cand = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe"
        exe = str(cand) if cand.is_file() else None
    if not exe:
        return []
    out = []
    raw = _run([exe, "--query-gpu=name,memory.total,driver_version,compute_cap", "--format=csv,noheader,nounits"])
    for line in raw.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            out.append({
                "name": parts[0],
                "vram_gb": round(float(parts[1]) / 1024, 1),
                "driver": parts[2] if len(parts) > 2 else "",
                "compute": parts[3] if len(parts) > 3 else "",
            })
        except ValueError:
            continue
    return out


def cpu_name() -> str:
    if os.name == "nt":
        name = _run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"])
        if name:
            return name.splitlines()[0].strip()
    elif Path("/proc/cpuinfo").is_file():
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    elif sys.platform == "darwin":
        return _run(["sysctl", "-n", "machdep.cpu.brand_string"])
    return platform.processor() or "Unknown CPU"


def ram_gb() -> float | None:
    if os.name == "nt":
        import ctypes

        class MemStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        m = MemStatus()
        m.dwLength = ctypes.sizeof(MemStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return round(m.ullTotalPhys / 2**30, 1)
        return None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal"):
                return round(int(line.split()[1]) / 2**20, 1)
    except OSError:
        pass
    return None


def disk_free_gb(path: Path) -> float:
    p = path
    while not p.exists() and p.parent != p:
        p = p.parent
    return round(shutil.disk_usage(p).free / 2**30, 1)


TORCH_PROBE = """
import json, torch
ok = torch.cuda.is_available()
print(json.dumps({"installed": True, "version": torch.__version__, "cuda": torch.version.cuda,
                  "cuda_available": ok, "device": torch.cuda.get_device_name(0) if ok else ""}))
"""


def torch_info() -> dict:
    # Probe in a child process: importing torch here would lock its DLLs on
    # Windows and make the setup step unable to replace a CPU-only build.
    importlib.invalidate_caches()
    if importlib.util.find_spec("torch") is None:
        return {"installed": False}
    try:
        proc = subprocess.run([sys.executable, "-c", TORCH_PROBE], capture_output=True, text=True, timeout=120)
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception as e:  # a broken install should not take the API down
        return {"installed": True, "error": str(e)}


def cuda_at_least(info: dict, major: int, minor: int) -> bool:
    try:
        a, b = (int(x) for x in str(info.get("cuda") or "0.0").split(".")[:2])
        return (a, b) >= (major, minor)
    except ValueError:
        return False


def ollama_path() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    if os.name == "nt":
        cand = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
        if cand.is_file():
            return str(cand)
    return None


def recommend_llm(vram: float, ram: float | None) -> str | None:
    if vram >= 10:
        return "qwen3:8b"
    if (ram or 0) >= 16:
        return "qwen3:4b"
    return None


def report(settings: Settings) -> dict:
    from . import installer

    cards = gpus()
    vram = max((g["vram_gb"] for g in cards), default=0)
    ram = ram_gb()
    torch = torch_info()
    has_ml = torch.get("installed", False) and importlib.util.find_spec("diffusers") is not None
    hw_preset = preset_for_vram(vram)
    state = settings.load_state()
    ffmpeg_ok = bool(_run([settings.ffmpeg, "-version"])[:6] == "ffmpeg")
    return {
        "os": f"{platform.system()} {platform.release()}",
        "python": sys.version.split()[0],
        "cpu": {"name": cpu_name(), "cores": os.cpu_count()},
        "ram_gb": ram,
        "disk_free_gb": disk_free_gb(settings.data_dir),
        "gpus": cards,
        "ffmpeg": ffmpeg_ok,
        "torch": torch,
        "ml_runtime": has_ml,
        "llm": {
            "url": settings.ollama_url,
            "model": settings.llm_model,
            "installed": ollama_path() is not None,
            "available": llm_available(settings),
            "recommended": recommend_llm(vram, ram),
        },
        "hardware_preset": hw_preset.name,
        # Until setup has installed a preset's models, only Test mode can actually render.
        "recommended_preset": state.get("preset", "mock") if state.get("complete") else "mock",
        "presets": [{**vars(p), "downloaded": installer.models_present(p)} for p in PRESETS.values()],
        "setup": state,
    }
