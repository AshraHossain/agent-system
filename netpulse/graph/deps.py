"""Runtime dependencies injected into graph nodes (never stored in, or checkpointed with, state)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from netpulse.config import Settings
from netpulse.data.store import DatasetStore, default_store
from netpulse.detection import DetectionConfig
from netpulse.llm.base import HypothesisGenerator
from netpulse.llm.fallback import FallbackGenerator
from netpulse.llm.heuristic import HeuristicGenerator
from netpulse.llm.ollama import OllamaGenerator
from netpulse.models import Budget
from netpulse.retrieval.retriever import CorpusRetriever
from netpulse.topology.analysis import TopologyGraph


def utc_now() -> datetime:
    return datetime.now(UTC)


def make_generator(
    provider: Literal["ollama", "heuristic"], topology: TopologyGraph, settings: Settings
) -> HypothesisGenerator:
    heuristic = HeuristicGenerator(topology)
    if provider == "heuristic":
        return heuristic
    ollama = OllamaGenerator(settings, timeout_seconds=Budget().llm_timeout_seconds)
    return FallbackGenerator(ollama, heuristic) if settings.llm_fallback else ollama


@dataclass
class Deps:
    store: DatasetStore
    retriever: CorpusRetriever
    topology: TopologyGraph
    generator: HypothesisGenerator
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    clock: Callable[[], datetime] = utc_now

    @classmethod
    def default(
        cls,
        data_dir: Path | None = None,
        generator: HypothesisGenerator | None = None,
        provider: Literal["ollama", "heuristic"] | None = None,
        settings: Settings | None = None,
    ) -> Deps:
        settings = settings or Settings.from_env()
        store = DatasetStore(data_dir) if data_dir else default_store()
        topology = TopologyGraph(store.topology)
        return cls(
            store=store,
            retriever=CorpusRetriever(store.corpus_dir),
            topology=topology,
            generator=generator or make_generator(provider or settings.llm_provider, topology, settings),
        )
