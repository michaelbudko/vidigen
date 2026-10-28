"""Pluggable generation backends.

Image backends turn a prompt into a keyframe. Video backends turn a keyframe
(image-to-video) or a source clip (video-to-video) into a moving clip. Heavy
ML imports happen lazily inside the backends so the mock path needs only numpy,
Pillow and ffmpeg.
"""
from __future__ import annotations

from .image import ImageBackend, MockImageBackend, DiffusersImageBackend
from .video import VideoBackend, KenBurnsBackend, LTXVideoBackend, WanVideoBackend


def make_image_backend(name: str, model: str, width: int, height: int) -> ImageBackend:
    if name == "mock":
        return MockImageBackend(width, height)
    if name == "diffusers":
        return DiffusersImageBackend(model, width, height)
    raise ValueError(f"unknown image backend: {name}")


def make_video_backend(name: str, model: str, width: int, height: int, ffmpeg: str) -> VideoBackend:
    if name == "kenburns":
        return KenBurnsBackend(ffmpeg)
    if name == "ltx":
        return LTXVideoBackend(model, width, height)
    if name == "wan":
        return WanVideoBackend(model, width, height)
    raise ValueError(f"unknown video backend: {name}")


__all__ = [
    "ImageBackend",
    "VideoBackend",
    "make_image_backend",
    "make_video_backend",
]
