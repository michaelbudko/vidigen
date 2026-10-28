"""Local HTTP API used by the desktop UI. Binds to loopback only and requires
the per-launch token the Electron main process passes in (as a header, or as
`?t=` on image/video URLs that cannot carry headers)."""
from __future__ import annotations

import os
import queue
import secrets
import socket
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import audio, autoedit, hardware, installer, pipeline, sources
from .autopilot import Autopilot, AutopilotConfig
from .backends import image_backend
from .characters import STYLES, CharacterPatch, CharacterStore
from .config import PRESETS, Settings
from .library import CATEGORIES, Library

settings = Settings()
settings.llm_model = settings.load_state().get("llm_model", settings.llm_model)
store = CharacterStore(settings.characters_dir)
TOKEN = os.environ.get("VIDIGEN_TOKEN") or secrets.token_urlsafe(24)

jobs: dict[str, pipeline.Job] = {}
work: "queue.Queue[pipeline.Job]" = queue.Queue()
tasks: dict[str, dict] = {}  # character previews, library downloads and imports
setup_task: Optional[installer.SetupTask] = None

library = Library(settings)
edits = autoedit.EditStore(settings)
edit_work: "queue.Queue[autoedit.Edit]" = queue.Queue()
library_lock = threading.Lock()  # one download or import at a time


def _worker() -> None:
    # One job at a time: generation saturates the GPU anyway.
    while True:
        job = work.get()
        if job.status == "queued":
            pipeline.run(job, settings)


def _edit_worker() -> None:
    # Edits only need ffmpeg, so they run beside GPU renders, one at a time.
    while True:
        edit = edit_work.get()
        if edit.status == "queued":
            autoedit.build(edit, library)


def submit_edit(req: autoedit.EditRequest) -> autoedit.Edit:
    edit = edits.new(req)
    edit_work.put(edit)
    return edit


threading.Thread(target=_worker, daemon=True).start()
threading.Thread(target=_edit_worker, daemon=True).start()
autopilot = Autopilot(settings, library, submit_edit, library_lock)
autopilot.start()


def auth(x_vidigen_token: str = Header(default=""), t: str = Query(default="")) -> None:
    if not secrets.compare_digest(x_vidigen_token or t, TOKEN):
        raise HTTPException(401, "bad token")


app = FastAPI(title="Vidigen engine", dependencies=[Depends(auth)])
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/system")
def system():
    return {**hardware.report(settings), "settings": settings.to_dict(), "styles": list(STYLES)}


# ---- setup ---------------------------------------------------------------

class SetupRequest(BaseModel):
    preset: str
    llm: Optional[str] = None


@app.post("/setup")
def start_setup(req: SetupRequest):
    global setup_task
    if req.preset not in PRESETS:
        raise HTTPException(400, f"unknown preset {req.preset}")
    if setup_task and setup_task.status == "running":
        raise HTTPException(409, "setup is already running")
    setup_task = installer.plan(req.preset, req.llm)
    threading.Thread(target=installer.run, args=(setup_task, settings), daemon=True).start()
    return setup_task.to_dict()


@app.get("/setup")
def get_setup():
    return setup_task.to_dict() if setup_task else None


@app.post("/setup/skip")
def skip_setup():
    """Use Test mode for now; the Welcome screen can be reopened from the title bar."""
    return settings.save_state(complete=True, preset=settings.load_state().get("preset", "mock"), skipped=True)


# ---- songs and renders ---------------------------------------------------

class AnalyzeRequest(BaseModel):
    path: str
    duration: float = 45.0
    start: float | None = None


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    p = Path(req.path)
    if not p.is_file():
        raise HTTPException(404, f"no such file: {p}")
    a = audio.analyze(p, settings)
    w = audio.choose_window(a, req.duration, req.start)
    return {"analysis": a.to_dict(), "window": {"start": w.start, "end": w.end}}


@app.post("/jobs")
def create_job(req: pipeline.RenderRequest):
    if req.song_path and not Path(req.song_path).is_file():
        raise HTTPException(404, f"no such file: {req.song_path}")
    if req.source_video and not Path(req.source_video).is_file():
        raise HTTPException(404, f"no such file: {req.source_video}")
    for cid in req.characters:
        _character(cid)
    job = pipeline.new_job(req, settings)
    jobs[job.id] = job
    work.put(job)
    return job.to_dict()


