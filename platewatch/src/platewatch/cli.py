"""`platewatch` command: demo | ingest | search | ask."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from platewatch.core.store import PlateStore
from platewatch.pipeline import PlatePipeline
from platewatch.vision.protocols import Detector, Reader

DEFAULT_DB = Path("var/platewatch/plates.db")
DEFAULT_INBOX = Path("var/platewatch/inbox")


def _backend(name: str) -> tuple[Detector, Reader]:
    if name == "hf":
        from platewatch.vision.huggingface import HFPlateDetector, HFPlateReader

        return HFPlateDetector(), HFPlateReader()
    raise SystemExit(
        "only --backend hf is available outside `demo` (scripted backend is demo-only)"
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="platewatch")
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser(
        "demo", help="end-to-end demo on synthetic images (scripted backend, mock agent)"
    )
    ing = sub.add_parser("ingest", help="scan an image directory with Hugging Face models")
    ing.add_argument("inbox", type=Path)
    ing.add_argument("--backend", default="hf")
    s = sub.add_parser("search", help="fuzzy-search stored plates")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=5)
    a = sub.add_parser(
        "ask", help="ask the PydanticAI agent (PLATEWATCH_AGENT_MODEL, default mock)"
    )
    a.add_argument("question")
    a.add_argument("--inbox", type=Path, default=DEFAULT_INBOX)
    args = p.parse_args(argv)

    if args.cmd == "demo":
        from platewatch.demo import run_demo

        return run_demo()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    store = PlateStore(args.db)
    if args.cmd == "ingest":
        det, rd = _backend(args.backend)
        print(PlatePipeline(det, rd, store).scan_inbox(args.inbox).model_dump_json(indent=2))
    elif args.cmd == "search":
        for h in store.search(args.query, limit=args.limit):
            print(h.model_dump_json())
    else:
        from platewatch.agent import PlateDeps, build_agent, default_model

        det, rd = _backend("hf")
        deps = PlateDeps(PlatePipeline(det, rd, store), store, args.inbox)
        print(
            build_agent(default_model())
            .run_sync(args.question, deps=deps)
            .output.model_dump_json(indent=2)
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
