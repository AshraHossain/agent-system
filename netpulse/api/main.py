"""Default API app, built from environment variables.

    uv run uvicorn netpulse.api.main:app --port 8000

| Variable | Default | Meaning |
|---|---|---|
| ``NETPULSE_DB`` | ``.netpulse/netpulse.db`` | SQLite checkpoints + audit log |
| ``NETPULSE_AUTH_MODE`` | ``tokens`` | ``oidc`` (production) or ``tokens`` (development only) |
| ``NETPULSE_API_TOKENS`` | *(none)* | ``tokens`` mode: ``token:role:name,...`` |
| ``NETPULSE_OIDC_ISSUER`` / ``_AUDIENCE`` / ``_ROLE_MAP`` | *(none)* | ``oidc`` mode, all required |
| ``NETPULSE_OIDC_JWKS_URL`` / ``_ROLE_CLAIM`` / ``_NAME_CLAIM`` | discovery / ``groups`` / ``sub`` | optional |
| ``NETPULSE_AUDIT_KEY`` | *(none — refuses to start)* | HMAC key (32+ chars) for the audit hash chain |
| ``NETPULSE_API_WORKERS`` | ``4`` | background job threads |
| ``NETPULSE_API_DOCS`` | ``false`` | serve ``/docs`` and ``/openapi.json`` (unauthenticated) |
| ``NETPULSE_REQUIRE_HTTPS`` | ``true`` in oidc mode | refuse requests whose scheme is not https (``/health`` exempt) |
| ``NETPULSE_ALLOWED_HOSTS`` | *(any)* | comma-separated Host header allowlist |
| ``NETPULSE_MAX_BODY_BYTES`` | ``262144`` | request body cap (413) |
| ``NETPULSE_RATE_LIMIT_{IP,READ,WRITE,AUTH_FAIL}_PER_MIN`` | 1200 / 600 / 30 / 10 | per-process limits, 0 disables |
| ``NETPULSE_DATA_DIR``, ``NETPULSE_LLM_PROVIDER``, … | see ``netpulse/config.py`` | dataset and LLM provider |

The API has no anonymous mode: with no configured identities (tokens or OIDC) it refuses to start rather than run
open. Static tokens are for development; production uses OIDC (docs/operations.md).
"""

from __future__ import annotations

import os
from pathlib import Path

from netpulse.api.app import create_app
from netpulse.api.auth import TokenStore
from netpulse.api.oidc import OidcConfig, OidcVerifier, parse_role_map
from netpulse.api.protection import Protection
from netpulse.graph.deps import Deps
from netpulse.observability import apply_tracing_policy, configure_logging, get_logger
from netpulse.persistence.store import PersistentStore
from netpulse.service import InvestigationService

DEFAULT_DB = Path(".netpulse/netpulse.db")
log = get_logger("api")


def _build_authenticator() -> TokenStore | OidcVerifier:
    mode = os.environ.get("NETPULSE_AUTH_MODE", "tokens").strip().lower()
    if mode == "oidc":
        return _build_oidc()
    if mode != "tokens":
        raise RuntimeError(f"NETPULSE_AUTH_MODE must be 'oidc' or 'tokens', not {mode!r}")
    log.warning("static bearer tokens are for development; use NETPULSE_AUTH_MODE=oidc in production")
    return _build_static_tokens()


def _build_oidc() -> OidcVerifier:
    env = os.environ.get
    missing = [v for v in ("NETPULSE_OIDC_ISSUER", "NETPULSE_OIDC_AUDIENCE", "NETPULSE_OIDC_ROLE_MAP") if not env(v)]
    if missing:
        raise RuntimeError(f"NETPULSE_AUTH_MODE=oidc needs {', '.join(missing)}")
    try:
        config = OidcConfig(
            issuer=env("NETPULSE_OIDC_ISSUER", ""),
            audience=env("NETPULSE_OIDC_AUDIENCE", ""),
            role_map=parse_role_map(env("NETPULSE_OIDC_ROLE_MAP", "")),
            jwks_url=env("NETPULSE_OIDC_JWKS_URL") or None,
            role_claim=env("NETPULSE_OIDC_ROLE_CLAIM", "groups"),
            name_claim=env("NETPULSE_OIDC_NAME_CLAIM", "sub"),
        )
    except ValueError as exc:
        raise RuntimeError(f"invalid OIDC configuration: {exc}") from exc
    return OidcVerifier(config)


def _build_static_tokens() -> TokenStore:
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
    return tokens


def build_app():
    configure_logging()
    apply_tracing_policy()
    authenticator = _build_authenticator()
    audit_key = os.environ.get("NETPULSE_AUDIT_KEY")
    if not audit_key:
        raise RuntimeError(
            "NETPULSE_AUDIT_KEY is not set. It keys the audit hash chain and must stay outside the database: "
            "python -c 'import secrets; print(secrets.token_urlsafe(48))'"
        )
    db = Path(os.environ.get("NETPULSE_DB", DEFAULT_DB))
    store = PersistentStore(db, audit_key=audit_key)
    service = InvestigationService(Deps.default(), store)
    workers = int(os.environ.get("NETPULSE_API_WORKERS", "4"))
    docs = os.environ.get("NETPULSE_API_DOCS", "false").strip().lower() in {"1", "true", "yes", "on"}
    # Production (oidc) refuses plain http by default; static-token development mode does not.
    protection = Protection.from_env(require_https_default=authenticator.mode == "oidc")
    if not protection.require_https:
        log.warning("plain http is allowed; set NETPULSE_REQUIRE_HTTPS=true behind TLS in production")
    return create_app(service, authenticator, max_workers=workers, docs=docs, protection=protection)


def __getattr__(name: str):
    """``uvicorn netpulse.api.main:app`` resolves ``app`` here, so importing this module has no side effects."""
    if name == "app":
        global _app
        if _app is None:
            _app = build_app()
        return _app
    raise AttributeError(name)


_app = None