@app.get("/jobs")
def list_jobs():
    return [j.to_dict() for j in sorted(jobs.values(), key=lambda j: j.created, reverse=True)]


def _job(jid: str) -> pipeline.Job:
    if jid not in jobs:
        raise HTTPException(404, "no such job")
    return jobs[jid]


@app.get("/jobs/{jid}")
def get_job(jid: str):
    return _job(jid).to_dict()


@app.post("/jobs/{jid}/cancel")
def cancel_job(jid: str):
    job = _job(jid)
    if job.status == "queued":
        job.status = "cancelled"
    job.cancel()
    return job.to_dict()


@app.get("/jobs/{jid}/files/{kind}")
def job_file(jid: str, kind: str, index: int = 0):
    job = _job(jid)
    paths = {
        "video": Path(job.output) if job.output else None,
        "thumbnail": Path(job.thumbnail) if job.thumbnail else None,
        "keyframe": job.dir / "keyframes" / f"{index:03d}.png",
    }
    p = paths.get(kind)
    if p is None or not p.is_file():
        raise HTTPException(404, "not ready")
    return FileResponse(p)


# ---- characters ------------------------------------------------------------

class CreateCharacter(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    photos: list[str] = Field(default_factory=list)
    description: str = ""
    style: str = "realistic"
    consent: bool = False


class PhotoPaths(BaseModel):
    photos: list[str]


class PreviewRequest(BaseModel):
    preset: str = "mock"
    count: int = Field(default=4, ge=1, le=8)
    style: Optional[str] = None


def _character(cid: str):
    try:
        return store.get(cid)
    except KeyError:
        raise HTTPException(404, "no such character")


def _bad_request(fn, *args):
    try:
        return fn(*args)
    except KeyError:
        raise HTTPException(404, "no such character")
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))


@app.get("/characters")
def list_characters():
    return [c.model_dump() for c in store.list()]


@app.post("/characters")
def create_character(req: CreateCharacter):
    c = _bad_request(store.create, req.name, req.consent, req.photos, req.description, req.style)
    return c.model_dump()


@app.get("/characters/{cid}")
def get_character(cid: str):
    return _character(cid).model_dump()


@app.patch("/characters/{cid}")
def update_character(cid: str, patch: CharacterPatch):
    return _bad_request(store.update, cid, patch).model_dump()


@app.post("/characters/{cid}/photos")
def add_photos(cid: str, req: PhotoPaths):
    return _bad_request(store.add_photos, cid, req.photos).model_dump()


@app.delete("/characters/{cid}/photos")
def remove_photo(cid: str, file: str):
    return _bad_request(store.remove_photo, cid, file).model_dump()


@app.delete("/characters/{cid}")
def delete_character(cid: str):
    _character(cid)
    store.delete(cid)
    return {"ok": True}


@app.get("/characters/{cid}/file")
def character_file(cid: str, path: str):
    _character(cid)
    d = store.dir(cid).resolve()
    p = (d / path).resolve()
    if d not in p.parents or not p.is_file():
        raise HTTPException(404, "no such file")
    return FileResponse(p)


@app.post("/characters/{cid}/previews")
def make_previews(cid: str, req: PreviewRequest):
    _character(cid)
    if req.preset not in PRESETS:
        raise HTTPException(400, f"unknown preset {req.preset}")
    tid = uuid.uuid4().hex[:10]
    task = {"id": tid, "character": cid, "status": "queued", "message": "Waiting for the GPU", "progress": 0.0,
            "error": "", "created": time.time()}
    tasks[tid] = task

    def go():
        with pipeline.gpu_lock:
            task["status"] = "running"

            def progress(i, n):
                task["progress"], task["message"] = i / n, f"Portrait {i + 1} of {n}"

            try:
                store.render_previews(cid, image_backend(PRESETS[req.preset]), req.count, req.style, progress=progress)
                task["status"], task["progress"], task["message"] = "done", 1.0, "Done"
            except ModuleNotFoundError as e:
                task["status"], task["error"] = "failed", pipeline.missing_models_message(req.preset, e)
            except Exception as e:
                task["status"], task["error"] = "failed", str(e)

    threading.Thread(target=go, daemon=True).start()
    return task


