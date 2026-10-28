from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from ..config import FLUX_FACE_ADAPTER, FLUX_FACE_ENCODER, SDXL_FACE_ADAPTER, SDXL_FACE_ENCODER

NEGATIVE = "blurry, deformed face, distorted, extra fingers, watermark, text, low quality"


class ImageBackend:
    def generate(
        self,
        prompt: str,
        seed: int,
        out: Path,
        refs: list[Path] | None = None,
        likeness: float = 0.7,
        square: bool = False,
    ) -> Path:
        """Render `prompt`. With `refs` (face crops), keep that person's likeness;
        `likeness` 0..1 trades resemblance against following the prompt."""
        raise NotImplementedError

    def unload(self) -> None:
        """Free GPU memory so the video model has room."""


class MockImageBackend(ImageBackend):
    """Procedural abstract art seeded by the prompt, with the reference face
    pasted in. No models needed; lets every screen be exercised without a GPU."""

    def __init__(self, width: int, height: int):
        self.width, self.height = width, height

    def generate(self, prompt, seed, out, refs=None, likeness=0.7, square=False):
        h = int(hashlib.sha256(f"{prompt}|{seed}".encode()).hexdigest(), 16)
        rng = np.random.default_rng(h % (2**32))
        w, ht = (self.width, self.width) if square else (self.width, self.height)
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
        if refs:
            face = Image.open(refs[int(rng.integers(0, len(refs)))]).convert("RGB")
            size = int(w * (0.55 + 0.35 * likeness))
            face = ImageOps.fit(face, (size, size))
            mask = Image.new("L", (size, size), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, size, size], fill=255)
            mask = mask.filter(ImageFilter.GaussianBlur(size // 40))
            img.paste(face, ((w - size) // 2, int(ht * 0.42 - size / 2) if not square else (ht - size) // 2), mask)
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out)
        return out


class DiffusersImageBackend(ImageBackend):
    """Text-to-image with Hugging Face diffusers (SDXL, SDXL-Turbo, FLUX.1-schnell).

    Character likeness comes from an IP-Adapter face model, loaded the first time
    a shot has reference faces.
    """

    def __init__(self, model: str, steps: int):
        self.model, self.steps = model, steps
        self.flux = "flux" in model.lower()
        self.turbo = "turbo" in model.lower()
        self.pipe = None
        self.adapter = False

    def size(self, square: bool) -> tuple[int, int]:
        if self.turbo:
            return (512, 512) if square else (576, 1024)
        return (1024, 1024) if square else (768, 1344)

    def _load(self):
        import torch
        from diffusers import AutoPipelineForText2Image

        if self.flux:
            pipe = AutoPipelineForText2Image.from_pretrained(self.model, torch_dtype=torch.bfloat16)
            pipe.enable_model_cpu_offload()  # FLUX does not fit 16 GB cards otherwise
        else:
            pipe = AutoPipelineForText2Image.from_pretrained(self.model, torch_dtype=torch.float16, variant="fp16")
            pipe.to("cuda")
        self.pipe = pipe

    def _load_adapter(self):
        if self.flux:
            self.pipe.load_ip_adapter(
                FLUX_FACE_ADAPTER[0], weight_name=FLUX_FACE_ADAPTER[1],
                image_encoder_pretrained_model_name_or_path=FLUX_FACE_ENCODER,
            )
            # Re-register offload hooks so the new encoder and adapter move with the rest.
            self.pipe.enable_model_cpu_offload()
        else:
            repo, sub, weight = SDXL_FACE_ADAPTER
            # The plus-face adapter uses the ViT-H encoder kept in the repo's models/ folder.
            self.pipe.load_ip_adapter(repo, subfolder=sub, weight_name=weight, image_encoder_folder=SDXL_FACE_ENCODER[1])
        self.adapter = True

    def generate(self, prompt, seed, out, refs=None, likeness=0.7, square=False):
        import torch

        if self.pipe is None:
            self._load()
        if refs and not self.adapter:
            self._load_adapter()
        w, h = self.size(square)
        kwargs = dict(
            prompt=prompt,
            width=w,
            height=h,
            num_inference_steps=self.steps,
            generator=torch.Generator("cpu").manual_seed(seed),
        )
        if self.turbo:
            kwargs["guidance_scale"] = 0.0
        elif self.flux:
            kwargs["guidance_scale"] = 0.0
        else:
            kwargs["negative_prompt"] = NEGATIVE
            kwargs["guidance_scale"] = 6.0
        if self.adapter:
            # Once loaded, every call must pass an image; scale 0 switches it off for shots without a character.
            faces = [Image.open(r).convert("RGB") for r in refs] if refs else [Image.new("RGB", (224, 224))]
            self.pipe.set_ip_adapter_scale(0.35 + 0.5 * likeness if refs else 0.0)
            kwargs["ip_adapter_image"] = [faces] if not self.flux else faces[0]
        image = self.pipe(**kwargs).images[0]
        out.parent.mkdir(parents=True, exist_ok=True)
        image.save(out)
        return out

    def unload(self) -> None:
        if self.pipe is not None:
            import gc

            import torch

            self.pipe = None
            self.adapter = False
            gc.collect()
            torch.cuda.empty_cache()
