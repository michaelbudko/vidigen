"""Engine settings and hardware presets."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

# 9:16 vertical, TikTok's native upload size.
OUT_WIDTH = 1080
OUT_HEIGHT = 1920
OUT_FPS = 30

MIN_DURATION = 30.0
MAX_DURATION = 90.0


@dataclass
class Preset:
    """A bundle of model choices sized for a VRAM budget."""

    name: str
    min_vram_gb: float
    image_backend: str
    image_model: str
    video_backend: str
    video_model: str
    gen_width: int
    gen_height: int
    description: str


PRESETS: dict[str, Preset] = {
    "mock": Preset(
        name="mock",
        min_vram_gb=0,
        image_backend="mock",
        image_model="",
        video_backend="kenburns",
        video_model="",
        gen_width=576,
        gen_height=1024,
        description="No AI models. Procedural art plus pan/zoom motion. For testing the pipeline.",
    ),
    "lite": Preset(
        name="lite",
        min_vram_gb=6,
        image_backend="diffusers",
        image_model="stabilityai/sdxl-turbo",
        video_backend="kenburns",
        video_model="",
        gen_width=576,
        gen_height=1024,
        description="SDXL-Turbo stills animated with pan/zoom. Runs on 6-8 GB cards.",
    ),
    "balanced": Preset(
        name="balanced",
        min_vram_gb=12,
        image_backend="diffusers",
        image_model="stabilityai/sdxl-turbo",
        video_backend="ltx",
        video_model="Lightricks/LTX-Video-0.9.7-distilled",
        gen_width=512,
        gen_height=896,
        description="SDXL-Turbo keyframes animated with LTX-Video image-to-video. 12-16 GB cards.",
    ),
    "quality": Preset(
        name="quality",
        min_vram_gb=24,
        image_backend="diffusers",
        image_model="black-forest-labs/FLUX.1-schnell",
        video_backend="wan",
        video_model="Wan-AI/Wan2.2-TI2V-5B-Diffusers",
        gen_width=704,
        gen_height=1280,
        description="FLUX.1-schnell keyframes animated with Wan 2.2. 24 GB cards.",
    ),
}


def preset_for_vram(vram_gb: float | None) -> Preset:
    if not vram_gb:
        return PRESETS["mock"]
    best = PRESETS["lite"]
    for p in PRESETS.values():
        if p.name != "mock" and vram_gb >= p.min_vram_gb and p.min_vram_gb >= best.min_vram_gb:
            best = p
    return best


def default_data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return Path(os.environ.get("VIDIGEN_DATA_DIR", base / "Vidigen"))


@dataclass
class Settings:
    data_dir: Path = field(default_factory=default_data_dir)
    ollama_url: str = field(default_factory=lambda: os.environ.get("VIDIGEN_OLLAMA_URL", "http://127.0.0.1:11434"))
    llm_model: str = field(default_factory=lambda: os.environ.get("VIDIGEN_LLM_MODEL", "qwen3:8b"))
    ffmpeg: str = field(default_factory=lambda: os.environ.get("VIDIGEN_FFMPEG", "ffmpeg"))
    ffprobe: str = field(default_factory=lambda: os.environ.get("VIDIGEN_FFPROBE", "ffprobe"))

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["data_dir"] = str(self.data_dir)
        return d
