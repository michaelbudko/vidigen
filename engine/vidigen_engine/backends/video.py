from __future__ import annotations

import math
from pathlib import Path

from .. import ffmpeg
from ..config import OUT_WIDTH, OUT_HEIGHT, OUT_FPS

MOTION_HINTS = {
    "push_in": "slow camera push in",
    "pull_out": "camera pulls back",
    "pan_left": "camera pans left",
    "pan_right": "camera pans right",
    "tilt_up": "camera tilts up",
    "tilt_down": "camera tilts down",
    "orbit": "camera orbits around the subject",
    "static": "locked-off camera, subject moves",
}


class VideoBackend:
    # Longest clip the model produces in one go; the composer stretches or holds beyond it.
    max_seconds: float = 30.0

    def animate(
        self, image: Path, prompt: str, motion: str, intensity: float, duration: float, seed: int, out: Path
    ) -> Path:
        raise NotImplementedError

    def restyle(self, source: Path, prompt: str, duration: float, seed: int, out: Path) -> Path:
        """Video-to-video: re-render a source clip in the prompt's style."""
        raise NotImplementedError(f"{type(self).__name__} does not support video-to-video")

    def unload(self) -> None:
        pass


class KenBurnsBackend(VideoBackend):
    """Pan/zoom over a still with ffmpeg. Works on any machine."""

    def __init__(self, ffmpeg_bin: str):
        self.ffmpeg = ffmpeg_bin

    def _expr(self, motion: str, intensity: float, n: int) -> tuple[str, str, str]:
        amt = 0.08 + 0.17 * max(0.0, min(1.0, intensity))
        p = f"(on/{max(n - 1, 1)})"
        cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
        z_hold = f"{1 + amt:.4f}"
        if motion == "pull_out":
            return f"{1 + amt:.4f}-{amt:.4f}*{p}", cx, cy
        if motion == "pan_left":
            return z_hold, f"(iw-iw/zoom)*(1-{p})", cy
        if motion == "pan_right":
            return z_hold, f"(iw-iw/zoom)*{p}", cy
        if motion == "tilt_up":
            return z_hold, cx, f"(ih-ih/zoom)*(1-{p})"
        if motion == "tilt_down":
            return z_hold, cx, f"(ih-ih/zoom)*{p}"
        if motion == "orbit":
            return z_hold, f"(iw-iw/zoom)*(0.5+0.5*sin(2*PI*{p}))", f"(ih-ih/zoom)*(0.5+0.5*cos(2*PI*{p}))"
        if motion == "static":
            return f"1+{amt / 4:.4f}*{p}", cx, cy
        return f"1+{amt:.4f}*{p}", cx, cy  # push_in

    def animate(self, image, prompt, motion, intensity, duration, seed, out):
        n = max(2, math.ceil(duration * OUT_FPS))
        z, x, y = self._expr(motion, intensity, n)
        # Upscale first so sub-pixel zoom steps do not jitter.
        vf = (
            f"scale={OUT_WIDTH * 2}:{OUT_HEIGHT * 2}:force_original_aspect_ratio=increase,"
            f"crop={OUT_WIDTH * 2}:{OUT_HEIGHT * 2},"
            f"zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={OUT_WIDTH}x{OUT_HEIGHT}:fps={OUT_FPS},"
            f"format=yuv420p"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg.run([
            self.ffmpeg, "-y", "-v", "error", "-loop", "1", "-i", str(image),
            "-vf", vf, "-frames:v", str(n), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(out),
        ])
        return out

    def restyle(self, source, prompt, duration, seed, out):
        # Without a model, keep the source footage and give it a consistent grade.
        out.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg.run([
            self.ffmpeg, "-y", "-v", "error", "-i", str(source), "-t", f"{duration:.3f}",
            "-vf", "eq=contrast=1.1:saturation=1.25,format=yuv420p", "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(out),
        ])
        return out


class _DiffusersVideo(VideoBackend):
    fps = 24
    frame_multiple = 8  # these models want 8k+1 or 4k+1 frames

    def __init__(self, model: str, width: int, height: int):
        self.model, self.width, self.height = model, width, height
        self.pipe = None

    def _frames_for(self, duration: float) -> int:
        n = int(min(duration, self.max_seconds) * self.fps)
        return (n // self.frame_multiple) * self.frame_multiple + 1

    def _export(self, frames, out: Path) -> Path:
        from diffusers.utils import export_to_video

        out.parent.mkdir(parents=True, exist_ok=True)
        export_to_video(frames, str(out), fps=self.fps)
        return out

    def unload(self) -> None:
        if self.pipe is not None:
            import torch

            self.pipe = None
            torch.cuda.empty_cache()


class LTXVideoBackend(_DiffusersVideo):
    """LTX-Video. One pipeline covers image-to-video and video-to-video conditioning."""

    max_seconds = 5.0
    fps = 24
    frame_multiple = 8

    def _load(self):
        import torch
        from diffusers import LTXConditionPipeline

        self.pipe = LTXConditionPipeline.from_pretrained(self.model, torch_dtype=torch.bfloat16)
        self.pipe.enable_model_cpu_offload()
        self.pipe.vae.enable_tiling()

    def _run(self, condition, prompt: str, duration: float, seed: int):
        import torch

        if self.pipe is None:
            self._load()
        distilled = "distilled" in self.model.lower()
        return self.pipe(
            conditions=[condition],
            prompt=prompt,
            negative_prompt="worst quality, blurry, jittery, distorted, watermark, text",
            width=self.width,
            height=self.height,
            num_frames=self._frames_for(duration),
            num_inference_steps=8 if distilled else 30,
            guidance_scale=1.0 if distilled else 3.0,
            generator=torch.Generator("cpu").manual_seed(seed),
        ).frames[0]

    def animate(self, image, prompt, motion, intensity, duration, seed, out):
        from diffusers.pipelines.ltx.pipeline_ltx_condition import LTXVideoCondition
        from diffusers.utils import load_image

        cond = LTXVideoCondition(image=load_image(str(image)), frame_index=0)
        frames = self._run(cond, f"{prompt}, {MOTION_HINTS.get(motion, '')}", duration, seed)
        return self._export(frames, out)

    def restyle(self, source, prompt, duration, seed, out):
        from diffusers.pipelines.ltx.pipeline_ltx_condition import LTXVideoCondition
        from diffusers.utils import load_video

        video = load_video(str(source))[: self._frames_for(duration)]
        cond = LTXVideoCondition(video=video, frame_index=0, strength=0.6)
        return self._export(self._run(cond, prompt, duration, seed), out)


class WanVideoBackend(_DiffusersVideo):
    """Wan 2.x image-to-video."""

    max_seconds = 5.0
    fps = 24
    frame_multiple = 4

    def _load(self):
        import torch
        from diffusers import AutoencoderKLWan, WanImageToVideoPipeline

        vae = AutoencoderKLWan.from_pretrained(self.model, subfolder="vae", torch_dtype=torch.float32)
        self.pipe = WanImageToVideoPipeline.from_pretrained(self.model, vae=vae, torch_dtype=torch.bfloat16)
        self.pipe.enable_model_cpu_offload()

    def animate(self, image, prompt, motion, intensity, duration, seed, out):
        import torch
        from diffusers.utils import load_image

        if self.pipe is None:
            self._load()
        frames = self.pipe(
            image=load_image(str(image)),
            prompt=f"{prompt}, {MOTION_HINTS.get(motion, '')}",
            negative_prompt="blurry, distorted, static, watermark, text, low quality",
            width=self.width,
            height=self.height,
            num_frames=self._frames_for(duration),
            guidance_scale=5.0,
            num_inference_steps=30,
            generator=torch.Generator("cpu").manual_seed(seed),
        ).frames[0]
        return self._export(frames, out)