@app.get("/tasks/{tid}")
def get_task(tid: str):
    if tid not in tasks:
        raise HTTPException(404, "no such task")
    return {k: v for k, v in tasks[tid].items() if not k.startswith("_")}


@app.post("/tasks/{tid}/cancel")
def cancel_task(tid: str):
    task = tasks.get(tid)
    if task is None:
        raise HTTPException(404, "no such task")
    if "_cancel" in task:
        task["_cancel"].set()
    return get_task(tid)


def _library_task(kind: str, fn) -> dict:
    """Run `fn(task)` in the background under the library lock and track it as a task."""
    if library_lock.locked():
        raise HTTPException(409, "The library is already updating. Wait for it to finish.")
    tid = uuid.uuid4().hex[:10]
    task = {"id": tid, "kind": kind, "status": "running", "message": "Starting", "progress": 0.0, "error": "",
            "result": None, "created": time.time(), "_cancel": threading.Event()}
    tasks[tid] = task

    def go():
        with library_lock:
            try:
                task["result"] = fn(task)
                task["status"], task["progress"] = "done", 1.0
            except Exception as e:
                task["status"], task["error"] = "failed", str(e)

    threading.Thread(target=go, daemon=True).start()
    return get_task(tid)


# ---- library ---------------------------------------------------------------------

class RefreshRequest(BaseModel):
    clips: int = Field(default=30, ge=0, le=500)
    tracks: int = Field(default=10, ge=0, le=200)
    categories: list[str] = Field(default_factory=list)
    max_footage_gb: float = Field(default=40.0, ge=1, le=4000)


class ImportRequest(BaseModel):
    folder: str
    kind: str = Field(pattern="^(music|footage)$")
    license: str = ""
    category: str = "racing"


class TrackPatch(BaseModel):
    title: Optional[str] = None
    artist: Optional[str] = None
    license: Optional[str] = None
    license_url: Optional[str] = None


@app.get("/library")
def library_overview():
    keys = sources.load_keys(settings)
    return {
        "stats": library.stats(),
        "categories": {k: {"label": v["label"], "queries": v["queries"]} for k, v in CATEGORIES.items()},
        "looks": {k: v["label"] for k, v in autoedit.LOOKS.items()},
        "intensity": {k: v["label"] for k, v in autoedit.INTENSITY.items()},
        "effects": list(autoedit.EFFECTS),
        "keys": sources.masked(keys),
        "key_links": sources.KEY_LINKS,
        "busy": library_lock.locked(),
        "root": str(library.root),
    }


@app.get("/library/tracks")
def library_tracks():
    return [t.summary() for t in library.tracks()]


@app.get("/library/clips")
def library_clips(category: str = ""):
    return [c.summary() for c in library.clips([category] if category else None)]


@app.patch("/library/tracks/{tid}")
def update_track(tid: str, patch: TrackPatch):
    try:
        return library.update_track(tid, **patch.model_dump()).summary()
    except KeyError:
        raise HTTPException(404, "no such track")


@app.delete("/library/{kind}/{item_id}")
def remove_media(kind: str, item_id: str):
    if kind not in ("track", "clip"):
        raise HTTPException(400, "kind is track or clip")
    try:
        library.remove(kind, item_id)
    except KeyError:
        raise HTTPException(404, f"no such {kind}")
    return {"ok": True}


@app.get("/library/{kind}/{item_id}/file")
def media_file(kind: str, item_id: str, what: str = "media"):
    try:
        p = library.media_path(kind, item_id, what)
    except KeyError:
        raise HTTPException(404, f"no such {kind}")
    if not p.is_file():
        raise HTTPException(404, "file missing")
    return FileResponse(p)


