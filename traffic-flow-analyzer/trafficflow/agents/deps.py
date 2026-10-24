from __future__ import annotations

from dataclasses import dataclass, field

from trafficflow.data.feed import Feed


@dataclass
class TrafficDeps:
    """Per-run dependencies. Tools reach data only through the feed; evidence is recorded here."""

    feed: Feed
    evidence: dict[str, dict] = field(default_factory=dict)

    def record(self, kind: str, payload: dict) -> str:
        eid = f"EV-{len(self.evidence) + 1:03d}"
        self.evidence[eid] = {"kind": kind, **payload}
        return eid
