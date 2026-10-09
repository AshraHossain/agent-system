"""Per-investigation runtime context that tools resolve from session state.

The model never chooses the dataset: tools look up the dataset bound to the
session's `investigation_id` (or `dataset_id`) — values written by the runner
and the deterministic intake agent, not by an LLM.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from opspilot.config import Settings
from opspilot.core.dataset import Dataset, open_dataset
from opspilot.core.errors import DataUnavailable
from opspilot.core.topology import Topology


@dataclass(frozen=True)
class RunFaults:
    """Fault injection for failure testing (never enabled by default)."""

    model_timeout: frozenset[str] = frozenset()
    model_quota: frozenset[str] = frozenset()
    telemetry_unavailable: bool = False
    # Model faults hit only each agent's first call (a transient outage).
    transient: bool = False

    @classmethod
    def from_spec(cls, spec: dict | None) -> RunFaults:
        spec = spec or {}
        return cls(
            model_timeout=frozenset(spec.get("model_timeout", [])),
            model_quota=frozenset(spec.get("model_quota", [])),
            telemetry_unavailable=bool(spec.get("telemetry_unavailable", False)),
            transient=bool(spec.get("transient", False)),
        )


class _UnavailableTelemetry(Dataset):
    def telemetry(self, *args, **kwargs):
        raise DataUnavailable("telemetry backend unavailable (injected fault)")

    def metric_catalog(self):
        raise DataUnavailable("telemetry backend unavailable (injected fault)")


@dataclass
class Runtime:
    settings: Settings
    dataset: Dataset
    faults: RunFaults = field(default_factory=RunFaults)

    @property
    def topology(self) -> Topology:
        if not hasattr(self, "_topo"):
            self._topo = Topology.from_dataset(self.dataset)
        return self._topo


_LOCK = threading.Lock()
_REGISTRY: dict[str, Runtime] = {}


def register(
    investigation_id: str, settings: Settings, dataset_id: str, faults: RunFaults | None = None
) -> Runtime:
    faults = faults or RunFaults()
    ds = open_dataset(dataset_id, settings.data_dir)
    if faults.telemetry_unavailable:
        ds = _UnavailableTelemetry(ds.path, ds.dataset_id)
    rt = Runtime(settings=settings, dataset=ds, faults=faults)
    with _LOCK:
        _REGISTRY[investigation_id] = rt
    return rt


def unregister(investigation_id: str) -> None:
    with _LOCK:
        _REGISTRY.pop(investigation_id, None)


def resolve(state) -> Runtime:
    """Find the runtime for a session state; lazily create one for dev-UI sessions."""
    inv = state.get("investigation_id")
    with _LOCK:
        if inv and inv in _REGISTRY:
            return _REGISTRY[inv]
    settings = Settings.from_env()
    dataset_id = state.get("dataset_id") or settings.default_dataset
    return register(inv or f"adhoc-{dataset_id}", settings, dataset_id)
