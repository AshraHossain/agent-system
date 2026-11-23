"""Bearer-token guard for the HTTP API (off unless a token is configured).

One static shared secret: enough to keep a demo instance off the open internet,
not a production auth system (no per-client identity, scopes or revocation).
The server holds a SHA-256 digest of the token, never the plaintext, and
compares it in constant time.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Sequence

MAX_TOKEN_CHARS = 512


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def verify_token(token: str, digest: str) -> bool:
    return hmac.compare_digest(hash_token(token), digest)


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


class BearerTokenAuth:
    """Require `Authorization: Bearer <token>` or `X-API-Key: <token>` on every HTTP request.

    Plain ASGI, not `BaseHTTPMiddleware`: it must run before FastAPI's own routing
    (a bad token never reaches a handler) and must not buffer the streaming
    `/stream` response. Non-HTTP scopes (there are none here beyond `lifespan`)
    pass straight through.
    """

    def __init__(self, app, *, digest: str):
        self.app = app
        self.digest = digest

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = [(k.lower(), v.decode("latin-1")) for k, v in scope["headers"]]
        auth_values = [v for k, v in headers if k == b"authorization"]
        api_key_values = [v for k, v in headers if k == b"x-api-key"]
        token = extract_request_token(auth_values, api_key_values)
        if token is not None and verify_token(token, self.digest):
            await self.app(scope, receive, send)
            return
        body = b'{"detail":"missing or invalid bearer token or API key"}'
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"www-authenticate", b"Bearer"),
            (b"cache-control", b"no-store"),
        ]
        await send({"type": "http.response.start", "status": 401, "headers": headers})
        await send({"type": "http.response.body", "body": body})
