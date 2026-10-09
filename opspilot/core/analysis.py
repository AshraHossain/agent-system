"""Deterministic hypothesis candidate generation and historical comparison.

Rules map anomaly *signatures* on a component to hypothesis categories.
Historical incidents are attached as references only — similarity is never
counted as supporting evidence. Confidence is categorical (ADR-0006).
"""

from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel, Field

from opspilot.contracts.evidence import Evidence
from opspilot.contracts.findings import Hypothesis, HypothesisCategory
from opspilot.contracts.request import TimeWindow
from opspilot.contracts.telemetry import Anomaly, AnomalySummary, BaselineComparison
from opspilot.core import knowledge
from opspilot.core.dataset import Dataset
from opspilot.core.errors import NotFound
from opspilot.core.telemetry import compare_to_baseline, summarize_anomalies
from opspilot.core.topology import SVC, Topology, svc_name

C = HypothesisCategory
CONF_RANK = {"strong": 0, "moderate": 1, "weak": 2}
DEVICE_TYPES = {"firewall", "load_balancer", "switch", "router", "wan_router"}
DEVICE_METRICS = ("cpu_pct", "mem_pct", "session_util_pct")
SIGNATURE = {
    C.LINK_CONGESTION: "utilization_pct",
    C.LINK_PHYSICAL: "error_rate",
    C.DEVICE_SATURATION: "cpu_pct",
    C.DNS: "query_latency_ms",
    C.WAN: "latency_ms",
}

CHECKS = {
    C.LINK_CONGESTION: [
        "Show utilization and output-discard counters on both ends of {c} ({ifaces})",
        "Review QoS queue drop counters for the best-effort queue on {c}",
        "Identify top talkers on {c} from flow telemetry for the investigation window",
        "Compare ECMP load distribution between {c} and its sibling uplink",
    ],
    C.LINK_PHYSICAL: [
        "Show CRC/FCS and input error counters on both ends of {c} ({ifaces})",
        "Check optic digital diagnostics (rx/tx power, temperature) for {ifaces}",
        "Compare interface loss with active probe loss on {c}",
        "Confirm utilization on {c} is normal to rule out congestion",
    ],
    C.DEVICE_SATURATION: [
        "Show CPU, memory and session-table utilization on {c}",
        "Review new-connection rate and top source prefixes on {c}",
        "Correlate {c} load with ingress service latency",
    ],
    C.DNS: [
        "Show query latency and query rate on {c}",
        "Check resolver CPU and cache hit ratio on {c}",
        "Identify clients with abnormal query rates towards {c}",
    ],
    C.WAN: [
        "Show latency, jitter and loss on {c}",
        "Compare {c} measurements with carrier-side probe results",
        "Check WAN router CPU and interface errors",
    ],
    C.TELEMETRY_ARTIFACT: [
        "Compare interface loss counters with active probe loss on {c}",
        "Check the device firmware version for known counter-reporting issues",
        "Verify whether services whose paths cross {c} show any impact",
    ],
    C.APPLICATION: [
        "Review the application latency breakdown and recent deployments for {c}",
        "Confirm there are no network anomalies on the dependency path of {c}",
    ],
    C.UNKNOWN: [
        "Collect additional telemetry for {c} before drawing conclusions",
        "Review recent changes affecting {c}",
    ],
}


class CandidateSet(BaseModel):
    hypotheses: list[Hypothesis]
    affected_services: list[str]
    unexplained_observations: list[str]
    additional_evidence_requests: list[str]
    anomaly_count: int
    evidence: list[Evidence] = Field(default_factory=list)


class IncidentComparison(BaseModel):
    incident_id: str
    title: str
    incident_category: str | None
    similarities: list[str]
    differences: list[str]
    signature_matches_current: bool
    note: str = "Historical similarity is context, not proof of causation."
    evidence: list[Evidence] = Field(default_factory=list)


