"""OIDC bearer-token authentication: the API validates JWTs issued by your identity provider.

NetPulse never sees passwords and issues no tokens. A caller presents an access token (a JWT) from the IdP; this
module checks, in this order:

1. the header algorithm is on the asymmetric allowlist (``none`` and ``HS*`` are refused, which closes the classic
   "sign with the public key as an HMAC secret" attack) and a ``kid`` is present;
2. the signature, against the key the IdP publishes under that ``kid`` (the key is never taken from the token);
3. ``iss`` equals the configured issuer, ``aud`` contains the configured audience, ``exp`` has not passed
   (with a small clock-skew leeway), ``nbf`` has been reached, and ``sub`` is present;
4. at least one of the caller's group/role claims maps to a NetPulse role. No mapping means 403, never "viewer".

The audit identity (``Principal.name``) comes from the configured name claim, ``sub`` by default, because it is stable;
emails and display names can be reassigned.

Limits: access is as fresh as the token's lifetime. NetPulse does not call the IdP's introspection or revocation
endpoints, so disable a user at the IdP and keep access-token lifetimes short (minutes). Signing keys are cached
(``jwks_ttl``) and refetched at most once per ``min_refresh`` seconds when an unknown ``kid`` appears; if the IdP is
unreachable, cached keys stay usable for ``max_stale`` seconds and then authentication fails closed.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
import jwt
from fastapi import HTTPException

from netpulse.api.auth import API_ROLE_RANK, Principal
from netpulse.observability import get_logger

log = get_logger("auth")

ALLOWED_ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512")
MAX_RESPONSE_BYTES = 1_000_000
_NAME = re.compile(r"^[^\s\x00-\x1f]{1,100}$")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _require_https(url: str, what: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in _LOCAL_HOSTS):
        return
    raise ValueError(f"{what} must be an https URL (plain http is allowed only for localhost): {url!r}")


def parse_role_map(spec: str) -> dict[str, str]:
    """``group-a=operator,group-b=senior_operator`` -> mapping. Rejects unknown roles and empty entries."""
    mapping: dict[str, str] = {}
    for entry in filter(None, (e.strip() for e in spec.split(","))):
        group, sep, role = entry.partition("=")
        group, role = group.strip(), role.strip()
        if not sep or not group or role not in API_ROLE_RANK:
            raise ValueError(f"invalid role mapping {entry!r}; expected <group>=viewer|operator|senior_operator")
        mapping[group] = role
    if not mapping:
        raise ValueError("the OIDC role map is empty: map at least one group to a NetPulse role")
    return mapping


@dataclass(frozen=True)
class OidcConfig:
    issuer: str
    audience: str
    role_map: dict[str, str]
    jwks_url: str | None = None
    role_claim: str = "groups"
    name_claim: str = "sub"
    leeway: int = 60
    jwks_ttl: int = 600
    min_refresh: int = 30
    max_stale: int = 86_400
    http_timeout: float = 5.0

    def __post_init__(self) -> None:
        if not self.issuer or not self.audience:
            raise ValueError("OIDC needs both an issuer and an audience")
        _require_https(self.issuer, "the OIDC issuer")
        if self.jwks_url:
            _require_https(self.jwks_url, "the OIDC JWKS URL")
        if not self.role_map:
            raise ValueError("the OIDC role map is empty")


@dataclass
class _KeyCache:
    keys: dict[str, Any] = field(default_factory=dict)
    fetched_at: float = 0.0
    attempted_at: float = 0.0
    jwks_url: str | None = None


class OidcVerifier:
    """Drop-in for ``TokenStore``: ``resolve(token)`` returns a ``Principal`` or ``None`` (401), or raises 403."""

    mode = "oidc"

    def __init__(
        self,
        config: OidcConfig,
        http: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self._http = http or httpx.Client(timeout=config.http_timeout, follow_redirects=False)
        self._clock = clock
        self._cache = _KeyCache(jwks_url=config.jwks_url)
        self._lock = threading.Lock()

    # ------------------------------------------------------------- keys

    def _get_json(self, url: str) -> dict[str, Any]:
        response = self._http.get(url, headers={"Accept": "application/json"})
        response.raise_for_status()
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ValueError("response from the identity provider is too large")
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("unexpected response from the identity provider")
        return data

    def _discover_jwks_url(self) -> str:
        base = self.config.issuer.rstrip("/")
        doc = self._get_json(f"{base}/.well-known/openid-configuration")
        # Mix-up protection: the discovery document must name the issuer we asked about.
        if str(doc.get("issuer", "")).rstrip("/") != base:
            raise ValueError("discovery document issuer does not match the configured issuer")
        url = str(doc.get("jwks_uri", ""))
        _require_https(url, "the discovered jwks_uri")
        return url

    def _refresh(self, now: float) -> None:
        self._cache.attempted_at = now
        try:
            if self._cache.jwks_url is None:
                self._cache.jwks_url = self._discover_jwks_url()
            jwk_set = jwt.PyJWKSet.from_dict(self._get_json(self._cache.jwks_url))
            keys = {k.key_id: k for k in jwk_set.keys if k.key_id and k.public_key_use in (None, "sig")}
            if not keys:
                raise ValueError("the key set contains no usable signing keys")
            self._cache.keys, self._cache.fetched_at = keys, now
        except Exception as exc:  # noqa: BLE001 - any failure leaves the previous keys in place, bounded by max_stale
            log.warning("could not refresh signing keys", extra={"error_type": type(exc).__name__})

    def _key_for(self, kid: str) -> Any | None:
        now = self._clock()
        with self._lock:
            cache = self._cache
            stale = now - cache.fetched_at > self.config.jwks_ttl
            unknown = kid not in cache.keys
            if (stale or unknown) and now - cache.attempted_at >= self.config.min_refresh:
                self._refresh(now)
            if now - cache.fetched_at > self.config.max_stale:
                cache.keys = {}  # fail closed rather than trust keys we cannot confirm are still current
            return cache.keys.get(kid)

    # ----------------------------------------------------------- verify

    def _claims(self, token: str) -> dict[str, Any] | None:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            return self._reject("malformed")
        alg, kid = header.get("alg"), header.get("kid")
        if alg not in ALLOWED_ALGORITHMS:
            return self._reject("algorithm_not_allowed")
        if not isinstance(kid, str) or not kid:
            return self._reject("missing_kid")
        key = self._key_for(kid)
        if key is None:
            return self._reject("unknown_kid")
        try:
            return jwt.decode(
                token,
                key.key,
                algorithms=[alg],
                audience=self.config.audience,
                issuer=self.config.issuer,
                leeway=self.config.leeway,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            return self._reject(type(exc).__name__)

    @staticmethod
    def _reject(reason: str) -> None:
        log.info("bearer token rejected", extra={"reason": reason})
        return None

    def _role(self, claims: dict[str, Any]) -> str | None:
        raw = claims.get(self.config.role_claim)
        values = raw.replace(",", " ").split() if isinstance(raw, str) else raw if isinstance(raw, list) else []
        roles = [self.config.role_map[v] for v in values if isinstance(v, str) and v in self.config.role_map]
        return max(roles, key=API_ROLE_RANK.__getitem__, default=None)

    def resolve(self, token: str) -> Principal | None:
        claims = self._claims(token)
        if claims is None:
            return None
        name = claims.get(self.config.name_claim)
        if not isinstance(name, str) or not _NAME.match(name):
            log.info("bearer token rejected", extra={"reason": "unusable_identity_claim"})
            return None
        role = self._role(claims)
        if role is None:
            log.info("authenticated identity has no NetPulse role", extra={"reason": "no_role_mapping"})
            raise HTTPException(status_code=403, detail="this identity has no NetPulse role")
        return Principal(token="", name=name, role=role)  # type: ignore[arg-type]
