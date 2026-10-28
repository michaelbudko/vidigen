"""Phonk edits: library, online sources (mocked), edit builder and autopilot."""
import json
import subprocess
import time
import wave
from datetime import datetime
from pathlib import Path

import httpx
import numpy as np
import pytest

from vidigen_engine import audio, autoedit, sources
from vidigen_engine.autopilot import Autopilot, AutopilotConfig
from vidigen_engine.config import Settings
from vidigen_engine.library import Library


def phonk_track(path: Path, bpm: float = 140, seconds: float = 50, drop: float = 18.0, sr: int = 22050) -> Path:
    """Quiet kicks, then a loud drop with an off-beat cowbell."""
    t = np.arange(int(seconds * sr)) / sr
    y = 0.02 * np.sin(2 * np.pi * 110 * t)
    for k, st in enumerate(np.arange(0, seconds, 60 / bpm)):
        i = int(st * sr)
        n = min(int(0.09 * sr), len(y) - i)
        g = (0.15 if st < drop else 0.9) * (1.3 if k % 4 == 0 else 1.0)
        y[i : i + n] += g * np.sin(2 * np.pi * 55 * t[:n]) * np.exp(-t[:n] * 30)
        if st >= drop and k % 2:
            y[i : i + n] += 0.4 * np.sin(2 * np.pi * 800 * t[:n]) * np.exp(-t[:n] * 20)
    pcm = (np.clip(y, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return path


def clip_file(path: Path, pattern: str = "testsrc2", seconds: float = 5, size: str = "320x568") -> Path:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"{pattern}=s={size}:r=30", "-t", str(seconds),
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def still_then_moving(path: Path) -> Path:
    """4 s of a still frame, then 4 s of motion."""
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=320x568:r=30:d=4",
        "-f", "lavfi", "-i", "life=s=320x568:r=30:mold=10:ratio=0.2", "-filter_complex",
        "[1:v]trim=duration=4,format=yuv420p[m];[0:v]format=yuv420p[s];[s][m]concat=n=2:v=1[v]", "-map", "[v]",
        "-c:v", "libx264", "-preset", "ultrafast", str(path)], check=True)
    return path


def probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    return {
        "track": phonk_track(d / "track.wav"),
        "track2": phonk_track(d / "track2.wav", bpm=150, drop=14.0, seconds=40),
        "clips": [clip_file(d / f"c{i}.mp4", p) for i, p in enumerate(["testsrc2", "testsrc", "rgbtestsrc"])],
        "mixed": still_then_moving(d / "mixed.mp4"),
    }


@pytest.fixture
def lib(tmp_path):
    return Library(Settings(data_dir=tmp_path / "data"))


@pytest.fixture
def stocked(lib, media):
    lib.add_track(media["track"], source="local", source_id="t1", title="Night Drift", artist="DJ Test",
                  license="cc-by", page_url="https://example.com/t1")
    for i, c in enumerate(media["clips"]):
        lib.add_clip(c, source="local", source_id=f"c{i}", category="drift", author="Cam")
    return lib


def test_drop_is_found(media, lib):
    a = audio.analyze(media["track"], lib.settings)
    assert 132 < a.tempo < 150 or 66 < a.tempo < 75
    assert abs(audio.find_drop(a) - 18.0) < 1.0


def test_motion_profile_finds_the_action(lib, media):
    c = lib.add_clip(media["mixed"], source="local", source_id="m", category="racing")
    assert c.width == 320 and c.height == 568
    assert abs(c.duration - 8) < 0.2
    first, second = np.mean(c.motion[:6]), np.mean(c.motion[10:])
    assert second > first + 0.1
    assert c.best_window(2.0) >= 3.5
    assert autoedit.window_options(c, 2.0)[0] >= 3.5
    assert Path(c.thumb).is_file()


def test_folder_import_skips_duplicates_and_keeps_files(lib, media, tmp_path):
    folder = tmp_path / "pack"
    folder.mkdir()
    (folder / "a.wav").write_bytes(media["track"].read_bytes())
    (folder / "short.wav").write_bytes(b"not audio")
    r = lib.import_folder(folder, "music", license="ncs")
    assert r["added"] == 1 and len(r["errors"]) == 1
    assert lib.import_folder(folder, "music")["skipped"] == 1
    t = lib.tracks()[0]
    assert t.license == "ncs" and t.title == "a"
    lib.remove("track", t.id)
    assert (folder / "a.wav").is_file()  # imported files are never deleted
    assert lib.stats()["tracks"] == 0


