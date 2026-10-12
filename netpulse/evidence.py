"""Evidence ID allocation.

IDs look like ``ev-<kind>-NNNN``. The allocator continues numbering from an
existing registry, so a second investigation round can never reuse an ID and
trip the immutable-evidence reducer.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from netpulse.models import EvidenceItem

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


def _content_key(item: Mapping[str, Any] | EvidenceItem) -> str:
    data = item.model_dump(mode="json") if isinstance(item, EvidenceItem) else item
    return json.dumps({k: v for k, v in data.items() if k != "evidence_id"}, sort_keys=True, default=str)


class EvidenceIdAllocator:
    """Allocates IDs and, via ``intern``, reuses the ID of an identical item already recorded.

    A second investigation round re-derives the same topology, history and runbook facts. Recording them
    again under new IDs would let one fact count as several pieces of support in the confidence rules.
    """

    def __init__(self, existing: Iterable[str] | Mapping[str, Mapping[str, Any] | EvidenceItem] = ()) -> None:
        self._next: dict[str, int] = {}
        self._by_content: dict[str, str] = (
            {_content_key(item): eid for eid, item in existing.items()} if isinstance(existing, Mapping) else {}
        )
        for evidence_id in existing:
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

    def intern(self, kind: str, build: Callable[[str], EvidenceItem]) -> EvidenceItem:
        """Build an item with a fresh ID, or return the already-recorded identical item (same ID)."""
        item = build(self.next(kind))
        key = _content_key(item)
        known = self._by_content.get(key)
        if known is not None:
            self._next[kind] -= 1  # the fresh ID was not used
            return build(known)
        self._by_content[key] = item.evidence_id
        return item
