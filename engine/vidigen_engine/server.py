"""Local HTTP API used by the desktop UI. Binds to loopback only and requires
the per-launch token the Electron main process passes in."""
from __future__ import annotations

import os
import queue
import secrets
import socket
import sys
import threading
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import audio, hardware, pipeline
from .config import Settings

settings = Settings()
TOKEN = os.environ.get("VIDIGEN_TOKEN") or secrets.token_urlsafe(24)

jobs: dict[str, pipeline.Job] = {}
work: "queue.Queue[pipeline.Job]" = queue.Queue()


def _worker() -> None:
    # One job at a time: generation saturates the GPU anyway.
    while True:
        job = work.get()
        if job.status == "queued":
            pipeline.run(job, settings)


threading.Thread(target=_worker, daemon=True).start()


def auth(x_vidigen_token: str = Header(default="")) -> None:
    if not secrets.compare_digest(x_vidigen_token, TOKEN):
        raise HTTPException(401, "bad token")


app = FastAPI(title="Vidigen engine", dependencies=[Depends(auth)])
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class AnalyzeRequest(BaseModel):
    path: str
    duration: float = 45.0
    start: float | None = None


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/system")
def system():
    return {**hardware.report(settings), "settings": settings.to_dict()}


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