def test_plan_cuts_on_the_drop(stocked):
    t = stocked.tracks()[0]
    import random

    req = autoedit.EditRequest(duration=20, seed=3)
    window, drop = autoedit.place_window(t, 20)
    assert 0 < drop < 8 and abs((window.start + drop) - t.drop_at) < 0.01
    shots = autoedit.plan_shots(t, window, drop, req, random.Random(3))
    assert shots[0].start == 0 and abs(sum(s.duration for s in shots) - 20) < 1e-6
    assert min(s.duration for s in shots) >= autoedit.MIN_SHOT - 1e-6
    hit = [s for s in shots if abs(s.start - drop) < 0.06]
    assert hit and {"flash", "punch"} <= set(hit[0].fx)
    before = [s.duration for s in shots if s.start < drop - 0.05]
    after = [s.duration for s in shots if s.start >= drop]
    assert np.mean(before) > np.mean(after)
    assert all(s.speed == autoedit.SLOWMO for s in shots if s.start < drop - 0.05)


def test_insane_cuts_faster_than_chill(stocked):
    import random

    t = stocked.tracks()[0]
    window, drop = autoedit.place_window(t, 20)
    n = {i: len(autoedit.plan_shots(t, window, drop, autoedit.EditRequest(intensity=i), random.Random(1)))
         for i in ("chill", "hard", "insane")}
    assert n["chill"] < n["hard"] < n["insane"]


def test_build_edit(stocked):
    store = autoedit.EditStore(stocked.settings)
    e = store.new(autoedit.EditRequest(duration=10, hook="when the cowbell hits", look="teal", seed=7))
    autoedit.build(e, stocked)
    assert e.status == "done", e.error
    info = probe(Path(e.output))
    kinds = {s["codec_type"] for s in info["streams"]}
    assert kinds == {"video", "audio"}
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1080, 1920)
    assert abs(float(info["format"]["duration"]) - 10) < 0.15
    assert "Night Drift" in e.caption and "#phonk" in e.caption and "when the cowbell hits" in e.caption
    assert (e.dir / "caption.txt").is_file() and Path(e.thumbnail).is_file()
    assert not (e.dir / "shots").exists()
    t = stocked.tracks()[0]
    assert t.used == 1
    listed = store.list()
    assert listed[0]["id"] == e.id and listed[0]["status"] == "done"
    assert store.file(e.id, "video") == Path(e.output)
    store.delete(e.id)
    assert store.list() == []


def test_edit_without_media_explains(lib):
    e = autoedit.EditStore(lib.settings).new(autoedit.EditRequest())
    autoedit.build(e, lib)
    assert e.status == "failed" and "Library" in e.error


def test_nc_tracks_can_be_excluded(lib, media):
    lib.add_track(media["track"], source="local", source_id="nc", title="NC", license="cc-by-nc-sa")
    lib.add_clip(media["clips"][0], source="local", source_id="c", category="fight")
    e = autoedit.EditStore(lib.settings).new(autoedit.EditRequest(allow_nc=False, duration=10))
    autoedit.build(e, lib)
    assert e.status == "failed" and "No tracks" in e.error


# ---- online sources, with the APIs mocked ------------------------------------------------

def fake_api(media, seen: list):
    clip_bytes = media["clips"][0].read_bytes()
    track_bytes = media["track"].read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        host, path, q = request.url.host, request.url.path, request.url.params
        if host == "api.pexels.com":
            if request.headers.get("Authorization") != "PEX":
                return httpx.Response(401, json={"error": "bad key"})
            page = int(q["page"])
            videos = [{
                "id": page * 100 + i, "duration": 8 if i != 2 else 120, "url": f"https://www.pexels.com/video/{i}/",
                "user": {"name": "Pex Author"},
                "video_files": [
                    {"file_type": "video/mp4", "width": 640, "height": 360, "link": "https://files.pexels.test/small.mp4"},
                    {"file_type": "video/mp4", "width": 2560, "height": 1440, "link": f"https://files.pexels.test/{i}.mp4"},
                    {"file_type": "video/mp4", "width": 3840, "height": 2160, "link": "https://files.pexels.test/4k.mp4"},
                ],
            } for i in range(3)]
            return httpx.Response(200, json={"videos": videos, "next_page": "x" if page < 2 else None})
        if host == "pixabay.com":
            hits = [{"id": 900 + i, "duration": 10, "pageURL": f"https://pixabay.com/videos/{i}/", "user": "pixuser",
                     "tags": "car, drift", "videos": {
                         "large": {"url": "", "width": 0, "height": 0},
                         "medium": {"url": f"https://cdn.pixabay.test/{i}.mp4", "width": 1920, "height": 1080},
                     }} for i in range(2)]
            return httpx.Response(200, json={"totalHits": 2, "hits": hits})
        if host == "api.jamendo.com":
            results = [
                {"id": "1", "name": "Cowbell Rush", "artist_name": "A", "duration": 50, "audiodownload_allowed": True,
                 "audiodownload": "https://dl.jamendo.test/1.mp3", "audio": "https://stream.jamendo.test/1.mp3",
                 "license_ccurl": "http://creativecommons.org/licenses/by-sa/3.0/", "shareurl": "https://jamen.do/t/1"},
                {"id": "2", "name": "No Remix", "artist_name": "B", "duration": 50, "audiodownload_allowed": True,
                 "audiodownload": "https://dl.jamendo.test/2.mp3",
                 "license_ccurl": "http://creativecommons.org/licenses/by-nc-nd/3.0/", "shareurl": "https://jamen.do/t/2"},
                {"id": "3", "name": "Drift Night", "artist_name": "C", "duration": 50, "audiodownload_allowed": False,
                 "audio": "https://stream.jamendo.test/3.mp3",
                 "license_ccurl": "http://creativecommons.org/licenses/by-nc-sa/3.0/", "shareurl": "https://jamen.do/t/3"},
            ]
            return httpx.Response(200, json={"headers": {"status": "success"}, "results": results})
        if path.endswith(".mp4"):
            return httpx.Response(200, content=clip_bytes)
        if path.endswith(".mp3"):
            return httpx.Response(200, content=track_bytes)
        return httpx.Response(404)

    return handler


