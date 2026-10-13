"""Tenant isolation.

Every authenticated identity belongs to one tenant. Incidents are isolated **by key namespace**, not by an
ownership check that each endpoint must remember: a non-default tenant's incident ``x`` is stored under the key
``<tenant>~x``, and a caller can only ever build keys inside their own namespace. Another tenant's ID therefore
resolves to a key that does not exist (404, indistinguishable from "never existed"), and ``~`` is rejected in every
client-supplied ID so nobody can write the separator themselves. The default tenant keeps unprefixed keys, so a
single-tenant deployment is unchanged.

| Tenant | Meaning |
|---|---|
| ``default`` | the only tenant when no tenant is configured (single-tenant mode) |
| any ``[a-z0-9][a-z0-9_-]{0,31}`` | a customer or team; sees only its own incidents |
| ``_platform`` | operators of the service: may verify the audit log; can read or submit **no** incident |

Tenant membership comes from the token: the optional fourth field of a static token (``token:role:name:tenant``) or an
OIDC claim (``NETPULSE_OIDC_TENANT_CLAIM``). It is never read from a request.

What is **not** isolated: the reference corpus (runbooks, past incidents) and the topology are shared read-only
knowledge for all tenants. Telemetry datasets are restricted per tenant by ``NETPULSE_TENANT_DATASETS``; in
multi-tenant mode a tenant with no entry may use no dataset. The audit hash chain is global (one chain), so verifying
it is a platform operation. Logs and the SQLite file contain every tenant's data; protect them as the platform.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_TENANT = "default"
PLATFORM_TENANT = "_platform"
SEPARATOR = "~"
_TENANT = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
_INCIDENT = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def valid_tenant(tenant: str) -> bool:
    return tenant == PLATFORM_TENANT or bool(_TENANT.fullmatch(tenant))


def scope_incident_id(tenant: str, incident_id: str) -> str | None:
    """The storage key for ``incident_id`` inside ``tenant``'s namespace, or None if the ID is not acceptable."""
    if not _INCIDENT.fullmatch(incident_id):
        return None
    return incident_id if tenant == DEFAULT_TENANT else f"{tenant}{SEPARATOR}{incident_id}"


@dataclass(frozen=True)
class DatasetPolicy:
    """Which telemetry datasets each tenant may investigate. Only consulted in multi-tenant mode."""

    allowed: dict[str, frozenset[str]]

    @classmethod
    def parse(cls, spec: str) -> DatasetPolicy:
        """``acme=case-01,case-02;globex=case-03;internal=*``"""
        allowed: dict[str, frozenset[str]] = {}
        for entry in filter(None, (e.strip() for e in spec.split(";"))):
            tenant, sep, datasets = entry.partition("=")
            tenant = tenant.strip()
            names = frozenset(d.strip() for d in datasets.split(",") if d.strip())
            if not sep or not names or not _TENANT.fullmatch(tenant):
                raise ValueError(f"invalid tenant dataset entry {entry!r}; expected <tenant>=<dataset>[,<dataset>...]")
            allowed[tenant] = names
        return cls(allowed)

    def allows(self, tenant: str, dataset_id: str) -> bool:
        names = self.allowed.get(tenant, frozenset())
        return "*" in names or dataset_id in names
