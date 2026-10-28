"""Online sources that fill the library with media you may reuse in edits.

- Pexels and Pixabay videos: free stock footage, no attribution required
  (their licences only forbid reselling the clips as they are).
- Jamendo: Creative Commons music, searched for phonk. Every track keeps its
  licence and credit. "No derivatives" (ND) tracks are skipped: Creative
  Commons treats syncing music to video as an adaptation.

Each source needs a free API key, stored in keys.json in the data folder.
"""
from __future__ import annotations

import json
import random
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import httpx

from .config import Settings
from .library import CATEGORIES, Library, free_gb, safe_name

KEY_NAMES = ("pexels", "pixabay", "jamendo")
KEY_LINKS = {
    "pexels": "https://www.pexels.com/api/",
    "pixabay": "https://pixabay.com/api/docs/",
    "jamendo": "https://devportal.jamendo.com/",
}
MUSIC_QUERIES = ["phonk", "drift phonk", "brazilian phonk", "phonk house", "memphis phonk", "aggressive phonk"]
UA = {"User-Agent": "Vidigen/0.2 (desktop app)"}
MAX_FILE_MB = 300
MIN_FREE_GB = 5.0
# Width the 9:16 crop should keep before it gets upscaled to 1080x1920.
GOOD_CROP_WIDTH = 810


class Cancelled(Exception):
    pass


# ---- keys -------------------------------------------------------------------

def keys_file(settings: Settings) -> Path:
    return settings.data_dir / "keys.json"


