import numpy as np
import pytest

from opspilot.core import knowledge
from opspilot.core.errors import InvalidArgument, NotFound
from opspilot.core.retrieval import BM25, HashingEmbedder, HybridRetriever, tokenize


def test_tokenize_keeps_identifiers_and_parts():
    toks = tokenize("Packet loss on leaf-3 uplink, CRC errors!")
    assert {"leaf-3", "leaf", "3", "crc", "packet", "loss"} <= set(toks)
    assert "on" not in toks


def test_bm25_prefers_matching_doc():
    bm = BM25([tokenize("crc errors optic"), tokenize("printer toner")])
    s = bm.scores(tokenize("crc errors"))
    assert s[0] > 0 and s[1] == 0


def test_hashing_embedder_is_deterministic_and_normalised():
    e = HashingEmbedder(dims=256)
    a, b = e.embed(["dns latency"]), e.embed(["dns latency"])
    assert np.allclose(a, b) and np.isclose(np.linalg.norm(a[0]), 1.0)


def test_hybrid_fusion_marks_sources():
    r = HybridRetriever(["crc errors on fabric link", "dns resolver latency", "printer toner"])
    hits = r.search("fabric crc", top_k=3)
    assert hits[0].index == 0 and "keyword" in hits[0].matched_by
    assert all(h.index != 2 for h in hits)


def test_runbook_search_flags_deprecated(ds_factory):
    res = knowledge.search(ds_factory("C03"), "packet loss crc errors leaf uplink", "runbook")
    ids = [h.doc_id for h in res.hits]
    assert ids[0] == "RB-002"
    rb3 = next(h for h in res.hits if h.doc_id == "RB-003")
    assert {"deprecated", "superseded"} <= set(rb3.flags) and rb3.superseded_by == "RB-002"
    assert "RB-003" in res.flagged
    assert all(e.trusted is False for e in res.evidence)


def test_malicious_document_is_quarantined(ds_factory):
    res = knowledge.search(ds_factory("C10"), "packet loss leaf-1 uplink", "tech_doc")
    bad = next(h for h in res.hits if h.doc_id == "DOC-666")
    assert "suspected_injection" in bad.flags
    assert "shutdown" not in bad.snippet and "AIza" not in bad.snippet
    assert bad.snippet.startswith("<<untrusted_document id=DOC-666>>")
    assert knowledge.suspicious_documents(ds_factory("C10")) == ["DOC-666"]
    assert knowledge.suspicious_documents(ds_factory("C02")) == []


def test_incident_search_returns_inc_evidence(ds_factory):
    res = knowledge.search(
        ds_factory("C08"), "packet loss lnk-l4-s2 search latency", "incident_report"
    )
    assert res.hits[0].doc_id == "INC-2025-0412"
    assert res.evidence[0].evidence_id.startswith("EV-INC-")


@pytest.mark.parametrize("q,t", [("", "runbook"), ("x" * 400, "runbook"), ("dns", "secrets")])
def test_search_argument_validation(ds_factory, q, t):
    with pytest.raises(InvalidArgument):
        knowledge.search(ds_factory("C01"), q, t)


def test_get_document(ds_factory):
    doc = knowledge.get_document(ds_factory("C01"), "RB-004")
    assert "resolver" in doc.snippet
    with pytest.raises(NotFound):
        knowledge.get_document(ds_factory("C01"), "../../etc/passwd")
