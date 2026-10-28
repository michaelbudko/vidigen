"""Command line: `python -m vidigen_engine serve` for the app, `render` for scripting."""
from __future__ import annotations

import argparse
import json
import sys

from .config import PRESETS, Settings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="vidigen_engine")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the local API for the desktop app")
    s.add_argument("--port", type=int, default=0)

    sub.add_parser("system", help="print detected hardware and recommended preset")

    a = sub.add_parser("analyze", help="print tempo, beats and the suggested clip window")
    a.add_argument("song")
    a.add_argument("--duration", type=float, default=45)

    r = sub.add_parser("render", help="render a video without the UI")
    r.add_argument("prompt")
    r.add_argument("--song", help="audio file; without one the video is silent and cut to --bpm")
    r.add_argument("--bpm", type=float, default=120)
    r.add_argument("--style", default="")
    r.add_argument("--duration", type=float, default=45)
    r.add_argument("--start", type=float)
    r.add_argument("--preset", default="mock", choices=list(PRESETS))
    r.add_argument("--license", default="own_work",
                   choices=["public_domain", "cc0", "cc_by", "cc_by_sa", "own_work", "licensed"])
    r.add_argument("--title", default="")
    r.add_argument("--artist", default="")
    r.add_argument("--source-url", default="")
    r.add_argument("--source-video", help="footage to restyle (video-to-video)")
    r.add_argument("--character", action="append", default=[], help="character id to cast (repeatable)")
    r.add_argument("--no-llm", action="store_true")
    r.add_argument("--seed", type=int, default=42)

    args = ap.parse_args(argv)
    settings = Settings()

    if args.cmd == "serve":
        from .server import serve

        serve(args.port)
        return 0

    if args.cmd == "system":
        from .hardware import report

        print(json.dumps(report(settings), indent=2))
        return 0

    if args.cmd == "analyze":
        from pathlib import Path

        from .audio import analyze, choose_window

        an = analyze(Path(args.song), settings)
        w = choose_window(an, args.duration)
        print(json.dumps({"tempo": an.tempo, "duration": an.duration, "beats": len(an.beats),
                          "window": [w.start, w.end]}, indent=2))
        return 0

    from . import pipeline
    from .licensing import SongRights

    req = pipeline.RenderRequest(
        song_path=args.song, prompt=args.prompt, style=args.style, duration=args.duration,
        start=args.start, preset=args.preset, use_llm=not args.no_llm, seed=args.seed,
        source_video=args.source_video,
        rights=SongRights(license=args.license, title=args.title, artist=args.artist,
                          source_url=args.source_url),
        bpm=args.bpm,
        characters=args.character,
    )
    job = pipeline.new_job(req, settings)
    last = ""

    import threading

    t = threading.Thread(target=pipeline.run, args=(job, settings))
    t.start()
    while t.is_alive():
        t.join(0.5)
        if job.message != last:
            last = job.message
            print(f"[{job.progress * 100:5.1f}%] {job.message}", file=sys.stderr)
    if job.status != "done":
        print(f"{job.status}: {job.error}", file=sys.stderr)
        return 1
    print(job.output)
    print(f"\nSuggested caption:\n{job.caption}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
