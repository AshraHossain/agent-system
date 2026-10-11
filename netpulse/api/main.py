"""Default API app, built from environment variables.

    uv run uvicorn netpulse.api.main:app --port 8000

| Variable | Default | Meaning |
|---|---|---|
| ``NETPULSE_DB`` | ``.netpulse/netpulse.db`` | SQLite checkpoints + audit log |
| ``NETPULSE_API_TOKENS`` | *(none — refuses to start)* | ``token:role:name,...``; see docs/human_approval.md |
| ``NETPULSE_API_WORKERS`` | ``4`` | background job threads |
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
            "NETPULSE_API_TOKENS='change-me:senior_operator:alice'"
        )
    tokens = TokenStore.parse(spec)
    db = Path(os.environ.get("NETPULSE_DB", DEFAULT_DB))
    store = PersistentStore(db)
    service = InvestigationService(Deps.default(), store)
    workers = int(os.environ.get("NETPULSE_API_WORKERS", "4"))
    return create_app(service, tokens, max_workers=workers)


app = build_app()
