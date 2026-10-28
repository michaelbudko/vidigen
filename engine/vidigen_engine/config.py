"""Engine settings and hardware presets."""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field, asdict
from pathlib import Path

# 9:16 vertical, TikTok's native upload size.
OUT_WIDTH = 1080
OUT_HEIGHT = 1920
OUT_FPS = 30

MIN_DURATION = 30.0
MAX_DURATION = 90.0

# IP-Adapter weights that carry a character's face into each keyframe.
SDXL_FACE_ADAPTER = ("h94/IP-Adapter", "sdxl_models", "ip-adapter-plus-face_sdxl_vit-h.safetensors")
SDXL_FACE_ENCODER = ("h94/IP-Adapter", "models/image_encoder")
FLUX_FACE_ADAPTER = ("XLabs-AI/flux-ip-adapter", "ip_adapter.safetensors")
FLUX_FACE_ENCODER = "openai/clip-vit-large-patch14"


@dataclass
class Preset:
    """A bundle of model choices sized for a VRAM budget."""

    name: str
    label: str
    min_vram_gb: float
    image_backend: str
    image_model: str
    image_steps: int
    video_backend: str
    video_model: str
    gen_width: int
    gen_height: int
    download_gb: float  # rough first-time download, for the setup screen
    description: str


PRESETS: dict[str, Preset] = {
    "mock": Preset(
        name="mock",
        label="Test mode",
        min_vram_gb=0,
        image_backend="mock",
        image_model="",
        image_steps=0,
        video_backend="kenburns",
        video_model="",
        gen_width=576,
        gen_height=1024,
        download_gb=0,
        description="No AI models. Abstract art with pan/zoom, characters pasted in. For trying the app.",
    ),
    "lite": Preset(
        name="lite",
        label="Lite",
        min_vram_gb=6,
        image_backend="diffusers",
        image_model="stabilityai/sdxl-turbo",
        image_steps=4,
        video_backend="kenburns",
        video_model="",
        gen_width=576,
        gen_height=1024,
        download_gb=9,
        description="SDXL-Turbo stills animated with pan/zoom. Fast, runs on 6-8 GB cards.",
    ),
    "balanced": Preset(
        name="balanced",
        label="Balanced",
        min_vram_gb=12,
        image_backend="diffusers",
        image_model="stabilityai/stable-diffusion-xl-base-1.0",
        image_steps=25,
        video_backend="ltx",
        video_model="Lightricks/LTX-Video-0.9.7-distilled",
        gen_width=576,
        gen_height=1024,
        download_gb=32,
        description="SDXL keyframes animated with LTX-Video. Real motion. Best fit for 12-16 GB cards.",
    ),
    "quality": Preset(
        name="quality",
        label="Quality",
        min_vram_gb=24,
        image_backend="diffusers",
        image_model="black-forest-labs/FLUX.1-schnell",
        image_steps=4,
        video_backend="wan",
        video_model="Wan-AI/Wan2.2-TI2V-5B-Diffusers",
        gen_width=704,
        gen_height=1280,
        download_gb=60,
        description="FLUX.1-schnell keyframes animated with Wan 2.2. Slowest and sharpest, 24 GB cards.",
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


def find_ffmpeg() -> str:
    """System ffmpeg if there is one, else the binary bundled with imageio-ffmpeg."""
    if os.environ.get("VIDIGEN_FFMPEG"):
        return os.environ["VIDIGEN_FFMPEG"]
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


@dataclass
class Settings:
    data_dir: Path = field(default_factory=default_data_dir)
    ollama_url: str = field(default_factory=lambda: os.environ.get("VIDIGEN_OLLAMA_URL", "http://127.0.0.1:11434"))
    llm_model: str = field(default_factory=lambda: os.environ.get("VIDIGEN_LLM_MODEL", "qwen3:8b"))
    ffmpeg: str = field(default_factory=find_ffmpeg)

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def characters_dir(self) -> Path:
        return self.data_dir / "characters"

    @property
    def state_file(self) -> Path:
        return self.data_dir / "setup.json"

    def load_state(self) -> dict:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save_state(self, **updates) -> dict:
        state = {**self.load_state(), **updates}
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        return state

    def to_dict(self) -> dict:
        d = asdict(self)
        d["data_dir"] = str(self.data_dir)
        return d
