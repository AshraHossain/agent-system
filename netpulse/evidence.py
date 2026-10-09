"""Evidence ID allocation.

IDs look like ``ev-<kind>-NNNN``. The allocator continues numbering from an
existing registry, so a second investigation round can never reuse an ID and
trip the immutable-evidence reducer.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

KINDS = {
    "anom": "detector anomaly",
    "dq": "data quality / coverage",
    "topo": "topology and blast radius",
    "evt": "event log entry",
    "mnt": "maintenance window",
    "rb": "runbook excerpt (untrusted)",
    "hist": "historical incident (untrusted)",
    "rev": "reviewer note",
    "chk": "verifier negative check (no anomaly where a hypothesis requires one)",
}
_ID = re.compile(r"^ev-([a-z]+)-(\d{4})$")


class EvidenceIdAllocator:
    def __init__(self, existing_ids: Iterable[str] = ()) -> None:
        self._next: dict[str, int] = {}
        for evidence_id in existing_ids:
            match = _ID.match(evidence_id)
            if match:
                kind, number = match.group(1), int(match.group(2))
                self._next[kind] = max(self._next.get(kind, 1), number + 1)

    def next(self, kind: str) -> str:
        if kind not in KINDS:
            raise ValueError(f"unknown evidence kind {kind!r}")
        number = self._next.get(kind, 1)
        if number > 9999:
            raise OverflowError(f"evidence kind {kind!r} exhausted")
        self._next[kind] = number + 1
        return f"ev-{kind}-{number:04d}"
