"""Bearer-token and X-API-Key authentication primitives for the shared-use API (ADR-0013).

Tokens are random secrets handed to clients; the server is configured with their
SHA-256 digests only, so a leaked config, environment dump or process listing does
not leak a credential. A fast hash is appropriate because tokens are 256-bit random
values from `new_token`, never human-chosen passwords.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from collections.abc import Sequence

DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_TOKEN_CHARS = 512


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def parse_digests(raw: str | None) -> tuple[str, ...]:
    """Digests from a comma- or whitespace-separated setting; raises on a malformed entry."""
    entries = [e for e in re.split(r"[,\s]+", raw or "") if e]
    digests: list[str] = []
    for position, entry in enumerate(entries, start=1):
        digest = entry.lower()
        if not DIGEST_RE.match(digest):
            raise ValueError(
                f"API token entry {position} is not a SHA-256 hex digest (64 hex characters); "
                "create one with `opspilot token`"
            )
        if digest not in digests:
            digests.append(digest)
    return tuple(digests)


def verify_token(token: str, digests: Sequence[str]) -> bool:
    """Constant-time check against every configured digest (no early exit)."""
    if not token or not digests:
        return False
    presented = hash_token(token)
    matched = False
    for digest in digests:
        matched |= hmac.compare_digest(presented, digest)
    return matched


def parse_bearer(authorization_values: Sequence[str]) -> str | None:
    """The token from exactly one well-formed `Authorization: Bearer <token>` header."""
    if len(authorization_values) != 1:
        return None
    scheme, separator, token = authorization_values[0].partition(" ")
    if scheme.lower() != "bearer" or separator != " ":
        return None
    if not token or " " in token or len(token) > MAX_TOKEN_CHARS:
        return None
    return token


def parse_api_key(api_key_values: Sequence[str]) -> str | None:
    """The token from exactly one well-formed `X-API-Key: <token>` header."""
    if len(api_key_values) != 1:
        return None
    token = api_key_values[0].strip()
    if not token or any(ch.isspace() for ch in token) or len(token) > MAX_TOKEN_CHARS:
        return None
    return token


def extract_request_token(
    authorization_values: Sequence[str],
    api_key_values: Sequence[str] = (),
) -> str | None:
    """Extract a single credential from either `Authorization: Bearer` or `X-API-Key`.

    Refuses ambiguous requests that supply both headers simultaneously.
    """
    if authorization_values and api_key_values:
        return None
    if authorization_values:
        return parse_bearer(authorization_values)
    if api_key_values:
        return parse_api_key(api_key_values)
    return None
