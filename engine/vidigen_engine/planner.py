"""Turns the song analysis and the user's prompt into a beat-synced shot list.

Cut points come from the music (downbeats, denser when the track is loud).
Per-shot prompts come from a local LLM through Ollama when one is running,
otherwise from a deterministic template expander so the app always works offline.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, asdict

import httpx

from .audio import Analysis, Window
from .config import Settings

MIN_SHOT = 1.2
MAX_SHOT = 5.0

MOTIONS = ["push_in", "pull_out", "pan_left", "pan_right", "tilt_up", "tilt_down", "orbit", "static"]

FRAMINGS = [
    "wide establishing shot",
    "medium shot",
    "extreme close-up",
    "low angle shot",
    "overhead shot",
    "silhouette against the light",
    "tracking shot",
    "dutch angle",
]

LIGHTING = [
    "golden hour light",
    "neon glow",
    "soft volumetric fog",
    "hard rim lighting",
    "moody blue hour",
    "strobing club lights",
    "candlelit warmth",
    "overcast diffuse light",
]


@dataclass
class Shot:
    index: int
    start: float  # seconds, relative to the clip window
    end: float
    prompt: str
    motion: str
    intensity: float  # 0..1 from the song's energy, drives motion speed

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration"] = round(self.duration, 3)
        return d


def cut_points(a: Analysis, w: Window) -> list[float]:
    """Cut times inside the window, in seconds relative to the window start."""
    grid = [b for b in a.beats if w.start <= b <= w.end] or [w.start]
    beat_len = 60.0 / a.tempo if a.tempo else 0.5
    cuts = [0.0]
    i = 0
    while True:
        last = cuts[-1]
        energy = a.energy_at(w.start + last)
        # Loud sections cut every bar, quiet sections every two bars.
        beats_per_shot = 4 if energy > 0.6 else 8
        target = last + beats_per_shot * beat_len
        target = min(max(target, last + MIN_SHOT), last + MAX_SHOT)
        # Snap to the nearest tracked beat so cuts land on the music.
        rel = [g - w.start for g in grid]
        snapped = min(rel, key=lambda r: abs(r - target)) if rel else target
        nxt = snapped if snapped - last >= MIN_SHOT else target
        if nxt >= w.duration - MIN_SHOT:
            break
        cuts.append(round(nxt, 3))
        i += 1
        if i > 500:
            break
    cuts.append(round(w.duration, 3))
    return cuts


def _template_prompts(user_prompt: str, style: str, n: int, seed: int) -> list[tuple[str, str]]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        framing = FRAMINGS[(i + rng.randrange(3)) % len(FRAMINGS)]
        light = rng.choice(LIGHTING)
        motion = rng.choice(MOTIONS)
        parts = [user_prompt.strip(), framing, light]
        if style:
            parts.append(style.strip())
        parts.append("cinematic, vertical composition, highly detailed")
        out.append((", ".join(p for p in parts if p), motion))
    return out


LLM_INSTRUCTIONS = """You are a music video director. Write a shot list for a vertical TikTok music video.
Concept: {prompt}
Visual style: {style}
Song tempo: {tempo:.0f} BPM. There are exactly {n} shots. Shot energies (0=calm, 1=intense): {energies}
Keep the same main subject and world across shots so the video feels continuous, but vary framing,
lighting and action. Match intensity to energy. Each prompt is one image-generation prompt under 60 words.
Respond with JSON only: {{"shots": [{{"prompt": "...", "motion": one of {motions}}}, ...]}}"""


def _llm_prompts(
    settings: Settings, user_prompt: str, style: str, tempo: float, energies: list[float]
) -> list[tuple[str, str]] | None:
    body = {
        "model": settings.llm_model,
        "prompt": LLM_INSTRUCTIONS.format(
            prompt=user_prompt,
            style=style or "director's choice",
            tempo=tempo,
            n=len(energies),
            energies=[round(e, 2) for e in energies],
            motions=MOTIONS,
        ),
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.8},
    }
    try:
        r = httpx.post(f"{settings.ollama_url}/api/generate", json=body, timeout=180)
        r.raise_for_status()
        shots = json.loads(r.json()["response"])["shots"]
    except Exception:
        return None
    out = []
    for s in shots[: len(energies)]:
        motion = s.get("motion") if s.get("motion") in MOTIONS else "push_in"
        prompt = str(s.get("prompt", "")).strip()
        if not prompt:
            return None
        out.append((f"{prompt}, {style}".strip(", ") if style else prompt, motion))
    return out if len(out) == len(energies) else None


def llm_available(settings: Settings) -> bool:
    try:
        r = httpx.get(f"{settings.ollama_url}/api/tags", timeout=2)
        names = {m["name"] for m in r.json().get("models", [])}
        return settings.llm_model in names or f"{settings.llm_model}:latest" in names
    except Exception:
        return False


def plan(
    a: Analysis,
    w: Window,
    user_prompt: str,
    style: str = "",
    settings: Settings | None = None,
    use_llm: bool = True,
    seed: int = 0,
) -> tuple[list[Shot], str]:
    """Returns the shots and which planner produced them ("llm" or "template")."""
    settings = settings or Settings()
    cuts = cut_points(a, w)
    spans = list(zip(cuts[:-1], cuts[1:]))
    energies = [a.energy_at(w.start + (s + e) / 2) for s, e in spans]

    prompts = None
    source = "template"
    if use_llm:
        prompts = _llm_prompts(settings, user_prompt, style, a.tempo, energies)
        if prompts:
            source = "llm"
    if not prompts:
        prompts = _template_prompts(user_prompt, style, len(spans), seed)

    shots = [
        Shot(index=i, start=s, end=e, prompt=p, motion=m, intensity=round(en, 3))
        for i, ((s, e), (p, m), en) in enumerate(zip(spans, prompts, energies))
    ]
    return shots, source
