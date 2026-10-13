"""Optional Gemini embeddings with a persistent cache (ADR-0011). No network: the
Gemini client is replaced by a fake, and a concept embedder stands in for semantics."""

import logging
from dataclasses import replace
from types import SimpleNamespace
from typing import ClassVar

import numpy as np
import pytest

from opspilot.adk import embeddings as E
from opspilot.adk.embeddings import GEMINI_MIN_COSINE, GeminiEmbedder, retrieval_for
from opspilot.config import EmbeddingConfig, Settings
from opspilot.core import knowledge
from opspilot.core.embedding_cache import CachedEmbedder, EmbeddingCache, cache_key
from opspilot.core.retrieval import EmbeddingUnavailable, HashingEmbedder, HybridRetriever

DOCS = ["crc errors on fabric link", "packet loss on uplink", "printer toner low"]
CONCEPTS = [
    {"loss", "drops", "dropped", "discards"},
    {"crc", "errors", "corruption"},
    {"printer", "toner", "ink"},
]


class ConceptEmbedder:
    """Maps synonyms onto shared dimensions, so a paraphrase scores like the original."""

    name = "concepts"

    def __init__(self):
        self.calls: list[tuple[str, list[str]]] = []

    def _vectors(self, texts):
        mat = np.zeros((len(texts), len(CONCEPTS)), dtype=np.float32)
        for row, text in enumerate(texts):
            for col, words in enumerate(CONCEPTS):
                mat[row, col] = len(words & set(text.lower().split()))
            norm = np.linalg.norm(mat[row])
            mat[row] /= norm or 1.0
        return mat

    def embed(self, texts):
        self.calls.append(("document", list(texts)))
        return self._vectors(texts)

    def embed_queries(self, texts):
        self.calls.append(("query", list(texts)))
        return self._vectors(texts)


class Failing:
    name = "failing"

    def __init__(self, fail_documents=True, fail_queries=True):
        self.fail_documents, self.fail_queries = fail_documents, fail_queries
        self.good = ConceptEmbedder()

    def embed(self, texts):
        if self.fail_documents:
            raise EmbeddingUnavailable("no network")
        return self.good.embed(texts)

    def embed_queries(self, texts):
        if self.fail_queries:
            raise EmbeddingUnavailable("no network")
        return self.good.embed_queries(texts)


@pytest.fixture(autouse=True)
def fresh_caches():
    knowledge._RETRIEVERS.clear()
    retrieval_for.cache_clear()
    yield
    knowledge._RETRIEVERS.clear()
    retrieval_for.cache_clear()


# --- the semantic signal ------------------------------------------------------------


def test_hashing_vectors_miss_a_paraphrase_that_a_semantic_embedder_finds():
    query = "dropped frames"
    assert HybridRetriever(DOCS).search(query) == []
    hits = HybridRetriever(DOCS, ConceptEmbedder(), min_cosine=0.5).search(query)
    assert [(h.index, h.matched_by) for h in hits] == [(1, ("vector",))]


def test_queries_use_the_query_side_of_an_asymmetric_embedder():
    emb = ConceptEmbedder()
    HybridRetriever(DOCS, emb, min_cosine=0.5).search("dropped frames")
    assert [role for role, _ in emb.calls] == ["document", "query"]
    assert emb.calls[1][1] == ["dropped frames"]


def test_symmetric_embedders_still_work():
    hashing = HybridRetriever(DOCS, HashingEmbedder())
    assert hashing.search("fabric crc")[0].index == 0


def test_unavailable_at_index_time_falls_back_to_keywords_with_a_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="opspilot.retrieval"):
        retriever = HybridRetriever(DOCS, Failing(), min_cosine=0.5)
    assert "vector search disabled" in caplog.text
    hits = retriever.search("fabric crc")
    assert hits[0].index == 0 and hits[0].matched_by == ("keyword",)
    assert retriever.search("dropped frames") == []


def test_unavailable_at_query_time_keeps_keyword_hits(caplog):
    retriever = HybridRetriever(DOCS, Failing(fail_documents=False), min_cosine=0.5)
    with caplog.at_level(logging.WARNING, logger="opspilot.retrieval"):
        hits = retriever.search("fabric crc")
    assert "query not embedded" in caplog.text
    assert hits[0].index == 0 and hits[0].matched_by == ("keyword",)


# --- cache ------------------------------------------------------------------------------