@app.post("/library/refresh")
def refresh_library(req: RefreshRequest):
    def fn(task):
        def progress(f, msg):
            task["progress"], task["message"] = f, msg

        return sources.refresh(library, clips=req.clips, tracks=req.tracks, categories=req.categories or None,
                               max_footage_gb=req.max_footage_gb, progress=progress,
                               cancel=task["_cancel"]).to_dict()

    return _library_task("refresh", fn)


@app.post("/library/import")
def import_folder(req: ImportRequest):
    folder = Path(req.folder)
    if not folder.is_dir():
        raise HTTPException(404, f"no such folder: {folder}")
    if req.kind == "footage" and req.category not in CATEGORIES:
        raise HTTPException(400, f"unknown category {req.category}")

    def fn(task):
        def progress(i, n, name):
            task["progress"], task["message"] = i / max(1, n), f"{i + 1} of {n}: {name}"

        return library.import_folder(folder, req.kind, license=req.license, category=req.category,
                                     progress=progress)

    return _library_task("import", fn)


# ---- API keys --------------------------------------------------------------------------

class KeysUpdate(BaseModel):
    pexels: Optional[str] = None
    pixabay: Optional[str] = None
    jamendo: Optional[str] = None


@app.get("/keys")
def get_keys():
    return sources.masked(sources.load_keys(settings))


@app.put("/keys")
def put_keys(req: KeysUpdate):
    return sources.masked(sources.save_keys(settings, req.model_dump()))


@app.post("/keys/test")
def check_keys():
    return sources.test_keys(settings)


# ---- edits -------------------------------------------------------------------------------

@app.post("/edits")
def create_edit(req: autoedit.EditRequest):
    if req.track_id:
        try:
            library.track(req.track_id)
        except KeyError:
            raise HTTPException(404, "no such track")
    bad = [c for c in req.categories if c not in CATEGORIES]
    if bad:
        raise HTTPException(400, f"unknown category {bad[0]}")
    return submit_edit(req).to_dict()


@app.get("/edits")
def list_edits():
    return edits.list()


@app.get("/edits/{eid}")
def get_edit(eid: str):
    try:
        return edits.get(eid)
    except KeyError:
        raise HTTPException(404, "no such edit")


@app.post("/edits/{eid}/cancel")
def cancel_edit(eid: str):
    e = edits.live.get(eid)
    if e is None:
        raise HTTPException(404, "no such running edit")
    if e.status == "queued":
        e.status = "cancelled"
        e.save()
    e.cancel()
    return e.to_dict()


@app.delete("/edits/{eid}")
def delete_edit(eid: str):
    try:
        edits.delete(eid)
    except KeyError:
        raise HTTPException(404, "no such edit")
    return {"ok": True}


@app.get("/edits/{eid}/files/{kind}")
def edit_file(eid: str, kind: str):
    if kind not in ("video", "thumb"):
        raise HTTPException(400, "kind is video or thumb")
    try:
        return FileResponse(edits.file(eid, kind))
    except KeyError:
        raise HTTPException(404, "not ready")


# ---- autopilot ------------------------------------------------------------------------------

@app.get("/autopilot")
def get_autopilot():
    return autopilot.to_dict()


@app.put("/autopilot")
def put_autopilot(cfg: AutopilotConfig):
    bad = [c for c in cfg.categories if c not in CATEGORIES]
    if bad:
        raise HTTPException(400, f"unknown category {bad[0]}")
    error = autopilot.set_config(cfg)
    return {**autopilot.to_dict(), "error": error}


@app.post("/autopilot/run")
def run_autopilot():
    if not autopilot.run_async():
        raise HTTPException(409, "The autopilot is already running.")
    return autopilot.to_dict()


@app.post("/autopilot/stop")
def stop_autopilot():
    autopilot.stop()
    return autopilot.to_dict()


def serve(port: int = 0) -> None:
    import uvicorn

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    # The Electron main process waits for this line to learn the port.
    print(f"VIDIGEN_READY port={sock.getsockname()[1]}", flush=True)
    if "VIDIGEN_TOKEN" not in os.environ:
        print(f"VIDIGEN_TOKEN={TOKEN}", file=sys.stderr, flush=True)
    config = uvicorn.Config(app, log_level="warning")
    uvicorn.Server(config).run(sockets=[sock])
