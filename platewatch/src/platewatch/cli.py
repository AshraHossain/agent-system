"""`platewatch` command: demo | ingest | search | ask | serve."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from platewatch.core.store import PlateStore
from platewatch.pipeline import PlatePipeline
from platewatch.vision.protocols import Detector, Reader

DEFAULT_DB = Path("var/platewatch/plates.db")
DEFAULT_INBOX = Path("var/platewatch/inbox")
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


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
    sv = sub.add_parser("serve", help="read-only HTTP search: GET /plates/search (needs [api])")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8100)
    sv.add_argument(
        "--allow-unauthenticated",
        action="store_true",
        help="serve beyond loopback without PLATEWATCH_API_TOKEN (not recommended)",
    )
    args = p.parse_args(argv)

    if args.cmd == "demo":
        from platewatch.demo import run_demo

        return run_demo()
    if args.cmd == "serve":
        return _serve(args)
    args.db.parent.mkdir(parents=True, exist_ok=True)
    with PlateStore(args.db) as store:
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


def _serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn

        from platewatch.api import api_token, create_app
    except ImportError:
        print("error: `serve` needs the api extra: uv sync --extra api", file=sys.stderr)
        return 2
    try:
        token = api_token()
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.host not in LOOPBACK_HOSTS and token is None:
        if not args.allow_unauthenticated:
            print(
                f"refusing to serve plate data on {args.host} without authentication: set "
                "PLATEWATCH_API_TOKEN, bind 127.0.0.1, or pass --allow-unauthenticated",
                file=sys.stderr,
            )
            return 2
        print(f"warning: serving plate data on {args.host} with no authentication", file=sys.stderr)
    try:
        app = create_app(args.db, token)
    except FileNotFoundError as e:
        print(f"error: {e} (ingest images first)", file=sys.stderr)
        return 2
    if token:
        print("auth: bearer token required (PLATEWATCH_API_TOKEN)", file=sys.stderr)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
