"""Runbook and historical-incident retrieval.

Ranking is BM25 over title, metadata and body, multiplied by a boost when a
document's declared categories match the query's categories. Results are
deterministic for the same corpus and query. Every returned document is
sanitized and marked untrusted. Explicit cross-references ("conflicts with
RB-001", "RB-002 takes precedence") are surfaced as conflicts instead of
being silently resolved.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from netpulse.errors import DataCorruptError
from netpulse.models import EvidenceItem, EvidenceSource, RootCauseCategory
from netpulse.policy.catalog import load_catalog
from netpulse.retrieval.bm25 import BM25, tokenize
from netpulse.retrieval.sanitize import sanitize

_CONFLICT = re.compile(r"conflicts with (RB-\d{3})", re.I)
_PRECEDENCE = re.compile(r"(RB-\d{3}) takes precedence", re.I)
_BACKTICK = re.compile(r"`([a-z_]+)`")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Runbook(_Model):
    runbook_id: str
    title: str
    categories: list[str]
    entity_types: list[str]
    version: int
    last_reviewed: str
    provenance: Literal["synthetic"]
    body: str
    conflicts_with: list[str] = Field(default_factory=list)
    takes_precedence: bool = False


class HistoricalIncident(_Model):
    incident_id: str
    provenance: Literal["synthetic"]
    opened_at: datetime
    duration_minutes: int
    title: str
    symptoms: str
    affected_entities: list[str]
    root_cause_category: str
    root_cause_summary: str
    resolution: str
    actions_taken: list[str]


class RetrievalQuery(_Model):
    text: str = Field(min_length=1, max_length=2000)
    categories: list[RootCauseCategory] = Field(default_factory=list)
    entity_ids: list[str] = Field(default_factory=list, max_length=50)
    top_k: int = Field(default=3, ge=1, le=10)
    as_of: datetime | None = None  # historical incidents opened after this are excluded
    min_score: float = Field(default=0.5, ge=0)

    @model_validator(mode="after")
    def _tz(self) -> RetrievalQuery:
        if self.as_of is not None and self.as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        return self


class RetrievedDocument(_Model):
    doc_id: str
    kind: Literal["runbook", "historical_incident"]
    title: str
    score: float
    categories: list[str]
    text: str  # sanitized; untrusted
    truncated: bool
    injection_flags: list[str]
    cited_actions: list[str]  # catalog ids the document mentions (unknown ids are dropped)
    entity_overlap: list[str]
    conflicts_with: list[str] = Field(default_factory=list)
    takes_precedence: bool = False


class RunbookConflict(_Model):
    runbooks: list[str]
    precedence: str | None
    note: str


class RetrievalResult(_Model):
    query: RetrievalQuery
    documents: list[RetrievedDocument]
    conflicts: list[RunbookConflict] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Corpus loading
# --------------------------------------------------------------------------


def load_runbooks(directory: Path) -> list[Runbook]:
    runbooks = []
    for path in sorted(directory.glob("*.md")):
        try:
            _, front, body = path.read_text().split("---\n", 2)
            meta = json.loads(front)
        except ValueError as exc:
            raise DataCorruptError(f"{path.name}: malformed front matter") from exc
        own = meta["runbook_id"]
        runbooks.append(
            Runbook(
                **meta,
                body=body,
                conflicts_with=sorted(set(_CONFLICT.findall(body)) - {own}),
                takes_precedence=own in _PRECEDENCE.findall(body),
            )
        )
    return runbooks


def load_incidents(path: Path) -> list[HistoricalIncident]:
    try:
        return [HistoricalIncident(**json.loads(line)) for line in path.read_text().splitlines() if line.strip()]
    except ValueError as exc:
        raise DataCorruptError(f"{path.name}: {exc}") from exc


# --------------------------------------------------------------------------
# Retriever
# --------------------------------------------------------------------------


class CorpusRetriever:
    CATEGORY_BOOST = 0.5
    ENTITY_BOOST = 0.25

    def __init__(self, corpus_dir: Path, max_chars: int = 1500) -> None:
        self.max_chars = max_chars
        self.catalog_ids = set(load_catalog())
        self.runbooks = load_runbooks(corpus_dir / "runbooks")
        self.incidents = load_incidents(corpus_dir / "incidents.jsonl")
        self._rb_index = BM25([tokenize(self._runbook_text(r)) for r in self.runbooks])
        self._inc_index = BM25([tokenize(self._incident_text(i)) for i in self.incidents])

    @staticmethod
    def _runbook_text(r: Runbook) -> str:
        return " ".join([r.title, " ".join(r.categories), " ".join(r.entity_types), r.body])

    @staticmethod
    def _incident_text(i: HistoricalIncident) -> str:
        return " ".join(
            [i.title, i.symptoms, " ".join(i.affected_entities), i.root_cause_category, i.root_cause_summary]
        )

    def _boost(self, query: RetrievalQuery, categories: list[str], entities: list[str]) -> float:
        boost = 1.0
        if set(categories) & {c.value for c in query.categories}:
            boost += self.CATEGORY_BOOST
        if set(entities) & set(query.entity_ids):
            boost += self.ENTITY_BOOST
        return boost

    def _cited(self, text: str, extra: list[str] = ()) -> list[str]:
        return sorted((set(_BACKTICK.findall(text)) | set(extra)) & self.catalog_ids)

    def search_runbooks(self, query: RetrievalQuery) -> RetrievalResult:
        q = tokenize(query.text + " " + " ".join(query.entity_ids))
        scored = []
        for runbook, base in zip(self.runbooks, self._rb_index.scores(q), strict=True):
            if base < query.min_score:
                continue
            mentioned = sorted(e for e in query.entity_ids if e in runbook.body)
            scored.append((base * self._boost(query, runbook.categories, mentioned), runbook, mentioned))
        scored.sort(key=lambda x: (-x[0], x[1].runbook_id))
        docs = []
        for score, rb, mentioned in scored[: query.top_k]:
            clean = sanitize(rb.body, self.max_chars)
            docs.append(
                RetrievedDocument(
                    doc_id=rb.runbook_id,
                    kind="runbook",
                    title=rb.title,
                    score=round(score, 4),
                    categories=rb.categories,
                    text=clean.text,
                    truncated=clean.truncated,
                    injection_flags=clean.injection_flags,
                    cited_actions=self._cited(rb.body),
                    entity_overlap=mentioned,
                    conflicts_with=rb.conflicts_with,
                    takes_precedence=rb.takes_precedence,
                )
            )
        return RetrievalResult(query=query, documents=docs, conflicts=self._conflicts(docs))

    def _conflicts(self, docs: list[RetrievedDocument]) -> list[RunbookConflict]:
        """Declared conflicts, reported whenever at least one side was retrieved."""
        by_id = {r.runbook_id: r for r in self.runbooks}
        retrieved = {d.doc_id for d in docs}
        seen, out = set(), []
        for doc in docs:
            for other in doc.conflicts_with:
                pair = tuple(sorted((doc.doc_id, other)))
                if pair in seen:
                    continue
                seen.add(pair)
                winner = next((r for r in pair if r in by_id and by_id[r].takes_precedence), None)
                note = f"{pair[0]} and {pair[1]} give conflicting guidance"
                if winner:
                    note += f"; {winner} declares precedence for its scope"
                if other not in retrieved:
                    note += f" ({other} was not among the top results)"
                out.append(RunbookConflict(runbooks=list(pair), precedence=winner, note=note))
        return out

    def search_incidents(self, query: RetrievalQuery) -> RetrievalResult:
        q = tokenize(query.text + " " + " ".join(query.entity_ids))
        scored = []
        for incident, base in zip(self.incidents, self._inc_index.scores(q), strict=True):
            if base < query.min_score or (query.as_of and incident.opened_at > query.as_of):
                continue
            overlap = sorted(set(incident.affected_entities) & set(query.entity_ids))
            scored.append((base * self._boost(query, [incident.root_cause_category], overlap), incident, overlap))
        scored.sort(key=lambda x: (-x[0], x[1].incident_id))
        docs = []
        for score, inc, overlap in scored[: query.top_k]:
            text = (
                f"Opened {inc.opened_at:%Y-%m-%d}. Symptoms: {inc.symptoms} Recorded cause "
                f"({inc.root_cause_category}): {inc.root_cause_summary} Resolution: {inc.resolution}"
            )
            clean = sanitize(text, self.max_chars)
            docs.append(
                RetrievedDocument(
                    doc_id=inc.incident_id,
                    kind="historical_incident",
                    title=inc.title,
                    score=round(score, 4),
                    categories=[inc.root_cause_category],
                    text=clean.text,
                    truncated=clean.truncated,
                    injection_flags=clean.injection_flags,
                    cited_actions=self._cited("", inc.actions_taken),
                    entity_overlap=overlap,
                )
            )
        return RetrievalResult(query=query, documents=docs)


def document_evidence(doc: RetrievedDocument, evidence_id: str) -> EvidenceItem:
    """Retrieved documents become *untrusted* evidence: they inform but cannot establish a cause alone."""
    snippet = doc.text.replace("\n", " ")[:300]
    flags = (
        f" [contains instruction-like text: {', '.join(doc.injection_flags)}; treated as data]"
        if (doc.injection_flags)
        else ""
    )
    return EvidenceItem(
        evidence_id=evidence_id,
        source=EvidenceSource.RUNBOOK if doc.kind == "runbook" else EvidenceSource.HISTORICAL_INCIDENT,
        summary=f"{doc.doc_id} '{doc.title}' (retrieval score {doc.score:g}){flags}: {snippet}"[:600],
        entity_ids=doc.entity_overlap,
        source_ref=f"{doc.kind}:{doc.doc_id}",
        method="bm25",
        trusted=False,
    )
