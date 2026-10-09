"""Phase 5: topology traversal, blast radius, localization, change context."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from netpulse.data.schemas import EventRecord, MaintenanceWindow
from netpulse.data.store import DEFAULT_DATA_DIR, DatasetStore
from netpulse.errors import ToolInputError
from netpulse.models import EvidenceSource
from netpulse.topology.analysis import (
    LocalizationQuery,
    TopologyGraph,
    blast_radius_evidence,
    event_evidence,
    localization_evidence,
    maintenance_evidence,
)
from netpulse.topology.model import Topology


@pytest.fixture(scope="module")
def graph() -> TopologyGraph:
    return TopologyGraph(DatasetStore(DEFAULT_DATA_DIR).topology)


def _topology(**overrides) -> dict:
    base = {
        "provenance": "synthetic",
        "nodes": [
            {"node_id": "up", "role": "upstream", "tier": "external", "monitored": False},
            {"node_id": "r1", "role": "router", "tier": "core", "monitored": True},
        ],
        "links": [{"link_id": "l1", "a": "up", "b": "r1", "capacity_gbps": 10}],
        "services": [
            {"service_id": "s1", "criticality": "high", "customers": 5, "depends_on": ["r1"], "probe_path": ["l1"]}
        ],
    }
    return {**base, **overrides}


def test_topology_validation_accepts_consistent_model():
    Topology(**_topology())


@pytest.mark.parametrize(
    "overrides",
    [
        {"links": [{"link_id": "l1", "a": "up", "b": "ghost", "capacity_gbps": 10}]},
        {
            "services": [
                {"service_id": "s1", "criticality": "high", "customers": 1, "depends_on": ["x"], "probe_path": []}
            ]
        },
        {"services": [{"service_id": "r1", "criticality": "high", "customers": 1, "depends_on": [], "probe_path": []}]},
        {"provenance": "production"},
    ],
)
def test_topology_validation_rejects_inconsistencies(overrides):
    with pytest.raises(ValidationError):
        Topology(**_topology(**overrides))


def test_shortest_path_to_upstream(graph):
    path = graph.shortest_path("acc-1", "isp-transit")
    assert path.nodes[0] == "acc-1" and path.nodes[-1] == "isp-transit"
    assert len(path.links) == len(path.nodes) - 1
    with pytest.raises(ToolInputError):
        graph.shortest_path("acc-1", "link-pe-1-fw-1")


def test_blast_radius_distinguishes_redundant_and_single_homed(graph):
    redundant = graph.blast_radius("link-fw-1-core-1")
    assert redundant.redundant and redundant.isolated_nodes == []
    assert redundant.affected_services == ["enterprise_vpn", "voip"]
    single = graph.blast_radius("link-agg-3-acc-6")
    assert not single.redundant and single.isolated_nodes == ["acc-6"] and single.affected_services == ["video"]
    node = graph.blast_radius("agg-3")
    assert node.isolated_nodes == ["acc-5", "acc-6"] and node.customers_at_risk == 9800


def test_blast_radius_input_errors(graph):
    with pytest.raises(ToolInputError):
        graph.blast_radius("voip")
    with pytest.raises(ToolInputError):
        graph.blast_radius("does-not-exist")


def test_localization_finds_shared_link_for_correlated_symptoms(graph):
    result = graph.localize(LocalizationQuery(symptomatic_entities=["link-fw-1-core-1", "voip", "enterprise_vpn"]))
    top = result.candidates[0]
    assert top.entity_id == "link-fw-1-core-1" and top.has_own_symptom and len(top.explains) == 3
    assert result.minimal_cover == ["link-fw-1-core-1"] and result.corroborated
    assert "not proof of causation" in result.caveat


def test_localization_reports_multiple_causes_via_cover(graph):
    result = graph.localize(
        LocalizationQuery(symptomatic_entities=["fw-1", "link-agg-1-acc-2", "voip", "enterprise_vpn"])
    )
    assert result.minimal_cover == ["fw-1", "link-agg-1-acc-2"] and result.unexplained_by_cover == []


def test_localization_without_corroboration_is_flagged(graph):
    result = graph.localize(LocalizationQuery(symptomatic_entities=["internet"], max_candidates=20))
    assert not result.corroborated and result.minimal_cover == []
    assert result.unexplained_by_cover == ["internet"]
    upstream = next(c for c in result.candidates if c.entity_id == "isp-transit")
    assert not upstream.monitored
    evidence = localization_evidence(result, "ev-topo-0001", top=20)
    assert evidence.summary.startswith("Topology localization: NO candidate shows an anomaly of its own")
    assert "Unmonitored candidates (no telemetry, cannot be verified): isp-transit" in evidence.summary


def test_localization_rejects_unknown_symptoms(graph):
    with pytest.raises(ToolInputError):
        graph.localize(LocalizationQuery(symptomatic_entities=["nope"]))


def test_related_events_expand_links_to_endpoints(graph):
    t0 = datetime(2026, 3, 2, 12, 0, tzinfo=UTC)
    events = [
        EventRecord(
            timestamp=t0 - timedelta(minutes=30),
            entity_id="agg-4",
            event_type="config_change",
            severity="notice",
            message="ACL update",
        ),
        EventRecord(
            timestamp=t0 - timedelta(hours=5),
            entity_id="agg-4",
            event_type="config_change",
            severity="notice",
            message="old",
        ),
        EventRecord(timestamp=t0, entity_id="acc-1", event_type="ntp", severity="info", message="unrelated"),
    ]
    hits = graph.related_events(events, ["link-core-2-agg-4"], t0, t0 + timedelta(hours=1))
    assert [e.message for e in hits] == ["ACL update"]
    assert event_evidence(hits[0], "ev-evt-0001").source == EvidenceSource.EVENT_LOG


def test_overlapping_maintenance_matches_shared_endpoint(graph):
    t0 = datetime(2026, 3, 2, 12, 0, tzinfo=UTC)
    window = MaintenanceWindow(
        entity_id="link-core-1-core-2", start=t0, end=t0 + timedelta(hours=3), ticket="CHG-1", description="optic swap"
    )
    assert graph.overlapping_maintenance([window], ["link-core-2-agg-1"], t0, t0 + timedelta(hours=1)) == [window]
    assert graph.overlapping_maintenance([window], ["acc-7"], t0, t0 + timedelta(hours=1)) == []
    assert graph.overlapping_maintenance([window], ["core-1"], t0 - timedelta(days=1), t0 - timedelta(hours=1)) == []
    assert maintenance_evidence(window, "ev-mnt-0001").source_ref == "maintenance:CHG-1"


def test_topology_evidence_is_trusted_and_structural(graph):
    item = blast_radius_evidence(graph.blast_radius("core-1"), "ev-topo-0001")
    assert item.trusted and item.source == EvidenceSource.TOPOLOGY and "dns-1" in item.summary