def test_pick_file_prefers_sharp_9x16():
    files = [{"link": "a", "width": 1920, "height": 1080}, {"link": "b", "width": 2560, "height": 1440},
             {"link": "c", "width": 3840, "height": 2160}, {"link": "d", "width": 1080, "height": 1920}]
    assert sources.pick_file(files)["link"] == "d"
    assert sources.pick_file(files[:3])["link"] == "b"
    assert sources.pick_file(files[:1])["link"] == "a"
    assert sources.license_name("http://creativecommons.org/licenses/by-nc-sa/3.0/") == "cc-by-nc-sa"
    assert sources.license_name("https://creativecommons.org/publicdomain/zero/1.0/") == "cc0"


def test_refresh_downloads_and_dedupes(lib, media):
    sources.save_keys(lib.settings, {"pexels": "PEX", "pixabay": "PIX", "jamendo": "JAM"})
    seen: list = []
    client = sources.make_client(httpx.MockTransport(fake_api(media, seen)))
    r = sources.refresh(lib, clips=4, tracks=5, categories=["drift", "fight"], client=client, seed=1)
    assert r.clips_added == 4, r.errors
    # The ND track is skipped; the streamed one is used when download is not allowed.
    assert r.tracks_added == 2
    titles = {t.title: t for t in lib.tracks()}
    assert set(titles) == {"Cowbell Rush", "Drift Night"}
    assert titles["Drift Night"].non_commercial and not titles["Cowbell Rush"].non_commercial
    assert "CC-BY-SA" in titles["Cowbell Rush"].credit()
    clip_urls = {str(q.url) for q in seen if q.url.path.endswith(".mp4")}
    assert "https://files.pexels.test/4k.mp4" not in clip_urls  # the 1440p file crops sharp enough
    assert "https://files.pexels.test/2.mp4" not in clip_urls  # 120 s clip skipped
    stats = lib.stats()
    assert stats["clips"] == 4 and set(stats["per_category"]) == {"drift", "fight"}
    assert all(c.author in ("Pex Author", "pixuser") for c in lib.clips())

    seen.clear()
    r2 = sources.refresh(lib, clips=50, tracks=5, categories=["drift"], client=client, seed=2)
    ids = [(r["source"], r["source_id"]) for r in lib._q("select source, source_id from clips")]
    assert len(ids) == len(set(ids))
    assert r2.tracks_added == 0
    # Pexels pages 1-2 hold 4 usable clips, Pixabay 2: every one ends up in the library once.
    assert lib.stats()["clips"] == 6


def test_refresh_reports_bad_keys(lib, media):
    sources.save_keys(lib.settings, {"pexels": "WRONG"})
    client = sources.make_client(httpx.MockTransport(fake_api(media, [])))
    r = sources.refresh(lib, clips=2, tracks=1, categories=["moto"], client=client)
    assert r.clips_added == 0
    assert any("key rejected" in e for e in r.errors)
    assert any("Jamendo" in e for e in r.errors)
    assert sources.test_keys(lib.settings, client)["pexels"] == {"ok": False, "error": "key rejected"}