@pytest.fixture
def cache(tmp_path):
    return EmbeddingCache(tmp_path / "nested" / "embeddings.db")


def test_second_lookup_is_served_from_the_cache(cache):
    inner = ConceptEmbedder()
    cached = CachedEmbedder(inner, cache)
    first = cached.embed(DOCS)
    again = cached.embed(DOCS)
    assert len(inner.calls) == 1 and np.allclose(first, again)


def test_only_missing_texts_reach_the_inner_embedder_and_order_is_kept(cache):
    inner = ConceptEmbedder()
    cached = CachedEmbedder(inner, cache)
    cached.embed(DOCS[:1])
    out = cached.embed([DOCS[1], DOCS[0], DOCS[1]])
    assert inner.calls[1][1] == [DOCS[1]]  # deduplicated, cached text skipped
    assert np.allclose(out, inner._vectors([DOCS[1], DOCS[0], DOCS[1]]))


def test_documents_and_queries_are_cached_separately(cache):
    inner = ConceptEmbedder()
    cached = CachedEmbedder(inner, cache)
    cached.embed(["dropped frames"])
    cached.embed_queries(["dropped frames"])
    assert [role for role, _ in inner.calls] == ["document", "query"]
    assert cache_key("n", "document", "x") != cache_key("n", "query", "x")


def test_cache_survives_a_restart_and_answers_without_the_network(tmp_path):
    path = tmp_path / "e.db"
    CachedEmbedder(ConceptEmbedder(), EmbeddingCache(path)).embed(DOCS)
    down = Failing()
    down.name = "concepts"  # same embedder identity, but the network is gone
    offline = CachedEmbedder(down, EmbeddingCache(path))
    assert offline.embed(DOCS).shape == (3, len(CONCEPTS))


def test_a_failed_fetch_caches_nothing_partial(cache):
    cached = CachedEmbedder(Failing(), cache)
    with pytest.raises(EmbeddingUnavailable):
        cached.embed(DOCS)
    assert cache.get_many([cache_key("failing", "document", t) for t in DOCS]) == {}


def test_embedders_with_different_names_do_not_share_vectors(cache):
    a, b = ConceptEmbedder(), ConceptEmbedder()
    b.name = "other-model@768"
    CachedEmbedder(a, cache).embed(DOCS)
    CachedEmbedder(b, cache).embed(DOCS)
    assert len(a.calls) == len(b.calls) == 1


def test_a_damaged_row_is_treated_as_a_miss(cache):
    inner = ConceptEmbedder()
    cached = CachedEmbedder(inner, cache)
    key = cache_key("concepts", "document", DOCS[0])
    with cache._connect() as db:
        db.execute("INSERT INTO embeddings VALUES (?, ?, ?)", (key, 99, b"\x00\x01"))
    assert cached.embed(DOCS[:1]).shape == (1, len(CONCEPTS))
    assert len(inner.calls) == 1


def test_the_cache_stores_vectors_and_hashes_not_text(cache):
    CachedEmbedder(ConceptEmbedder(), cache).embed(["secret runbook text"])
    with cache._connect() as db:
        dump = b"".join(bytes(str(row), "utf8") for row in db.execute("SELECT * FROM embeddings"))
    assert b"secret runbook text" not in dump


# --- Gemini embedder against a fake client -----------------------------------------------


class FakeModels:
    def __init__(self, dims=4, error=None, drop_one=False, wrong_dims=False):
        self.dims, self.error, self.drop_one, self.wrong_dims = dims, error, drop_one, wrong_dims
        self.calls: list[SimpleNamespace] = []

    def embed_content(self, *, model, contents, config):
        self.calls.append(
            SimpleNamespace(
                model=model,
                contents=list(contents),
                task=config.task_type,
                dims=config.output_dimensionality,
                timeout=config.http_options.timeout,
            )
        )
        if self.error:
            raise self.error
        rows = [
            SimpleNamespace(
                values=[float(len(c)) + 1, 2.0, 3.0, 4.0, 5.0][: self.dims + self.wrong_dims]
            )
            for c in contents
        ]
        return SimpleNamespace(embeddings=rows[:-1] if self.drop_one else rows)


def gemini(**fake):
    models = FakeModels(**fake)
    return GeminiEmbedder(
        "gemini-embedding-001", 4, 30.0, client=SimpleNamespace(models=models)
    ), models


