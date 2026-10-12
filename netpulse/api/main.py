"""Default API app, built from environment variables.

    uv run uvicorn netpulse.api.main:app --port 8000

| Variable | Default | Meaning |
|---|---|---|
| ``NETPULSE_DB`` | ``.netpulse/netpulse.db`` | SQLite checkpoints + audit log |
| ``NETPULSE_API_TOKENS`` | *(none — refuses to start)* | ``token:role:name,...``; see docs/human_approval.md |
| ``NETPULSE_API_WORKERS`` | ``4`` | background job threads |
| ``NETPULSE_API_DOCS`` | ``false`` | serve ``/docs`` and ``/openapi.json`` (unauthenticated) |
| ``NETPULSE_DATA_DIR``, ``NETPULSE_LLM_PROVIDER``, … | see ``netpulse/config.py`` | dataset and LLM provider |

``NETPULSE_API_TOKENS`` has no default: an API with no configured
reviewers cannot authorize anything, so it refuses to start rather than
run open.
"""

from __future__ import annotations

import os
from pathlib import Path

from netpulse.api.app import create_app
from netpulse.api.auth import TokenStore
from netpulse.graph.deps import Deps
from netpulse.observability import apply_tracing_policy, configure_logging
from netpulse.persistence.store import PersistentStore
from netpulse.service import InvestigationService

DEFAULT_DB = Path(".netpulse/netpulse.db")


def build_app():
    configure_logging()
    apply_tracing_policy()
    spec = os.environ.get("NETPULSE_API_TOKENS")
    if not spec:
        raise RuntimeError(
            "NETPULSE_API_TOKENS is not set. Configure at least one reviewer, e.g. "
            "NETPULSE_API_TOKENS='<16+ random chars>:senior_operator:alice'"
        )
    tokens = TokenStore.parse(spec)
    if weak := tokens.weak_tokens():
        raise RuntimeError(
            f"NETPULSE_API_TOKENS has weak or placeholder tokens for: {', '.join(weak)}. "
            "Use at least 16 random characters each (python -c 'import secrets; print(secrets.token_urlsafe(24))')."
        )
    db = Path(os.environ.get("NETPULSE_DB", DEFAULT_DB))
    store = PersistentStore(db)
    service = InvestigationService(Deps.default(), store)
    workers = int(os.environ.get("NETPULSE_API_WORKERS", "4"))
    docs = os.environ.get("NETPULSE_API_DOCS", "false").strip().lower() in {"1", "true", "yes", "on"}
    return create_app(service, tokens, max_workers=workers, docs=docs)


def __getattr__(name: str):
    """``uvicorn netpulse.api.main:app`` resolves ``app`` here, so importing this module has no side effects."""
    if name == "app":
        global _app
        if _app is None:
            _app = build_app()
        return _app
    raise AttributeError(name)


_app = None
