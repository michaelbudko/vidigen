"""Pluggable generation backends.

Image backends turn a prompt (and optionally a character's reference faces)
into a keyframe. Video backends turn a keyframe (image-to-video) or a source
clip (video-to-video) into a moving clip. Heavy ML imports happen lazily inside
the backends so the mock path needs only numpy, Pillow and ffmpeg.
"""
from __future__ import annotations

from ..config import Preset
from .image import ImageBackend, MockImageBackend, DiffusersImageBackend
from .video import VideoBackend, KenBurnsBackend, LTXVideoBackend, WanVideoBackend

# Loading SDXL takes a while, so keep the last image backend around between
# character previews and renders. The pipeline unloads it before the video stage.
_image_cache: dict[tuple[str, str], ImageBackend] = {}


def image_backend(preset: Preset) -> ImageBackend:
    key = (preset.image_backend, preset.image_model)
    if key not in _image_cache:
        for other in _image_cache.values():
            other.unload()
        _image_cache.clear()
        if preset.image_backend == "mock":
            _image_cache[key] = MockImageBackend(preset.gen_width, preset.gen_height)
        elif preset.image_backend == "diffusers":
            _image_cache[key] = DiffusersImageBackend(preset.image_model, preset.image_steps)
        else:
            raise ValueError(f"unknown image backend: {preset.image_backend}")
    return _image_cache[key]


def make_video_backend(preset: Preset, ffmpeg: str) -> VideoBackend:
    if preset.video_backend == "kenburns":
        return KenBurnsBackend(ffmpeg)
    if preset.video_backend == "ltx":
        return LTXVideoBackend(preset.video_model, preset.gen_width, preset.gen_height)
    if preset.video_backend == "wan":
        return WanVideoBackend(preset.video_model, preset.gen_width, preset.gen_height)
    raise ValueError(f"unknown video backend: {preset.video_backend}")


__all__ = ["ImageBackend", "VideoBackend", "image_backend", "make_video_backend"]