class _Ctx:
    def __init__(self, ds: Dataset, window: TimeWindow, baseline: TimeWindow):
        self.ds, self.window, self.baseline = ds, window, baseline
        self.extra: list[Evidence] = []
        self._cache: dict[tuple[str, str], BaselineComparison | None] = {}

    def cmp(self, entity: str, metric: str) -> BaselineComparison | None:
        key = (entity, metric)
        if key not in self._cache:
            try:
                res = compare_to_baseline(self.ds, entity, metric, self.window, self.baseline)
                self.extra.extend(res.evidence)
                self._cache[key] = res
            except NotFound:
                self._cache[key] = None
        return self._cache[key]


def _missing(cmp: BaselineComparison | None) -> bool:
    return cmp is not None and cmp.verdict == "insufficient_data"


def _conf(kinds: int, contradicted: bool, gaps: bool) -> tuple[str, str]:
    if contradicted:
        return "weak", "weak: at least one measurement contradicts this hypothesis"
    if kinds >= 2 and not gaps:
        return (
            "strong",
            f"strong: {kinds} independent metrics support it, none contradict, data complete",
        )
    if kinds >= 2:
        return (
            "moderate",
            f"moderate: {kinds} supporting metrics but telemetry for the component is incomplete",
        )
    if kinds == 1:
        return "moderate", "moderate: a single supporting metric and no contradicting measurements"
    return "weak", "weak: only indirect support (topology inference or historical similarity)"


