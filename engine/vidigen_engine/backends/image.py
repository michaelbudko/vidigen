from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


class ImageBackend:
    def generate(self, prompt: str, seed: int, out: Path) -> Path:
        raise NotImplementedError

    def unload(self) -> None:
        """Free GPU memory so the video model has room."""


class MockImageBackend(ImageBackend):
    """Procedural abstract art seeded by the prompt. No models needed."""

    def __init__(self, width: int, height: int):
        self.width, self.height = width, height

    def generate(self, prompt: str, seed: int, out: Path) -> Path:
        h = int(hashlib.sha256(f"{prompt}|{seed}".encode()).hexdigest(), 16)
        rng = np.random.default_rng(h % (2**32))
        w, ht = self.width, self.height
        c1, c2 = rng.integers(0, 255, 3), rng.integers(0, 255, 3)
        t = np.linspace(0, 1, ht)[:, None, None]
        grad = (c1 * (1 - t) + c2 * t).astype(np.uint8)
        img = Image.fromarray(np.broadcast_to(grad, (ht, w, 3)).copy())
        draw = ImageDraw.Draw(img, "RGBA")
        for _ in range(14):
            r = int(rng.integers(40, w // 2))
            cx, cy = int(rng.integers(0, w)), int(rng.integers(0, ht))
            col = tuple(int(v) for v in rng.integers(0, 255, 3)) + (int(rng.integers(60, 170)),)
            if rng.random() < 0.5:
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
            else:
                pts = [
                    (cx + r * math.cos(a), cy + r * math.sin(a))
                    for a in np.linspace(0, 2 * math.pi, int(rng.integers(3, 7)), endpoint=False)
                ]
                draw.polygon(pts, fill=col)
        img = img.filter(ImageFilter.GaussianBlur(2))
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out)
        return out


class DiffusersImageBackend(ImageBackend):
    """Text-to-image with Hugging Face diffusers (SDXL-Turbo, FLUX.1-schnell, ...)."""

    def __init__(self, model: str, width: int, height: int):
        self.model, self.width, self.height = model, width, height
        self.pipe = None

    def _load(self):
        import torch
        from diffusers import AutoPipelineForText2Image

        dtype = torch.bfloat16 if "flux" in self.model.lower() else torch.float16
        pipe = AutoPipelineForText2Image.from_pretrained(self.model, torch_dtype=dtype)
        if "flux" in self.model.lower():
            # FLUX does not fit most consumer cards without offload.
            pipe.enable_model_cpu_offload()
        else:
            pipe.to("cuda")
        self.pipe = pipe

    def generate(self, prompt: str, seed: int, out: Path) -> Path:
        import torch

        if self.pipe is None:
            self._load()
        turbo = "turbo" in self.model.lower() or "schnell" in self.model.lower()
        kwargs = dict(
            prompt=prompt,
            width=self.width,
            height=self.height,
            num_inference_steps=4 if turbo else 30,
            generator=torch.Generator("cpu").manual_seed(seed),
        )
        if "turbo" in self.model.lower():
            kwargs["guidance_scale"] = 0.0
        image = self.pipe(**kwargs).images[0]
        out.parent.mkdir(parents=True, exist_ok=True)
        image.save(out)
        return out

    def unload(self) -> None:
        if self.pipe is not None:
            import torch

            self.pipe = None
            torch.cuda.empty_cache()