def test_documents_and_queries_use_their_task_types_and_the_configured_shape():
    emb, models = gemini()
    emb.embed(["a runbook"])
    emb.embed_queries(["a question"])
    doc, query = models.calls
    assert (doc.task, query.task) == ("RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY")
    assert doc.model == "gemini-embedding-001" and doc.dims == 4 and doc.timeout == 30_000


def test_large_corpora_are_sent_in_batches():
    emb, models = gemini()
    out = emb.embed([f"doc {i}" for i in range(70)])
    assert [len(c.contents) for c in models.calls] == [32, 32, 6] and out.shape == (70, 4)


def test_returned_vectors_are_unit_length():
    emb, _ = gemini()
    assert np.allclose(np.linalg.norm(emb.embed(["x", "yy"]), axis=1), 1.0)


def test_secrets_are_redacted_before_text_leaves_the_process():
    emb, models = gemini()
    emb.embed(["rotate the key: api_key=hunter2-super-secret on the leaf"])
    sent = models.calls[0].contents[0]
    assert "hunter2" not in sent and "rotate the key" in sent


@pytest.mark.parametrize(
    "fault",
    [
        {"error": RuntimeError("503 with details that must not leak")},
        {"drop_one": True},
        {"wrong_dims": True},
    ],
)
def test_api_failures_and_bad_responses_become_embedding_unavailable(fault):
    emb, _ = gemini(**fault)
    with pytest.raises(EmbeddingUnavailable) as err:
        emb.embed(["a", "b"])
    assert "must not leak" not in str(err.value)


def test_no_client_is_created_until_the_network_is_needed(monkeypatch, tmp_path):
    created = []

    def boom(*a, **k):
        created.append(1)
        raise ValueError("Missing key inputs argument")

    monkeypatch.setattr("google.genai.Client", boom)
    emb = GeminiEmbedder("gemini-embedding-001", 4)
    assert created == []
    with pytest.raises(EmbeddingUnavailable, match="no Gemini client"):
        emb.embed(["x"])
    # A fully cached corpus never gets that far.
    cache = EmbeddingCache(tmp_path / "e.db")
    warm = ConceptEmbedder()
    CachedEmbedder(warm, cache).embed(DOCS)
    cold = CachedEmbedder(emb, cache)
    cold.name = warm.name  # share the namespace the cache was warmed under
    assert cold.embed(DOCS).shape[0] == 3 and created == [1]


# --- configuration ------------------------------------------------------------------------


def test_hashing_is_the_default_and_changes_nothing():
    assert EmbeddingConfig().provider == "hashing"
    assert retrieval_for(EmbeddingConfig()) == (None, None)


def test_gemini_config_builds_a_cached_embedder_with_its_own_cutoff(tmp_path):
    cfg = EmbeddingConfig("gemini", dims=256, cache_path=tmp_path / "c.db")
    embedder, cutoff = retrieval_for(cfg, 12.0)
    assert isinstance(embedder, CachedEmbedder) and embedder.name == "gemini-embedding-001@256"
    assert isinstance(embedder.inner, GeminiEmbedder) and embedder.inner.timeout_s == 12.0
    assert cutoff == GEMINI_MIN_COSINE
    assert retrieval_for(replace(cfg, min_cosine=0.8))[1] == 0.8
    assert (tmp_path / "c.db").exists()


def test_embedding_settings_come_from_the_environment(monkeypatch, tmp_path):
    for name in ("EMBEDDER", "EMBEDDING_MODEL", "EMBEDDING_DIMS", "EMBEDDING_CACHE"):
        monkeypatch.delenv(f"OPSPILOT_{name}", raising=False)
    assert Settings.from_env().embedding == EmbeddingConfig()
    monkeypatch.setenv("OPSPILOT_EMBEDDER", "Gemini")
    monkeypatch.setenv("OPSPILOT_EMBEDDING_MODEL", "m-1")
    monkeypatch.setenv("OPSPILOT_EMBEDDING_DIMS", "1536")
    monkeypatch.setenv("OPSPILOT_EMBEDDING_CACHE", str(tmp_path / "x.db"))
    monkeypatch.setenv("OPSPILOT_EMBEDDING_MIN_COSINE", "0.7")
    cfg = Settings.from_env().embedding
    assert (cfg.provider, cfg.model, cfg.dims, cfg.min_cosine) == ("gemini", "m-1", 1536, 0.7)
    assert cfg.cache_path == tmp_path / "x.db" and cfg.label == "m-1@1536"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"provider": "openai"}, "embedder must be"),
        ({"dims": 0}, "dims"),
        ({"dims": 4096}, "dims"),
        ({"min_cosine": 1.5}, "min_cosine"),
    ],
)
def test_invalid_embedding_settings_are_rejected(kwargs, message):
    with pytest.raises(ValueError, match=message):
        EmbeddingConfig(**kwargs)