def generate_candidates(
    ds: Dataset,
    topo: Topology,
    window: TimeWindow,
    baseline: TimeWindow,
    summary: AnomalySummary | None = None,
) -> CandidateSet:
    summary = summary or summarize_anomalies(ds, window, baseline)
    ctx = _Ctx(ds, window, baseline)
    verdict_by_ev = {a.evidence_id: a.verdict for a in summary.anomalies}
    by_entity: dict[str, dict[str, Anomaly]] = defaultdict(dict)
    for a in summary.anomalies:
        by_entity[a.entity_id][a.metric] = a
    gaps_by_entity: dict[str, list[str]] = defaultdict(list)
    for d in summary.data_quality:
        gaps_by_entity[d.entity_id].append(d.evidence_id)
    affected = sorted(svc_name(e) for e in by_entity if e.startswith(SVC))
    requests: list[str] = []
    for d in summary.data_quality:
        what = (
            f"{d.metric} for {d.entity_id}" if d.metric else f"current telemetry for {d.entity_id}"
        )
        requests.append(f"Collect {what} ({d.issue})")

    raw: list[dict] = []

    def add(cat, comp, statement, support, contra=(), kinds=None, cap=None):
        support = [s for s in support if s]
        comp_info = ds.components.get(comp, {})
        related = {comp, *comp_info.get("endpoints", [])}
        gap_ids = [g for e in related for g in gaps_by_entity.get(e, [])]
        n = kinds if kinds is not None else len(support)
        conf, why = _conf(n, bool(contra), bool(gap_ids))
        if cap and CONF_RANK[conf] < CONF_RANK[cap]:
            conf, why = cap, f"{cap}: capped — {why.split(': ', 1)[1]}; needs confirmation"
        recovered = [e for e in support if e in verdict_by_ev]
        if recovered and all(verdict_by_ev[e] == "recovered" for e in recovered):
            statement = f"Transient episode, now recovered: {statement}"
            if CONF_RANK[conf] < CONF_RANK["moderate"]:
                conf = "moderate"
            why += "; all supporting measurements have returned to normal (recovered episode)"
        raw.append(
            dict(
                category=cat,
                component=comp,
                statement=statement,
                support=support,
                contra=list(contra),
                conf=conf,
                why=why,
                gaps=gap_ids,
            )
        )

    for entity, m in sorted(by_entity.items()):
        if entity.startswith(SVC):
            continue
        etype = ds.components.get(entity, {}).get("type", "unknown")
        ev = {k: a.evidence_id for k, a in m.items()}
        if etype == "link":
            endpoints = ds.components[entity]["endpoints"]
            is_wan = any(ds.components.get(e, {}).get("type") == "wan_router" for e in endpoints)
            loss, probe = m.get("packet_loss_pct"), m.get("probe_loss_pct")
            err, util, lat = m.get("error_rate"), m.get("utilization_pct"), m.get("latency_ms")
            err_cmp = ctx.cmp(entity, "error_rate")
            err_missing = err_cmp is not None and err_cmp.verdict == "insufficient_data"
            if is_wan and (lat or loss or probe):
                add(
                    C.WAN,
                    entity,
                    f"Degradation of WAN link {entity} (latency/loss) is slowing services with "
                    "external dependencies.",
                    [ev.get("latency_ms"), ev.get("packet_loss_pct"), ev.get("probe_loss_pct")],
                )
                continue
            if util:
                contra = [ev["error_rate"]] if err else []
                add(
                    C.LINK_CONGESTION,
                    entity,
                    f"Congestion on {entity} (utilization {util.peak:.0f}% peak vs "
                    f"{util.baseline_mean:.0f}% baseline) is adding latency and tail-drop loss.",
                    [
                        ev["utilization_pct"],
                        ev.get("latency_ms"),
                        ev.get("packet_loss_pct"),
                        ev.get("probe_loss_pct"),
                    ],
                    contra,
                )
            if err:
                util_cmp = ctx.cmp(entity, "utilization_pct")
                contra = [ev["utilization_pct"]] if util else []
                add(
                    C.LINK_PHYSICAL,
                    entity,
                    f"Physical-layer degradation on {entity} (CRC/input errors with packet loss) "
                    "is dropping packets for flows hashed onto it.",
                    [ev["error_rate"], ev.get("packet_loss_pct"), ev.get("probe_loss_pct")],
                    contra,
                )
                if util_cmp is not None and util_cmp.verdict == "normal":
                    raw[-1]["support"].append(util_cmp.evidence[0].evidence_id)
                    raw[-1]["why"] += "; utilization normal (congestion unlikely)"
            elif not util and loss and not probe:
                probe_cmp = ctx.cmp(entity, "probe_loss_pct")
                normals = [
                    c.evidence[0].evidence_id
                    for c in (probe_cmp, err_cmp)
                    if c is not None and c.verdict == "normal"
                ]
                if probe_cmp is not None and probe_cmp.verdict == "normal":
                    add(
                        C.TELEMETRY_ARTIFACT,
                        entity,
                        f"Interface counters on {entity} report loss that active probes do not "
                        "confirm; this may be a counter/telemetry artifact.",
                        [ev["packet_loss_pct"], *normals],
                        kinds=2,
                        cap="moderate",
                    )
                    add(
                        C.LINK_PHYSICAL,
                        entity,
                        f"Real but unconfirmed packet loss on {entity}.",
                        [ev["packet_loss_pct"]],
                        normals,
                    )
                else:
                    add(
                        C.UNKNOWN,
                        entity,
                        f"Unexplained packet loss on {entity}.",
                        [ev["packet_loss_pct"]],
                    )
            elif not util and (loss or probe) and err_missing:
                add(
                    C.LINK_PHYSICAL,
                    entity,
                    f"Packet loss on {entity} confirmed by probes with normal utilization suggests "
                    "a physical-layer fault, but error counters are missing.",
                    [ev.get("packet_loss_pct"), ev.get("probe_loss_pct")],
                )
            elif (
                not util
                and not err
                and (loss or probe)
                and _missing(ctx.cmp(entity, "utilization_pct"))
            ):
                normal_err = [
                    c.evidence[0].evidence_id
                    for c in (err_cmp,)
                    if c is not None and c.verdict == "normal"
                ]
                add(
                    C.LINK_CONGESTION,
                    entity,
                    f"Loss and latency on {entity} with normal error counters point to "
                    "congestion, but utilization telemetry is missing.",
                    [
                        ev.get("packet_loss_pct"),
                        ev.get("probe_loss_pct"),
                        ev.get("latency_ms"),
                        *normal_err,
                    ],
                    cap="moderate",
                )
            elif not util and (loss or probe or lat):
                add(
                    C.UNKNOWN,
                    entity,
                    f"Unexplained degradation on {entity}.",
                    [ev.get("packet_loss_pct"), ev.get("probe_loss_pct"), ev.get("latency_ms")],
                )
        elif etype in DEVICE_TYPES and any(k in m for k in DEVICE_METRICS):
            add(
                C.DEVICE_SATURATION,
                entity,
                f"Resource saturation on {entity} "
                f"({', '.join(k for k in DEVICE_METRICS if k in m)}) "
                "is slowing traffic that traverses it.",
                [ev.get(k) for k in DEVICE_METRICS],
            )
        elif etype == "dns_server":
            add(
                C.DNS,
                entity,
                f"DNS resolver {entity} is degraded (query latency/CPU), slowing name resolution "
                "for dependent services.",
                [ev.get("query_latency_ms"), ev.get("cpu_pct")],
            )
        else:
            add(
                C.UNKNOWN,
                entity,
                f"Anomalies on {entity} ({etype}) with no matching rule.",
                list(ev.values()),
            )

    explained_any: set[str] = set()
    hyps: list[Hypothesis] = []
    for r in raw:
        explained = sorted(s for s in affected if topo.exposure(s, r["component"]) is not None)
        r["explained"] = explained
        if r["category"] not in (C.TELEMETRY_ARTIFACT, C.UNKNOWN):
            explained_any.update(explained)

    if not raw and affected:
        # No network anomaly at all: attribute to the most upstream degraded services
        # (those that do not themselves depend on another degraded service).
        deps_of = {
            s: {svc_name(n) for n in topo.closure(f"{SVC}{s}") if n.startswith(SVC)}
            for s in affected
        }
        roots = [s for s in affected if not (deps_of[s] & set(affected))] or affected
        for s in roots:
            downstream = [t for t in affected if s in deps_of[t]]
            sev = [a.evidence_id for a in by_entity[f"{SVC}{s}"].values()]
            add(
                C.APPLICATION,
                s,
                f"{s} is degraded with no network anomaly on its documented dependency path"
                + (f"; {', '.join(downstream)} depend(s) on it" if downstream else "")
                + ". The cause is likely application-side.",
                sev,
                cap="moderate",
            )
            raw[-1]["explained"] = sorted([s, *downstream])

    incidents = [d for d in ds.documents if d.doc_type == "incident_report"]
    hist_evidence: list[Evidence] = []
    for r in raw:
        comp = r["component"]
        related = {comp, *ds.components.get(comp, {}).get("endpoints", [])}
        refs, alts = [], []
        for inc in incidents:
            inc_ev = knowledge.document_evidence(ds, inc.doc_id)
            if "suspected_injection" in str(inc_ev.data.get("flags", "")):
                hist_evidence.append(inc_ev)  # registered so the report can quarantine it
                continue
            overlap = related & set(inc.components)
            if inc.root_cause_category == r["category"].value and (overlap or not inc.components):
                refs.append(inc.doc_id)
                hist_evidence.append(knowledge.document_evidence(ds, inc.doc_id))
            elif (
                overlap
                and inc.root_cause_category
                and inc.root_cause_category != r["category"].value
            ):
                sig = SIGNATURE.get(HypothesisCategory(inc.root_cause_category))
                now = by_entity.get(comp, {})
                missing = f" ({sig} is not anomalous now)" if sig and sig not in now else ""
                alts.append(
                    f"{inc.doc_id} on {', '.join(sorted(overlap))} was "
                    f"{inc.root_cause_category}{missing}"
                )
                hist_evidence.append(knowledge.document_evidence(ds, inc.doc_id))
        r["refs"], r["alts"] = refs, alts

    def sort_key(r):
        crit = sum(1 for a in by_entity.get(r["component"], {}).values() if a.verdict == "critical")
        return (
            CONF_RANK[r["conf"]],
            -len(r["explained"]),
            -crit,
            r["category"].value,
            r["component"],
        )

    raw.sort(key=sort_key)
    for i, r in enumerate(raw, start=1):
        comp_info = ds.components.get(r["component"], {})
        ifaces = ", ".join(comp_info.get("interfaces", [])[:2]) or r["component"]
        checks = [c.format(c=r["component"], ifaces=ifaces) for c in CHECKS[r["category"]]]
        for g in r["gaps"]:
            checks.append(f"Collect the missing/delayed telemetry referenced by {g}")
        alternatives = list(r["alts"])
        for other in raw:
            if other is not r and other["component"] == r["component"]:
                alternatives.append(f"{other['category'].value} on {other['component']}")
        why = r["why"]
        if r["refs"]:
            why += f"; similar past incidents: {', '.join(r['refs'])} (context only)"
        hyps.append(
            Hypothesis(
                hypothesis_id=f"H{i}",
                category=r["category"],
                component_id=r["component"],
                statement=r["statement"],
                supporting_evidence_ids=list(dict.fromkeys(r["support"])),
                contradicting_evidence_ids=list(dict.fromkeys(r["contra"])),
                historical_references=r["refs"],
                explained_services=r["explained"],
                confidence=r["conf"],
                confidence_rationale=why[:500],
                alternative_explanations=alternatives[:5],
                diagnostic_checks=checks[:6],
            )
        )

    unexplained = [
        f"{s} shows latency/errors but has no documented dependency path to any anomalous "
        "component (topology may be incomplete, or the cause is application-side)"
        for s in affected
        if s not in explained_any and any(h.category not in (C.APPLICATION,) for h in hyps)
    ]
    evidence = list(summary.evidence) + ctx.extra + hist_evidence
    return CandidateSet(
        hypotheses=hyps,
        affected_services=affected,
        unexplained_observations=unexplained,
        additional_evidence_requests=requests,
        anomaly_count=len(summary.anomalies),
        evidence=list({e.evidence_id: e for e in evidence}.values()),
    )


