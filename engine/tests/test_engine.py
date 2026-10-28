import json
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from vidigen_engine import audio, planner, pipeline
from vidigen_engine.config import Settings, preset_for_vram
from vidigen_engine.licensing import SongRights


def click_track(path: Path, bpm: float, seconds: float, sr: int = 44100) -> Path:
    """Kick-like clicks on every beat over a quiet pad, louder in the second half."""
    t = np.arange(int(seconds * sr)) / sr
    y = 0.02 * np.sin(2 * np.pi * 220 * t)
    period = 60 / bpm
    for k, start in enumerate(np.arange(0, seconds, period)):
        i = int(start * sr)
        n = min(int(0.08 * sr), len(y) - i)
        gain = 0.9 if start > seconds / 2 else 0.4
        if k % 4 == 0:
            gain *= 1.3
        y[i : i + n] += gain * np.sin(2 * np.pi * 60 * t[:n]) * np.exp(-t[:n] * 40)
    pcm = (np.clip(y, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return path


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def song(tmp_path):
    return click_track(tmp_path / "song.wav", bpm=120, seconds=70)


def test_tempo_and_beats(song, settings):
    a = audio.analyze(song, settings)
    assert abs(a.tempo - 120) < 3
    gaps = np.diff(a.beats)
    assert abs(np.median(gaps) - 0.5) < 0.03
    assert len(a.downbeats) >= len(a.beats) // 4 - 1


def test_window_prefers_loud_section(song, settings):
    a = audio.analyze(song, settings)
    w = audio.choose_window(a, 30)
    assert abs(w.duration - 30) < 0.6
    assert w.start >= 30  # the second half is louder
    assert w.end <= a.duration


def test_cut_points_cover_window(song, settings):
    a = audio.analyze(song, settings)
    w = audio.choose_window(a, 30)
    cuts = planner.cut_points(a, w)
    assert cuts[0] == 0 and abs(cuts[-1] - w.duration) < 1e-6
    lengths = np.diff(cuts)
    assert lengths.min() >= planner.MIN_SHOT - 1e-6
    assert lengths.max() <= planner.MAX_SHOT + planner.MIN_SHOT


def test_template_planner_offline(song, settings):
    settings.ollama_url = "http://127.0.0.1:9"  # nothing listens here
    a = audio.analyze(song, settings)
    w = audio.choose_window(a, 30)
    shots, source = planner.plan(a, w, "a neon fox running through Tokyo", "anime", settings)
    assert source == "template"
    assert all("neon fox" in s.prompt and "anime" in s.prompt for s in shots)


def test_rights_are_optional_and_credit_when_given():
    assert SongRights().credit_line() == ""
    assert SongRights(license="cc_by").missing_credit
    r = SongRights(license="cc_by", title="Song", artist="Someone")
    assert not r.missing_credit and "CC BY" in r.credit_line()


def test_prompt_is_the_only_required_field():
    with pytest.raises(ValidationError):
        pipeline.RenderRequest()
    req = pipeline.RenderRequest(prompt="a fox in the snow")
    assert req.song_path is None and req.bpm == 120


def test_preset_by_vram():
    assert preset_for_vram(0).name == "mock"
    assert preset_for_vram(8).name == "lite"
    assert preset_for_vram(12).name == "balanced"
    assert preset_for_vram(24).name == "quality"


def test_mock_render_end_to_end(song, settings):
    settings.ollama_url = "http://127.0.0.1:9"
    req = pipeline.RenderRequest(
        song_path=str(song),
        prompt="a paper boat sailing through a storm",
        duration=30,
        preset="mock",
        rights=SongRights(license="own_work", attested=True),
    )
    job = pipeline.run(pipeline.new_job(req, settings), settings)
    assert job.status == "done", job.error
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
         "-of", "json", job.output],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(out.stdout)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1920)
    assert any(s["codec_type"] == "audio" for s in info["streams"])
    assert abs(float(info["format"]["duration"]) - 30) < 0.6
    assert Path(job.thumbnail).is_file()


def test_render_without_song_is_silent_and_beat_cut(settings):
    settings.ollama_url = "http://127.0.0.1:9"
    req = pipeline.RenderRequest(prompt="a lighthouse in fog", duration=30, bpm=100)
    job = pipeline.run(pipeline.new_job(req, settings), settings)
    assert job.status == "done", job.error
    beat = 60 / 100
    for s in job.shots[:-1]:
        assert abs(s["end"] / beat - round(s["end"] / beat)) < 1e-3  # cuts land on the grid
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", job.output],
        capture_output=True, text=True, check=True,
    )
    assert abs(float(probe.stdout) - 30) < 0.6
    assert "Music:" not in job.caption