def load_keys(settings: Settings) -> dict[str, str]:
    try:
        data = json.loads(keys_file(settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {k: str(data.get(k, "")).strip() for k in KEY_NAMES}


def save_keys(settings: Settings, updates: dict[str, Optional[str]]) -> dict[str, str]:
    keys = load_keys(settings)
    for k, v in updates.items():
        if k in KEY_NAMES and v is not None:
            keys[k] = v.strip()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    keys_file(settings).write_text(json.dumps(keys, indent=2), encoding="utf-8")
    return keys


def masked(keys: dict[str, str]) -> dict:
    return {k: {"set": bool(v), "hint": f"…{v[-4:]}" if len(v) > 6 else ("set" if v else "")} for k, v in keys.items()}


# ---- search results -----------------------------------------------------------

@dataclass
class FootageHit:
    source: str
    source_id: str
    url: str
    author: str
    page_url: str
    duration: float
    width: int
    height: int
    tags: str = ""


@dataclass
class MusicHit:
    source: str
    source_id: str
    url: str
    title: str
    artist: str
    license: str
    license_url: str
    page_url: str
    duration: float


def crop_width(w: int, h: int) -> float:
    """Width left after cropping a w x h frame to 9:16."""
    return min(w, h * 9 / 16)


def pick_file(files: list[dict]) -> Optional[dict]:
    """The smallest file that still crops to a sharp 9:16 frame, else the sharpest."""
    files = [f for f in files if f.get("link") or f.get("url")]
    if not files:
        return None
    good = [f for f in files if crop_width(f["width"], f["height"]) >= GOOD_CROP_WIDTH and f["height"] <= 2160
            and f["width"] <= 3840]
    if good:
        return min(good, key=lambda f: f["width"] * f["height"])
    capped = [f for f in files if f["width"] <= 3840 and f["height"] <= 3840] or files
    return max(capped, key=lambda f: crop_width(f["width"], f["height"]))


def license_name(url: str) -> str:
    """'http://creativecommons.org/licenses/by-nc-sa/3.0/' -> 'cc-by-nc-sa'."""
    u = url.lower()
    if "publicdomain" in u or "/zero/" in u:
        return "cc0"
    if "/licenses/" in u:
        part = u.split("/licenses/")[1].split("/")[0]
        return f"cc-{part}"
    return "other"


class PexelsSource:
    name = "pexels"

    def __init__(self, key: str, client: httpx.Client):
        self.key, self.client = key, client

    def search(self, query: str, page: int) -> tuple[list[FootageHit], bool]:
        r = self.client.get(
            "https://api.pexels.com/videos/search",
            params={"query": query, "per_page": 40, "page": page, "size": "medium"},
            headers={"Authorization": self.key},
        )
        r.raise_for_status()
        data = r.json()
        hits = []
        for v in data.get("videos", []):
            files = [
                {"link": f.get("link"), "width": f.get("width") or 0, "height": f.get("height") or 0}
                for f in v.get("video_files", []) if (f.get("file_type") or "video/mp4") == "video/mp4"
            ]
            best = pick_file(files)
            if not best:
                continue
            hits.append(FootageHit(
                source=self.name, source_id=str(v["id"]), url=best["link"], author=(v.get("user") or {}).get("name", ""),
                page_url=v.get("url", ""), duration=float(v.get("duration") or 0), width=best["width"], height=best["height"],
            ))
        return hits, bool(data.get("next_page"))

    def test(self) -> None:
        self.search("car", 1)


class PixabaySource:
    name = "pixabay"

    def __init__(self, key: str, client: httpx.Client):
        self.key, self.client = key, client

    def search(self, query: str, page: int) -> tuple[list[FootageHit], bool]:
        r = self.client.get(
            "https://pixabay.com/api/videos/",
            params={"key": self.key, "q": query, "per_page": 50, "page": page, "safesearch": "true"},
        )
        if r.status_code == 400 and page > 1:
            return [], False  # Pixabay answers 400 past the last page
        r.raise_for_status()
        data = r.json()
        hits = []
        for v in data.get("hits", []):
            sizes = v.get("videos") or {}
            files = [
                {"url": s.get("url"), "width": s.get("width") or 0, "height": s.get("height") or 0}
                for s in sizes.values() if isinstance(s, dict)
            ]
            best = pick_file(files)
            if not best:
                continue
            hits.append(FootageHit(
                source=self.name, source_id=str(v["id"]), url=best["url"], author=v.get("user", ""),
                page_url=v.get("pageURL", ""), duration=float(v.get("duration") or 0), width=best["width"],
                height=best["height"], tags=v.get("tags", ""),
            ))
        return hits, page * 50 < int(data.get("totalHits") or 0)

    def test(self) -> None:
        self.search("car", 1)


class JamendoSource:
    name = "jamendo"

    def __init__(self, client_id: str, client: httpx.Client):
        self.client_id, self.client = client_id, client

    def search(self, query: str, page: int) -> tuple[list[MusicHit], bool]:
        limit = 50
        r = self.client.get(
            "https://api.jamendo.com/v3.0/tracks/",
            params={"client_id": self.client_id, "format": "json", "limit": limit, "offset": (page - 1) * limit,
                    "search": query, "audioformat": "mp32", "audiodlformat": "mp32", "order": "popularity_total"},
        )
        r.raise_for_status()
        data = r.json()
        head = data.get("headers") or {}
        if head.get("status") not in (None, "success"):
            raise RuntimeError(head.get("error_message") or "Jamendo refused the request")
        hits = []
        for t in data.get("results", []):
            lic_url = t.get("license_ccurl") or ""
            lic = license_name(lic_url)
            if "nd" in lic.split("-"):
                continue  # no derivatives: may not be synced to video
            url = t.get("audiodownload") if t.get("audiodownload_allowed") and t.get("audiodownload") else t.get("audio")
            if not url:
                continue
            hits.append(MusicHit(
                source=self.name, source_id=str(t["id"]), url=url, title=t.get("name", ""),
                artist=t.get("artist_name", ""), license=lic, license_url=lic_url,
                page_url=t.get("shareurl", ""), duration=float(t.get("duration") or 0),
            ))
        return hits, len(data.get("results", [])) == limit

    def test(self) -> None:
        self.search("phonk", 1)


def make_client(transport: Optional[httpx.BaseTransport] = None) -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(30.0, read=120.0), follow_redirects=True, headers=UA, transport=transport)


def footage_sources(keys: dict[str, str], client: httpx.Client) -> list:
    out = []
    if keys.get("pexels"):
        out.append(PexelsSource(keys["pexels"], client))
    if keys.get("pixabay"):
        out.append(PixabaySource(keys["pixabay"], client))
    return out


def test_keys(settings: Settings, client: Optional[httpx.Client] = None) -> dict[str, dict]:
    """Make one small request per saved key and report what each said."""
    keys = load_keys(settings)
    client = client or make_client()
    makers = {"pexels": PexelsSource, "pixabay": PixabaySource, "jamendo": JamendoSource}
    out = {}
    for name, make in makers.items():
        if not keys[name]:
            out[name] = {"ok": False, "error": "not set"}
            continue
        try:
            make(keys[name], client).test()
            out[name] = {"ok": True, "error": ""}
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            out[name] = {"ok": False, "error": "key rejected" if code in (401, 403) else f"HTTP {code}"}
        except Exception as e:
            out[name] = {"ok": False, "error": str(e)[:200]}
    return out


# ---- downloading ----------------------------------------------------------------

def download(client: httpx.Client, url: str, dest: Path, cancel: Optional[threading.Event] = None) -> Path:
    tmp = dest.with_suffix(dest.suffix + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    try:
        with client.stream("GET", url) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(1 << 16):
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
                    size += len(chunk)
                    if size > MAX_FILE_MB * 2**20:
                        raise ValueError("file is larger than the size limit")
                    f.write(chunk)
        tmp.replace(dest)
        return dest
    finally:
        tmp.unlink(missing_ok=True)


@dataclass
class RefreshResult:
    clips_added: int = 0
    tracks_added: int = 0
    errors: list[str] = None
    stopped: str = ""

    def to_dict(self) -> dict:
        return {"clips_added": self.clips_added, "tracks_added": self.tracks_added,
                "errors": (self.errors or [])[:20], "stopped": self.stopped}


Progress = Callable[[float, str], None]


def refresh(
    lib: Library,
    *,
    clips: int = 30,
    tracks: int = 10,
    categories: Optional[list[str]] = None,
    max_footage_gb: float = 40.0,
    client: Optional[httpx.Client] = None,
    progress: Optional[Progress] = None,
    cancel: Optional[threading.Event] = None,
    seed: Optional[int] = None,
) -> RefreshResult:
    """Download up to `clips` new footage clips (spread over `categories`) and
    `tracks` new phonk tracks. Search pages are remembered, so every refresh
    continues where the last one stopped instead of re-reading page one."""
    settings = lib.settings
    keys = load_keys(settings)
    client = client or make_client()
    rng = random.Random(seed)
    res = RefreshResult(errors=[])
    say = progress or (lambda f, m: None)
    cats = [c for c in (categories or list(CATEGORIES)) if c in CATEGORIES]
    total = max(1, clips + tracks)
    done = 0

    def check_stop() -> bool:
        if cancel is not None and cancel.is_set():
            res.stopped = "cancelled"
        elif free_gb(lib.root) < MIN_FREE_GB:
            res.stopped = f"less than {MIN_FREE_GB:.0f} GB of disk left"
        return bool(res.stopped)

    # ---- footage: round-robin over categories, sources and queries
    sources = footage_sources(keys, client)
    if clips and not sources:
        res.errors.append("Add a Pexels or Pixabay key to download footage.")
    elif clips and cats:
        budget = {c: clips // len(cats) + (1 if i < clips % len(cats) else 0) for i, c in enumerate(cats)}
        plans = [(c, s, q) for c in cats for s in sources for q in CATEGORIES[c]["queries"]]
        rng.shuffle(plans)
        idle_rounds = 0
        while any(budget.values()) and idle_rounds < 2 and not check_stop():
            progressed = False
            for cat, src, query in plans:
                if budget[cat] <= 0 or check_stop():
                    continue
                if lib.footage_bytes() / 2**30 >= max_footage_gb:
                    res.stopped = f"footage folder reached its {max_footage_gb:g} GB limit"
                    break
                key = f"{src.name}:{query}"
                page = lib.cursor(key)
                try:
                    hits, more = src.search(query, page)
                except Exception as e:
                    res.errors.append(f"{src.name} '{query}': {_short(e)}")
                    continue
                fresh = [h for h in hits if 3 <= h.duration <= 60 and not lib.has("clips", h.source, h.source_id)]
                # Stay on this page until every usable result on it is in the library.
                if len(fresh) <= 2:
                    lib.set_cursor(key, page + 1 if more else 1)
                for h in fresh[:2]:
                    if budget[cat] <= 0 or check_stop():
                        break
                    say(done / total, f"Footage: {CATEGORIES[cat]['label'].lower()} from {src.name} ({h.width}x{h.height})")
                    dest = lib.root / "footage" / f"{h.source}-{h.source_id}.mp4"
                    try:
                        download(client, h.url, dest, cancel)
                        lib.add_clip(dest, source=h.source, source_id=h.source_id, category=cat, author=h.author,
                                     page_url=h.page_url, tags=h.tags)
                    except Cancelled:
                        res.stopped = "cancelled"
                        break
                    except Exception as e:
                        dest.unlink(missing_ok=True)
                        lib.skip(h.source, h.source_id)
                        res.errors.append(f"{src.name} {h.source_id}: {_short(e)}")
                        continue
                    budget[cat] -= 1
                    res.clips_added += 1
                    done += 1
                    progressed = True
                if res.stopped:
                    break
            idle_rounds = 0 if progressed else idle_rounds + 1

    # ---- music
    if tracks and not keys.get("jamendo"):
        res.errors.append("Add a Jamendo client ID to download music, or import a folder of tracks.")
    elif tracks and not res.stopped:
        src = JamendoSource(keys["jamendo"], client)
        want = tracks
        queries = list(MUSIC_QUERIES)
        idle = 0
        while want > 0 and idle < len(queries) and not check_stop():
            query = queries[0]
            queries.append(queries.pop(0))
            key = f"jamendo:{query}"
            page = lib.cursor(key)
            try:
                hits, more = src.search(query, page)
            except Exception as e:
                res.errors.append(f"jamendo '{query}': {_short(e)}")
                idle += 1
                continue
            fresh = [h for h in hits if not lib.has("tracks", h.source, h.source_id) and h.duration >= 30]
            if len(fresh) <= want:
                lib.set_cursor(key, page + 1 if more else 1)
            idle = 0 if fresh else idle + 1
            for h in fresh:
                if want <= 0 or check_stop():
                    break
                say(done / total, f"Music: {h.title} by {h.artist}")
                dest = lib.root / "music" / f"jamendo-{h.source_id}-{safe_name(h.title)}.mp3"
                try:
                    download(client, h.url, dest, cancel)
                    lib.add_track(dest, source=h.source, source_id=h.source_id, title=h.title, artist=h.artist,
                                  license=h.license, license_url=h.license_url, page_url=h.page_url)
                except Cancelled:
                    res.stopped = "cancelled"
                    break
                except Exception as e:
                    dest.unlink(missing_ok=True)
                    lib.skip(h.source, h.source_id)
                    res.errors.append(f"jamendo {h.source_id}: {_short(e)}")
                    continue
                want -= 1
                res.tracks_added += 1
                done += 1
    say(1.0, "Library updated")
    return res


def _short(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        return "key rejected" if code in (401, 403) else ("rate limited, try later" if code == 429 else f"HTTP {code}")
    return str(e)[:160] or type(e).__name__