def compare_with_incident(
    ds: Dataset, incident_id: str, summary: AnomalySummary
) -> IncidentComparison:
    doc = next(
        (d for d in ds.documents if d.doc_id == incident_id and d.doc_type == "incident_report"),
        None,
    )
    if doc is None:
        raise NotFound(f"incident {incident_id!r} not found")
    anomalous = {a.entity_id for a in summary.anomalies}
    metrics_by_entity: dict[str, set[str]] = defaultdict(set)
    for a in summary.anomalies:
        metrics_by_entity[a.entity_id].add(a.metric)
    expanded = set(anomalous)
    for e in anomalous:
        expanded.update(ds.components.get(e, {}).get("endpoints", []))
    sims, diffs = [], []
    shared = sorted(expanded & set(doc.components))
    if shared:
        sims.append(f"same components involved: {', '.join(shared)}")
    else:
        diffs.append("no overlapping components with current anomalies")
    svc_now = {svc_name(e) for e in anomalous if e.startswith(SVC)}
    if svc_now & set(doc.components + doc.tags):
        sims.append(
            f"same services affected: {', '.join(sorted(svc_now & set(doc.components + doc.tags)))}"
        )
    matches = False
    if doc.root_cause_category:
        sig = SIGNATURE.get(HypothesisCategory(doc.root_cause_category))
        if sig:
            matches = any(
                sig in metrics_by_entity.get(e, set()) for e in expanded & set(doc.components)
            ) or any(sig in ms for ms in metrics_by_entity.values())
            (sims if matches else diffs).append(
                f"incident root cause was {doc.root_cause_category}; its signature metric "
                f"'{sig}' is {'also' if matches else 'NOT'} anomalous now"
            )
    ev = knowledge.document_evidence(ds, doc.doc_id)
    return IncidentComparison(
        incident_id=doc.doc_id,
        title=doc.title,
        incident_category=doc.root_cause_category,
        similarities=sims,
        differences=diffs,
        signature_matches_current=matches,
        evidence=[ev],
    )
