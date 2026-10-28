"""Beat, tempo and energy analysis using only numpy.

The beat tracker follows Ellis (2007), "Beat Tracking by Dynamic Programming":
an onset-strength envelope from spectral flux, a global tempo estimate from its
autocorrelation, then dynamic programming to place beats that agree with both.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np

from . import ffmpeg
from .config import Settings, MIN_DURATION, MAX_DURATION

SR = 22050
HOP = 512
N_FFT = 2048


@dataclass
class Analysis:
    duration: float
    tempo: float
    beats: list[float]
    downbeats: list[float]
    # Loudness per second, normalised to 0..1, for the energy graph and shot pacing.
    energy: list[float]

    def to_dict(self) -> dict:
        return asdict(self)

    def energy_at(self, t: float) -> float:
        if not self.energy:
            return 0.5
        i = int(np.clip(t, 0, len(self.energy) - 1))
        return self.energy[i]


@dataclass
class Window:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def _frames(y: np.ndarray) -> np.ndarray:
    if len(y) < N_FFT:
        y = np.pad(y, (0, N_FFT - len(y)))
    n = 1 + (len(y) - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    return y[idx] * np.hanning(N_FFT)[None, :]


def onset_envelope(y: np.ndarray) -> np.ndarray:
    frames = _frames(y)
    out = []
    # Chunk the FFT so a long song does not allocate one huge matrix.
    for i in range(0, len(frames), 2048):
        out.append(np.abs(np.fft.rfft(frames[i : i + 2048], axis=1)))
    mag = np.log1p(100.0 * np.concatenate(out))
    flux = np.maximum(0.0, np.diff(mag, axis=0)).sum(axis=1)
    flux = np.concatenate([[0.0], flux])
    # Remove slow trends so quiet and loud passages both produce peaks.
    k = max(1, int(SR / HOP))
    trend = np.convolve(flux, np.ones(k) / k, mode="same")
    env = np.maximum(0.0, flux - trend)
    sd = env.std()
    return env / sd if sd > 0 else env


def estimate_tempo(env: np.ndarray, lo_bpm: float = 70, hi_bpm: float = 180) -> float:
    fps = SR / HOP
    env = env - env.mean()
    ac = np.correlate(env, env, mode="full")[len(env) - 1 :]
    lags = np.arange(len(ac))
    min_lag = int(fps * 60 / hi_bpm)
    max_lag = min(len(ac) - 1, int(fps * 60 / lo_bpm))
    if max_lag <= min_lag:
        return 120.0
    bpm = 60 * fps / np.maximum(lags[min_lag : max_lag + 1], 1)
    # Prefer tempos near 120 BPM, as listeners do, to resolve half/double ambiguity.
    weight = np.exp(-0.5 * (np.log2(bpm / 120.0) / 0.9) ** 2)
    best = min_lag + int(np.argmax(ac[min_lag : max_lag + 1] * weight))
    return float(60 * fps / best)


def track_beats(env: np.ndarray, tempo: float, tightness: float = 100.0) -> np.ndarray:
    fps = SR / HOP
    period = 60 * fps / tempo
    n = len(env)
    score = env.astype(np.float64).copy()
    back = -np.ones(n, dtype=np.int64)
    lo, hi = int(round(period / 2)), int(round(2 * period))
    offsets = np.arange(-hi, -lo + 1)
    penalty = -tightness * np.log(-offsets / period) ** 2
    for t in range(n):
        prev = t + offsets
        ok = prev >= 0
        if not ok.any():
            continue
        cand = score[prev[ok]] + penalty[ok]
        j = int(np.argmax(cand))
        if cand[j] > 0:
            score[t] = env[t] + cand[j]
            back[t] = prev[ok][j]
    # Start from the strongest score within the last beat period.
    tail = max(0, n - int(period))
    t = tail + int(np.argmax(score[tail:]))
    beats = []
    while t >= 0:
        beats.append(t)
        t = back[t]
    return np.array(beats[::-1]) / fps


def pick_downbeats(beats: np.ndarray, env: np.ndarray, per_bar: int = 4) -> np.ndarray:
    if len(beats) < per_bar:
        return beats
    fps = SR / HOP
    idx = np.clip((beats * fps).astype(int), 0, len(env) - 1)
    strength = env[idx]
    phase = max(range(per_bar), key=lambda p: strength[p::per_bar].mean())
    return beats[phase::per_bar]


def energy_curve(y: np.ndarray) -> np.ndarray:
    n = int(np.ceil(len(y) / SR))
    rms = np.array([np.sqrt(np.mean(y[i * SR : (i + 1) * SR] ** 2) + 1e-12) for i in range(n)])
    if len(rms) == 0:
        return rms
    db = 20 * np.log10(rms)
    lo, hi = np.percentile(db, 5), np.percentile(db, 98)
    return np.clip((db - lo) / max(hi - lo, 1e-6), 0, 1)


def analyze(path: Path, settings: Settings | None = None) -> Analysis:
    settings = settings or Settings()
    y = ffmpeg.decode_mono(settings.ffmpeg, path, SR)
    duration = len(y) / SR
    env = onset_envelope(y)
    tempo = estimate_tempo(env)
    beats = track_beats(env, tempo)
    downbeats = pick_downbeats(beats, env)
    energy = energy_curve(y)
    return Analysis(
        duration=round(duration, 3),
        tempo=round(tempo, 2),
        beats=[round(float(b), 3) for b in beats],
        downbeats=[round(float(b), 3) for b in downbeats],
        energy=[round(float(e), 3) for e in energy],
    )


def beat_grid(duration: float, bpm: float) -> Analysis:
    """A synthetic analysis for renders without a song: steady beats at `bpm`,
    a calmer first quarter, then full energy, so pacing still builds."""
    bpm = float(np.clip(bpm, 60, 200))
    beats = np.arange(0, duration + 1e-6, 60.0 / bpm)
    secs = int(np.ceil(duration)) + 1
    energy = [0.45 if i < duration / 4 else 0.8 for i in range(secs)]
    return Analysis(
        duration=round(duration, 3),
        tempo=bpm,
        beats=[round(float(b), 3) for b in beats],
        downbeats=[round(float(b), 3) for b in beats[::4]],
        energy=energy,
    )


def choose_window(a: Analysis, target: float, start: float | None = None) -> Window:
    """Pick the clip of the song to use.

    With an explicit start, snap it to the nearest downbeat. Otherwise pick the
    most energetic stretch (usually the chorus/drop), starting on a downbeat.
    """
    target = float(np.clip(target, MIN_DURATION, MAX_DURATION))
    target = min(target, a.duration)
    latest = max(0.0, a.duration - target)
    anchors = [d for d in a.downbeats if d <= latest] or [0.0]

    if start is not None:
        s = min(anchors, key=lambda d: abs(d - start))
    else:
        e = np.array(a.energy) if a.energy else np.ones(int(a.duration) + 1)
        span = max(1, int(target))

        def mean_energy(s: float) -> float:
            i = int(s)
            return float(e[i : i + span].mean()) if i < len(e) else 0.0

        s = max(anchors, key=mean_energy)
    return Window(start=round(s, 3), end=round(min(a.duration, s + target), 3))
