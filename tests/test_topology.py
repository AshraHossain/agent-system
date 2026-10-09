import pytest

from opspilot.core.errors import InvalidArgument, NotFound
from opspilot.core.topology import IMPACT_RULES, Edge, Topology


def test_closure_and_paths(topo_factory):
    t = topo_factory("C01")
    deps = t.closure("svc:checkout")
    assert {"leaf-1", "lnk-l1-s1", "spine-1", "fw-1", "lb-1", "svc:auth"} <= deps
    paths = t.paths("svc:checkout", "spine-2")
    assert paths and all(p[0] == "svc:checkout" and p[-1] == "spine-2" for p in paths)


def test_redundancy_and_exposure(topo_factory):
    t = topo_factory("C01")
    assert t.satisfied("svc:checkout", {"lnk-l1-s1"})  # ECMP sibling survives
    assert not t.satisfied("svc:checkout", {"lnk-l1-s1", "lnk-l1-s2"})
    assert t.exposure("checkout", "lnk-l1-s1") == "redundant"
    assert t.exposure("checkout", "fw-1") == "single_point"
    assert t.exposure("checkout", "lnk-l3-s2") == "indirect"  # via auth
    assert t.exposure("analytics", "lnk-l1-s1") is None


def test_blast_radius_rules(topo_factory):
    br = topo_factory("C01").blast_radius("fw-1")
    by = {i.service: i for i in br.impacts}
    assert by["checkout"].impact == IMPACT_RULES[("single_point", 1)] == "critical"
    assert by["video-stream"].impact == "high"
    assert br.impacts[0].impact == "critical"  # sorted by impact
    assert br.evidence and br.rules


def test_blast_radius_unknown_component(topo_factory):
    with pytest.raises(NotFound):
        topo_factory("C01").blast_radius("router-99")


def test_dependencies_shared_and_uncertainty(topo_factory):
    res = topo_factory("C01").dependencies(["payments", "notifications", "ghost"])
    assert "wan-1" in res.shared_components and set(res.shared_components["wan-1"]) == {
        "payments",
        "notifications",
    }
    assert res.unknown_services == ["ghost"]
    assert any("inferred" in u for u in res.uncertainties)
    with pytest.raises(InvalidArgument):
        topo_factory("C01").dependencies([])


def test_lookup_unknown_component_reports_uncertainty(topo_factory):
    info = topo_factory("C01").lookup("mystery-box")
    assert info.known is False and info.evidence[0].data["known"] is False


def test_cycles_are_safe():
    comps = {
        n: {
            "id": n,
            "type": "switch",
            "redundancy_group": None,
            "tier": None,
            "interfaces": [],
            "endpoints": [],
        }
        for n in "abc"
    }
    t = Topology(comps, [Edge("a", "b", None), Edge("b", "c", None), Edge("c", "a", None)])
    assert t.closure("a") == {"b", "c"}
    assert t.satisfied("a", set())
    assert not t.satisfied("a", {"c"})
    assert t.dependents("a") == {"b", "c"}