# --- knowledge search and investigations --------------------------------------------------


def test_knowledge_search_uses_the_supplied_embedder(ds_factory):
    ds = ds_factory("C03")
    emb = ConceptEmbedder()
    knowledge.search(ds, "packet loss crc errors leaf uplink", "runbook", embedder=emb)
    assert [role for role, _ in emb.calls] == ["document", "query"]
    assert len(emb.calls[0][1]) == len(ds.documents)


def test_default_search_is_unchanged_and_indexes_are_kept_apart(ds_factory):
    ds = ds_factory("C03")
    query = "packet loss crc errors leaf uplink"
    before = knowledge.search(ds, query, "runbook")
    knowledge.search(ds, query, "runbook", embedder=ConceptEmbedder(), min_cosine=0.5)
    assert knowledge.search(ds, query, "runbook") == before
    assert len(knowledge._RETRIEVERS) == 2


def test_reading_a_document_never_builds_an_index(ds_factory):
    ds = ds_factory("C03")
    knowledge.get_document(ds, "RB-002")
    knowledge.document_evidence(ds, "RB-002")
    knowledge.suspicious_documents(ds)
    assert knowledge._RETRIEVERS == {}


class FakeGemini(ConceptEmbedder):
    """Stands in for GeminiEmbedder in the factory; records every network-side call."""

    instances: ClassVar[list] = []

    def __init__(self, model, dims, timeout_s=60.0):
        super().__init__()
        self.model, self.dims, self.timeout_s = model, dims, timeout_s
        self.name = f"{model}@{dims}"
        self.hashing = HashingEmbedder(dims)
        FakeGemini.instances.append(self)

    def _vectors(self, texts):
        return self.hashing.embed(texts)


@pytest.fixture
def gemini_settings(settings, tmp_path, monkeypatch):
    FakeGemini.instances = []
    monkeypatch.setattr(E, "GeminiEmbedder", FakeGemini)
    cfg = EmbeddingConfig("gemini", dims=128, cache_path=tmp_path / "emb.db", min_cosine=0.2)
    return replace(settings, embedding=cfg)


async def test_investigation_runs_with_the_semantic_option(run_case, gemini_settings):
    baseline = (await run_case("C03")).report
    out = await run_case("C03", settings_override=gemini_settings)
    assert out.report.status == baseline.status
    assert FakeGemini.instances and any(c[0] == "query" for c in FakeGemini.instances[0].calls)


async def test_second_process_reuses_the_cache_and_needs_no_embedding_calls(
    run_case, gemini_settings
):
    await run_case("C03", settings_override=gemini_settings)
    first = FakeGemini.instances[0]
    assert any(role == "document" for role, _ in first.calls)
    knowledge._RETRIEVERS.clear()  # as if the process had restarted
    retrieval_for.cache_clear()
    out = await run_case("C03", settings_override=gemini_settings)
    again = FakeGemini.instances[1]
    assert again.calls == [] and out.report.status.value == "investigated"


async def test_investigation_survives_the_embedding_service_being_down(
    run_case, gemini_settings, monkeypatch, caplog
):
    baseline = (await run_case("C03")).report
    monkeypatch.setattr(
        FakeGemini, "embed", lambda self, texts: (_ for _ in ()).throw(EmbeddingUnavailable("down"))
    )
    monkeypatch.setattr(
        FakeGemini,
        "embed_queries",
        lambda self, texts: (_ for _ in ()).throw(EmbeddingUnavailable("down")),
    )
    with caplog.at_level(logging.WARNING, logger="opspilot.retrieval"):
        out = await run_case("C03", settings_override=gemini_settings)
    assert "vector search disabled" in caplog.text
    assert out.report.status == baseline.status


async def test_evaluation_records_which_embedder_was_used(settings, gemini_settings):
    from opspilot.eval.harness import run_eval

    assert (await run_eval(settings, ["C01"]))["config"]["embedder"] == "hashing"
    assert (await run_eval(gemini_settings, ["C01"]))["config"]["embedder"] == (
        "gemini-embedding-001@128"
    )
