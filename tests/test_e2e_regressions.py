"""Regressions found by Phase 11 end-to-end runs. Each test names the failure it guards against."""

import threading
from datetime import UTC, datetime

from langgraph.checkpoint.base import empty_checkpoint

from netpulse.persistence.store import PersistentStore


def test_audit_and_checkpoint_writes_do_not_corrupt_each_other(tmp_path):
    """Live API run: concurrent jobs failed with 'cannot commit - no transaction is active' because the audit
    log and SqliteSaver shared one connection under two different locks."""
    store = PersistentStore(tmp_path / "s.db")
    errors: list[str] = []

    def audit(i: int) -> None:
        for n in range(150):
            try:
                store.audit(f"inc-{i}", "tick", "t", {"n": n}, datetime.now(UTC))
            except Exception as exc:  # noqa: BLE001 - collected and asserted below
                errors.append(repr(exc))

    def checkpoint(i: int) -> None:
        for n in range(150):
            try:
                cfg = {"configurable": {"thread_id": f"inc-{i}", "checkpoint_ns": ""}}
                store.checkpointer.put(cfg, empty_checkpoint(), {"source": "loop", "step": n, "parents": {}}, {})
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

    threads = [threading.Thread(target=audit, args=(i,)) for i in range(4)]
    threads += [threading.Thread(target=checkpoint, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(store.audit_log("inc-0")) == 150


# ---------------------------------------------------------------- evidence duplication (second round)

import json  # noqa: E402

from netpulse.evidence import EvidenceIdAllocator  # noqa: E402
from netpulse.models import EvidenceItem  # noqa: E402
from tests.graph_helpers import CASES, shared_deps  # noqa: E402


def _item(eid: str, summary: str = "Blast radius of node fw-1") -> EvidenceItem:
    return EvidenceItem(evidence_id=eid, source="topology", summary=summary, entity_ids=["fw-1"])


def test_allocator_reuses_id_for_identical_content_and_not_for_different_content():
    first = EvidenceIdAllocator({}).intern("topo", lambda eid: _item(eid))
    registry = {first.evidence_id: first.model_dump(mode="json")}
    alloc = EvidenceIdAllocator(registry)
    again = alloc.intern("topo", lambda eid: _item(eid))
    other = alloc.intern("topo", lambda eid: _item(eid, "Blast radius of node fw-2"))
    assert again.evidence_id == first.evidence_id
    assert other.evidence_id != first.evidence_id
    assert alloc.next("topo") == "ev-topo-0003"  # the reused ID did not burn a number


def _run_case(case_id: str) -> dict:
    from eval.investigation_benchmark import run_case

    return run_case(CASES[case_id], shared_deps())


def test_second_investigation_round_does_not_duplicate_evidence():
    """case-05/14/17 run two rounds. Identical facts were re-recorded under new IDs and counted as extra support."""
    for case_id in ("case-05", "case-14", "case-17"):
        state = _run_case(case_id)
        assert state["investigation_rounds"] == 2
        contents = [
            json.dumps({k: v for k, v in item.items() if k != "evidence_id"}, sort_keys=True)
            for item in state["evidence_references"].values()
        ]
        assert len(contents) == len(set(contents)), case_id


# ---------------------------------------------------------------- hypothesis citations


def test_hypotheses_cite_only_topology_evidence_about_their_own_entities():
    """case-10 (two simultaneous faults): the link hypothesis cited the firewall's blast radius."""
    state = _run_case("case-10")
    registry = state["evidence_references"]
    assert len(state["hypotheses"]) == 2
    for hyp in state["hypotheses"]:
        own = {hyp["suspected_root_entity"], *hyp["affected_components"]}
        for eid in hyp["supporting_evidence"]:
            item = registry[eid]
            if item["source"] == "topology" and item["entity_ids"]:
                assert set(item["entity_ids"]) & own, (hyp["hypothesis_id"], eid)


# ---------------------------------------------------------------- recommendations


def test_delayed_telemetry_recommends_collector_health_check():
    """case-06: 62% of samples visible with data-quality evidence, but coverage was above the 'gap' threshold."""
    state = _run_case("case-06")
    assert state["telemetry_window"]["gap_entities"] == []
    assert state["telemetry_window"]["data_quality_evidence_ids"]
    actions = {a["catalog_id"] for a in state["recommended_actions"]}
    assert "check_collector_health" in actions
    assert all(a["executed"] is False for a in state["recommended_actions"])
