"""Knowledge search over runbooks, technical documents and incident reports.

Every hit passes through `security.isolate_untrusted` before it can reach an
agent. Deprecated/superseded documents and suspected injections are flagged.
"""

from __future__ import annotations

from opspilot.contracts.evidence import Evidence, EvidenceKind
from opspilot.contracts.knowledge import DocumentHit, SearchResult
from opspilot.core.dataset import Dataset, Document
from opspilot.core.errors import InvalidArgument, NotFound
from opspilot.core.retrieval import HybridRetriever
from opspilot.core.security import detect_injection, isolate_untrusted, normalize

DOC_TYPES = ("runbook", "tech_doc", "incident_report")
MAX_QUERY = 300
MAX_TOP_K = 8


_INDEX: dict[str, tuple[list[Document], HybridRetriever]] = {}


def _index(ds: Dataset) -> tuple[list[Document], HybridRetriever]:
    key = str(ds.path)
    if key not in _INDEX:
        docs = ds.documents
        texts = [f"{d.title}\n{' '.join(d.tags)}\n{' '.join(d.components)}\n{d.body}" for d in docs]
        _INDEX[key] = (docs, HybridRetriever(texts))
    return _INDEX[key]


def _flags(doc: Document, iso_flags: list[str]) -> list[str]:
    flags = []
    if doc.status == "deprecated":
        flags.append("deprecated")
    if doc.superseded_by:
        flags.append("superseded")
    flags += [f for f in iso_flags if f not in flags]
    return flags


def _evidence(doc: Document, flags: list[str]) -> Evidence:
    kind = EvidenceKind.INCIDENT if doc.doc_type == "incident_report" else EvidenceKind.DOCUMENT
    summary = f"{doc.doc_id} ({doc.doc_type}, {doc.status}, updated {doc.updated}): {doc.title}"
    if flags:
        summary += f" [flags: {', '.join(flags)}]"
    return Evidence.make(
        kind,
        summary,
        f"document:{doc.doc_id}",
        entity_id=doc.doc_id,
        data={
            "doc_type": doc.doc_type,
            "status": doc.status,
            "updated": doc.updated,
            "flags": ",".join(flags),
            "root_cause_category": doc.root_cause_category,
            "components": ",".join(doc.components),
        },
        trusted=False,
    )


def search(ds: Dataset, query: str, doc_type: str, top_k: int = 5) -> SearchResult:
    query = normalize(query or "")
    if not query:
        raise InvalidArgument("query must not be empty")
    if len(query) > MAX_QUERY:
        raise InvalidArgument(f"query longer than {MAX_QUERY} characters")
    if doc_type not in DOC_TYPES:
        raise InvalidArgument(f"doc_type must be one of {DOC_TYPES}")
    top_k = max(1, min(int(top_k), MAX_TOP_K))
    docs, retriever = _index(ds)
    allowed = {i for i, d in enumerate(docs) if d.doc_type == doc_type}
    hits, evidence, flagged = [], [], []
    for h in retriever.search(query, top_k=top_k, allowed=allowed):
        doc = docs[h.index]
        snippet, iso = isolate_untrusted(doc.doc_id, doc.body, max_chars=600)
        flags = _flags(doc, iso)
        ev = _evidence(doc, flags)
        evidence.append(ev)
        if {"suspected_injection", "deprecated", "superseded"} & set(flags):
            flagged.append(doc.doc_id)
        hits.append(
            DocumentHit(
                doc_id=doc.doc_id,
                doc_type=doc.doc_type,
                title=doc.title,
                status=doc.status,
                updated=doc.updated,
                score=h.score,
                matched_by=list(h.matched_by),
                snippet=snippet,
                flags=flags,
                superseded_by=doc.superseded_by,
                root_cause_category=doc.root_cause_category,
                components=doc.components,
                evidence_id=ev.evidence_id,
            )
        )
    return SearchResult(
        query=query, doc_type=doc_type, hits=hits, flagged=flagged, evidence=evidence
    )


def get_document(ds: Dataset, doc_id: str) -> DocumentHit:
    docs, _ = _index(ds)
    for doc in docs:
        if doc.doc_id == doc_id:
            body, iso = isolate_untrusted(doc.doc_id, doc.body, max_chars=1500)
            flags = _flags(doc, iso)
            ev = _evidence(doc, flags)
            return DocumentHit(
                doc_id=doc.doc_id,
                doc_type=doc.doc_type,
                title=doc.title,
                status=doc.status,
                updated=doc.updated,
                score=1.0,
                matched_by=[],
                snippet=body,
                flags=flags,
                superseded_by=doc.superseded_by,
                root_cause_category=doc.root_cause_category,
                components=doc.components,
                evidence_id=ev.evidence_id,
            )
    raise NotFound(f"document {doc_id!r} not found")


def document_evidence(ds: Dataset, doc_id: str) -> Evidence:
    docs, _ = _index(ds)
    for doc in docs:
        if doc.doc_id == doc_id:
            _, iso = isolate_untrusted(doc.doc_id, doc.body)
            return _evidence(doc, _flags(doc, iso))
    raise NotFound(doc_id)


def suspicious_documents(ds: Dataset) -> list[str]:
    docs, _ = _index(ds)
    return [d.doc_id for d in docs if detect_injection(d.body)]
