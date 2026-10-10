"""Bearer-token authentication for the API.

Tokens map to a ``Principal`` (reviewer id + role). This is a minimal
scheme for a demo: tokens are configured by the deployer (environment
variable or a dict passed directly in tests), not issued or rotated by
NetPulse itself. There is no session state and no token expiry.

Role model (distinct from, but aligned with, the policy roles in
``netpulse.models.ROLE_RANK``):

| Role | Can read | Can submit incidents | Can approve/reject/request more investigation |
|---|---|---|---|
| ``viewer`` | yes | no | no |
| ``operator`` | yes | yes | up to ``operator``-level decisions |
| ``senior_operator`` | yes | yes | any decision |

A client's claimed role or reviewer identity in a request **body** is never
trusted. The principal always comes from the token.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

API_ROLE_RANK = {"viewer": 0, "operator": 1, "senior_operator": 2}
ApiRole = Literal["viewer", "operator", "senior_operator"]


@dataclass(frozen=True)
class Principal:
    token: str
    name: str
    role: ApiRole


class TokenStore:
    """In-memory token → Principal map. Built from env (api/main.py) or injected directly in tests."""

    def __init__(self, tokens: dict[str, Principal]) -> None:
        self._tokens = tokens

    @classmethod
    def parse(cls, spec: str) -> TokenStore:
        """Parse ``token:role:name,token2:role2:name2`` (as set in ``NETPULSE_API_TOKENS``)."""
        tokens: dict[str, Principal] = {}
        for entry in filter(None, (e.strip() for e in spec.split(","))):
            parts = entry.split(":")
            if len(parts) != 3 or parts[1] not in API_ROLE_RANK:
                raise ValueError(f"invalid token spec {entry!r}; expected token:role:name")
            token, role, name = parts
            tokens[token] = Principal(token=token, name=name, role=role)  # type: ignore[arg-type]
        return cls(tokens)

    def resolve(self, token: str) -> Principal | None:
        return self._tokens.get(token)


_bearer = HTTPBearer(auto_error=False)


def principal_dependency(store: TokenStore):
    """Build a FastAPI dependency that resolves the caller's ``Principal`` from the bearer token."""

    def get_principal(credentials: HTTPAuthorizationCredentials | None = Security(_bearer)) -> Principal:
        if credentials is None:
            raise HTTPException(status_code=401, detail="missing bearer token")
        principal = store.resolve(credentials.credentials)
        if principal is None:
            raise HTTPException(status_code=401, detail="invalid bearer token")
        return principal

    return get_principal


def require_role(minimum: ApiRole):
    """Dependency factory: 403 unless the caller's role is at least ``minimum``."""

    def check(principal: Principal) -> Principal:
        if API_ROLE_RANK[principal.role] < API_ROLE_RANK[minimum]:
            raise HTTPException(status_code=403, detail=f"{minimum} role or higher required")
        return principal

    return check