def test_keys_are_masked(lib):
    sources.save_keys(lib.settings, {"pexels": "abcdefghij1234"})
    m = sources.masked(sources.load_keys(lib.settings))
    assert m["pexels"] == {"set": True, "hint": "…1234"} and not m["jamendo"]["set"]


# ---- autopilot ------------------------------------------------------------------------------

def test_autopilot_schedule_and_run(stocked):
    import threading

    store = autoedit.EditStore(stocked.settings)

    def submit(req):
        return autoedit.build(store.new(req), stocked)

    ap = Autopilot(stocked.settings, stocked, submit, threading.Lock())
    ap.set_config(AutopilotConfig(enabled=True, per_day=2, at="09:00", duration=8, refresh=False,
                                  hooks=["pov: 3am drift"]))
    assert not ap.due(datetime(2026, 1, 5, 8, 59))
    assert ap.due(datetime(2026, 1, 5, 9, 1))
    res = ap.run()
    assert len(res["edits"]) == 2 and res["failed"] == 0
    today = datetime.now()
    assert not ap.due(today.replace(hour=23, minute=59))
    assert ap.next_run(today).startswith((today.date().fromordinal(today.toordinal() + 1)).isoformat())
    made = store.list()
    assert len(made) == 2 and all(e["request"]["auto"] for e in made)
    assert all("pov: 3am drift" in e["caption"] for e in made)
    # The settings and the last run survive a restart.
    again = Autopilot(stocked.settings, stocked, submit, threading.Lock())
    assert again.config.per_day == 2 and again.last_day == today.strftime("%Y-%m-%d")


def test_api_edits_and_library(tmp_path, monkeypatch, media):
    import importlib

    monkeypatch.setenv("VIDIGEN_DATA_DIR", str(tmp_path / "api"))
    monkeypatch.setenv("VIDIGEN_TOKEN", "tok")
    from fastapi.testclient import TestClient
    from vidigen_engine import server

    importlib.reload(server)
    c = TestClient(server.app, headers={"x-vidigen-token": "tok"})
    overview = c.get("/library").json()
    assert overview["stats"]["clips"] == 0 and "drift" in overview["categories"]
    assert not overview["keys"]["pexels"]["set"]

    assert c.put("/keys", json={"pexels": "  abcdefgh9999 "}).json()["pexels"]["hint"] == "…9999"

    music, footage = tmp_path / "m", tmp_path / "f"
    music.mkdir()
    footage.mkdir()
    (music / "song.wav").write_bytes(media["track"].read_bytes())
    for i, p in enumerate(media["clips"]):
        (footage / f"{i}.mp4").write_bytes(p.read_bytes())
    assert c.post("/library/import", json={"folder": str(music), "kind": "nope"}).status_code == 422

    def wait_task(t):
        for _ in range(200):
            t = c.get(f"/tasks/{t['id']}").json()
            if t["status"] != "running":
                return t
            time.sleep(0.1)
        raise AssertionError("task did not finish")

    t = wait_task(c.post("/library/import", json={"folder": str(music), "kind": "music", "license": "ncs"}).json())
    assert t["status"] == "done" and t["result"]["added"] == 1
    t = wait_task(c.post("/library/import", json={"folder": str(footage), "kind": "footage", "category": "fight"}).json())
    assert t["result"]["added"] == 3
    tracks = c.get("/library/tracks").json()
    assert tracks[0]["license"] == "ncs"
    clip = c.get("/library/clips", params={"category": "fight"}).json()[0]
    assert c.get(f"/library/clip/{clip['id']}/file", params={"what": "thumb"}).status_code == 200

    assert c.post("/edits", json={"categories": ["nope"]}).status_code == 400
    e = c.post("/edits", json={"duration": 8, "categories": ["fight"]}).json()
    for _ in range(600):
        e = c.get(f"/edits/{e['id']}").json()
        if e["status"] not in ("queued", "running"):
            break
        time.sleep(0.1)
    assert e["status"] == "done", e["error"]
    assert c.get(f"/edits/{e['id']}/files/video").status_code == 200
    assert c.get("/edits").json()[0]["id"] == e["id"]

    ap = c.get("/autopilot").json()
    assert ap["config"]["enabled"] is False and ap["next_run"] is None
    ap = c.put("/autopilot", json={**ap["config"], "enabled": True, "at": "07:30"}).json()
    assert ap["config"]["at"] == "07:30" and ap["next_run"]
    assert c.put("/autopilot", json={**ap["config"], "at": "7:30pm"}).status_code == 422

    assert c.delete(f"/edits/{e['id']}").json() == {"ok": True}
    assert c.delete(f"/library/track/{tracks[0]['id']}").json() == {"ok": True}
    assert (music / "song.wav").is_file()
