"""Command line: `python -m vidigen_engine serve` for the app; `edit`, `library` and
`render` for scripting."""
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

    e = sub.add_parser("edit", help="make a phonk edit from the library")
    e.add_argument("--duration", type=float, default=20)
    e.add_argument("--intensity", default="hard", choices=["chill", "hard", "insane"])
    e.add_argument("--look", default="auto")
    e.add_argument("--category", action="append", default=[], help="footage theme (repeatable)")
    e.add_argument("--track", help="track id; default is the least used")
    e.add_argument("--hook", default="", help="text over the first seconds")
    e.add_argument("--count", type=int, default=1)

    lb = sub.add_parser("library", help="show, fill or import into the media library")
    lsub = lb.add_subparsers(dest="action", required=True)
    lsub.add_parser("stats")
    rf = lsub.add_parser("refresh", help="download new footage and music (needs API keys)")
    rf.add_argument("--clips", type=int, default=30)
    rf.add_argument("--tracks", type=int, default=10)
    rf.add_argument("--category", action="append", default=[])
    im = lsub.add_parser("import", help="add a folder of music or footage")
    im.add_argument("kind", choices=["music", "footage"])
    im.add_argument("folder")
    im.add_argument("--license", default="", help="licence of the music, such as cc-by or ncs")
    im.add_argument("--category", default="racing")

    au = sub.add_parser("autopilot", help="make today's edits if they are due (Task Scheduler runs this)")
    au.add_argument("--force", action="store_true", help="run now even if today's edits exist")

    args = ap.parse_args(argv)
    settings = Settings()

    if args.cmd == "autopilot":
        import threading

        from . import autoedit
        from .autopilot import Autopilot
        from .library import Library

        lib = Library(settings)
        store = autoedit.EditStore(settings)
        pilot = Autopilot(settings, lib, lambda req: autoedit.build(store.new(req), lib), threading.Lock())
        if not (args.force or (pilot.config.enabled and pilot.due())):
            print("Nothing to do: the autopilot is off or today's edits are done.", file=sys.stderr)
            return 0
        print(json.dumps(pilot.run(scheduled=not args.force), indent=2))
        return 0

    if args.cmd == "library":
        from pathlib import Path

        from . import sources
        from .library import Library

        lib = Library(settings)
        say = lambda f, m: print(f"[{f * 100:5.1f}%] {m}", file=sys.stderr)  # noqa: E731
        if args.action == "refresh":
            res = sources.refresh(lib, clips=args.clips, tracks=args.tracks, categories=args.category or None,
                                  progress=say)
            print(json.dumps(res.to_dict(), indent=2))
        elif args.action == "import":
            res = lib.import_folder(Path(args.folder), args.kind, license=args.license, category=args.category,
                                    progress=lambda i, n, name: print(f"{i + 1}/{n} {name}", file=sys.stderr))
            print(json.dumps(res, indent=2))
        print(json.dumps(lib.stats(), indent=2))
        return 0

    if args.cmd == "edit":
        from . import autoedit
        from .library import Library

        lib = Library(settings)
        store = autoedit.EditStore(settings)
        failed = 0
        for _ in range(args.count):
            req = autoedit.EditRequest(duration=args.duration, intensity=args.intensity, look=args.look,
                                       categories=args.category, track_id=args.track, hook=args.hook)
            ed = autoedit.build(store.new(req), lib)
            if ed.status != "done":
                print(f"{ed.status}: {ed.error}", file=sys.stderr)
                failed += 1
                continue
            print(ed.output)
            print(f"\n{ed.caption}\n", file=sys.stderr)
        return 1 if failed else 0

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
