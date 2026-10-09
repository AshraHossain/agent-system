"""Deterministic per-agent policies for `ScriptedLlm`.

Each policy reads only what a real model would see: the rendered instruction
(context blocks) and the tool results of its own turn. These policies are a
*reference behaviour* for offline tests and evaluation; they do not measure
LLM reasoning quality.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from opspilot.adk.models import Final, ToolCalls, Turn

RUNBOOK_KEYWORDS = {
    "link_congestion": ("congestion",),
    "link_physical_degradation": ("crc",),
    "telemetry_artifact": ("crc",),
    "device_resource_saturation": ("resource saturation",),
    "dns_degradation": ("dns",),
    "wan_degradation": ("wan",),
}
INC_RE = re.compile(r"INC-\d{4}-\d{4}")


# ---------------------------------------------------------------- fallbacks
def failed_output(agent: str, reason: str) -> dict:
    """Schema-valid 'failed' output for an agent (used on model errors/budgets)."""
    reason = reason[:300]
    if agent == "telemetry_analyst":
        return {
            "status": "failed",
            "summary": f"Telemetry analysis failed: {reason}",
            "time_window": "",
            "anomalies": [],
            "affected_entities": [],
            "affected_services": [],
            "data_gaps": [],
            "evidence_ids": [],
            "errors": [reason],
        }
    if agent == "topology_analyst":
        return {
            "status": "failed",
            "summary": f"Topology analysis failed: {reason}",
            "services_examined": [],
            "shared_dependencies": [],
            "blast_radius": [],
            "propagation_paths": [],
            "uncertainties": [],
            "evidence_ids": [],
            "errors": [reason],
        }
    if agent == "knowledge_researcher":
        return {
            "status": "failed",
            "summary": f"Knowledge research failed: {reason}",
            "relevant_runbooks": [],
            "related_incidents": [],
            "technical_references": [],
            "outdated_or_conflicting": [],
            "suspicious_documents": [],
            "evidence_ids": [],
            "errors": [reason],
        }
    if agent == "incident_analyst":
        return {
            "status": "failed",
            "hypotheses": [],
            "unexplained_observations": [],
            "errors": [reason],
        }
    if agent == "report_drafter":
        return {
            "summary": f"Report drafting failed: {reason}",
            "key_facts": [],
            "affected_services": [],
            "affected_components": [],
            "recommended_steps": [],
            "risk_and_impact": "unknown",
            "open_questions": [reason],
        }
    if agent == "review_verifier":
        return {
            "overall": "concerns",
            "concerns": [
                {
                    "severity": "info",
                    "description": f"secondary review unavailable: {reason}",
                    "related_ids": [],
                }
            ],
        }
    raise KeyError(agent)


# ---------------------------------------------------------------- specialists
def telemetry_policy(t: Turn):
    if not t.called("summarize_anomalies"):
        return ToolCalls([("summarize_anomalies", {})])
    r = t.last("summarize_anomalies")
    if r.get("status") != "ok":
        return Final(
            failed_output("telemetry_analyst", f"{r.get('error_type')}: {r.get('message')}")
        )
    anomalies = [
        {
            "entity_id": a["entity_id"],
            "metric": a["metric"],
            "description": (
                f"{a['verdict']}: {a['metric']} window mean {a['window_mean']:.3g} "
                f"(peak {a['peak']:.3g}) vs baseline {a['baseline_mean']:.3g}"
                + (f", first seen {a['first_seen']}" if a.get("first_seen") else "")
            ),
            "evidence_ids": [a["evidence_id"]],
        }
        for a in r["anomalies"]
    ]
    entities = sorted(
        {a["entity_id"] for a in r["anomalies"] if not a["entity_id"].startswith("svc:")}
    )
    services = sorted(
        {a["entity_id"][4:] for a in r["anomalies"] if a["entity_id"].startswith("svc:")}
    )
    gaps = [d["detail"] for d in r["data_quality"]]
    if anomalies:
        summary = (
            f"{len(anomalies)} anomalous metrics on {len(entities)} network entities; "
            f"services with degraded latency/errors: {', '.join(services) or 'none'}."
        )
    else:
        summary = (
            f"No anomalies across {r['entities_checked']} entities "
            f"({r['normal_metrics_checked']} metrics normal)."
        )
    if gaps:
        summary += f" {len(gaps)} data-quality issue(s) reported."
    return Final(
        {
            "status": "completed",
            "summary": summary,
            "time_window": r["window"],
            "anomalies": anomalies,
            "affected_entities": entities,
            "affected_services": services,
            "data_gaps": gaps,
            "evidence_ids": r["evidence_ids"],
            "errors": [],
        }
    )


def topology_policy(t: Turn):
    services = t.context.get("scope", {}).get("candidate_services", [])
    if not services:
        return Final(
            {
                "status": "completed",
                "summary": "No symptomatic services to analyse.",
                "services_examined": [],
                "shared_dependencies": [],
                "blast_radius": [],
                "propagation_paths": [],
                "uncertainties": ["no candidate services in scope"],
                "evidence_ids": [],
                "errors": [],
            }
        )
    if not t.called("get_service_dependencies"):
        return ToolCalls([("get_service_dependencies", {"services": services})])
    deps = t.last("get_service_dependencies")
    if deps.get("status") != "ok":
        return Final(
            failed_output("topology_analyst", f"{deps.get('error_type')}: {deps.get('message')}")
        )
    ranked = [r for r in deps["shared_ranked"] if r["type"] != "service"]
    if len(services) == 1 and not ranked:
        ranked = [
            {"component_id": c, "services": services, "other_dependents": 0, "type": "?"}
            for c in deps["dependencies"].get(services[0], [])[:4]
        ]
    top = ranked[:4]
    if top and not t.called("get_blast_radius"):
        return ToolCalls([("get_blast_radius", {"component_id": r["component_id"]}) for r in top])
    if top and not t.called("trace_dependency_paths"):
        return ToolCalls(
            [
                ("trace_dependency_paths", {"service": s, "component_id": top[0]["component_id"]})
                for s in top[0]["services"][:3]
            ]
        )
    blasts = {b["component_id"]: b for b in t.all("get_blast_radius") if b.get("status") == "ok"}
    paths = [
        p
        for tr in t.all("trace_dependency_paths")
        if tr.get("status") == "ok"
        for p in tr["paths"][:1]
    ]
    uncertainties = list(deps["uncertainties"])
    for b in blasts.values():
        uncertainties += [u for u in b["uncertainties"] if u not in uncertainties]
    if top:
        covered = set(top[0]["services"])
        for s in services:
            if s not in covered and s not in deps["unknown_services"]:
                uncertainties.append(
                    f"{s} does not depend on {top[0]['component_id']} in the documented topology"
                )
    evidence = list(deps["evidence_ids"])
    for b in blasts.values():
        evidence += b["evidence_ids"]
    for tr in t.all("trace_dependency_paths"):
        evidence += tr.get("evidence_ids", [])
    shared = [
        {
            "component_id": r["component_id"],
            "services": r["services"],
            "evidence_ids": blasts.get(r["component_id"], {}).get("evidence_ids", []),
        }
        for r in top
    ]
    blast_entries = [
        {
            "component_id": c,
            "evidence_ids": b["evidence_ids"],
            "impacts": [
                {"service": i["service"], "impact": i["impact"], "exposure": i["exposure"]}
                for i in b["impacts"]
            ],
        }
        for c, b in blasts.items()
    ]
    summary = (
        f"Examined {len(deps['services'])} services. Most specific shared dependencies: "
        f"{', '.join(r['component_id'] for r in top) or 'none'}."
    )
    return Final(
        {
            "status": "completed",
            "summary": summary,
            "services_examined": deps["services"],
            "shared_dependencies": shared,
            "blast_radius": blast_entries,
            "propagation_paths": paths,
            "uncertainties": uncertainties,
            "evidence_ids": list(dict.fromkeys(evidence)),
            "errors": [],
        }
    )


def knowledge_policy(t: Turn):
    q = t.context.get("scope", {}).get("search_query", "latency packet loss")
    if not t.called("search_runbooks"):
        return ToolCalls(
            [
                ("search_runbooks", {"query": q, "top_k": 6}),
                ("search_incidents", {"query": q, "top_k": 5}),
                ("search_technical_docs", {"query": q, "top_k": 4}),
            ]
        )
    results = [t.last(n) for n in ("search_runbooks", "search_incidents", "search_technical_docs")]
    ok = [r for r in results if r and r.get("status") == "ok"]
    if not ok:
        return Final(failed_output("knowledge_researcher", "all searches failed"))
    hits = [h for r in ok for h in r["hits"]]
    bad = {"suspected_injection", "deprecated", "superseded"}

    def ref(h, note):
        return {
            "doc_id": h["doc_id"],
            "title": h["title"],
            "note": note,
            "evidence_ids": [h["evidence_id"]],
        }

    runbooks = [
        ref(h, f"current runbook covering {', '.join(h['components'])}")
        for h in hits
        if h["doc_type"] == "runbook" and not bad & set(h["flags"]) and h["components"]
    ][:5]
    incidents = [
        ref(h, f"past root cause: {h['root_cause_category']} — similarity is context, not proof")
        for h in hits
        if h["doc_type"] == "incident_report" and "suspected_injection" not in h["flags"]
    ][:5]
    tech = [
        ref(h, "technical reference")
        for h in hits
        if h["doc_type"] == "tech_doc" and not bad & set(h["flags"])
    ][:4]
    outdated = [
        {
            "doc_id": h["doc_id"],
            "issue": f"{h['status']}"
            + (f"; superseded by {h['superseded_by']}" if h.get("superseded_by") else "")
            + "; conflicts with the current procedure — do not follow",
        }
        for h in hits
        if {"deprecated", "superseded"} & set(h["flags"])
    ]
    suspicious = [
        {
            "doc_id": h["doc_id"],
            "issue": "matched prompt-injection patterns; content withheld and not used",
        }
        for h in hits
        if "suspected_injection" in h["flags"]
    ]
    irrelevant = [h["doc_id"] for h in hits if h["doc_type"] == "runbook" and not h["components"]]
    summary = (
        f"{len(runbooks)} applicable runbooks, {len(incidents)} related incidents, "
        f"{len(outdated)} outdated/conflicting, {len(suspicious)} quarantined."
    )
    if irrelevant:
        summary += f" Not applicable to data-centre components: {', '.join(irrelevant)}."
    return Final(
        {
            "status": "completed" if len(ok) == 3 else "partial",
            "summary": summary,
            "relevant_runbooks": runbooks,
            "related_incidents": incidents,
            "technical_references": tech,
            "outdated_or_conflicting": outdated,
            "suspicious_documents": suspicious,
            "evidence_ids": [h["evidence_id"] for h in hits],
            "errors": [
                f"{r.get('error_type')}: {r.get('message')}"
                for r in results
                if r and r.get("status") != "ok"
            ],
        }
    )


def incident_policy(t: Turn):
    if not t.called("generate_hypothesis_candidates"):
        return ToolCalls([("generate_hypothesis_candidates", {})])
    r = t.last("generate_hypothesis_candidates")
    if r.get("status") != "ok":
        return Final(
            failed_output("incident_analyst", f"{r.get('error_type')}: {r.get('message')}")
        )
    hyps = r["hypotheses"]
    to_compare = []
    if hyps:
        to_compare = list(
            dict.fromkeys(
                INC_RE.findall(" ".join(hyps[0]["alternative_explanations"]))
                + hyps[0]["historical_references"]
            )
        )[:2]
    if to_compare and not t.called("compare_with_incident"):
        return ToolCalls([("compare_with_incident", {"incident_id": i}) for i in to_compare])
    for cmp in t.all("compare_with_incident"):
        if cmp.get("status") == "ok" and not cmp["signature_matches_current"] and hyps:
            note = f"{cmp['incident_id']} compared: {'; '.join(cmp['differences'])[:180]}"
            hyps[0]["alternative_explanations"] = (hyps[0]["alternative_explanations"] + [note])[:6]
    status = "completed" if hyps or r["anomaly_count"] == 0 else "partial"
    return Final(
        {
            "status": status,
            "hypotheses": hyps,
            "unexplained_observations": r["unexplained_observations"],
            "errors": [],
        }
    )


# ---------------------------------------------------------------- synthesis
def _plan_draft(ctx: dict) -> dict:
    tel = ctx.get("telemetry_finding") or {}
    kn = ctx.get("knowledge_finding") or {}
    an = ctx.get("incident_analysis") or {}
    hyps = an.get("hypotheses", [])
    runbooks = kn.get("relevant_runbooks", [])

    def runbooks_for(cat):
        kws = RUNBOOK_KEYWORDS.get(cat, ())
        return [r["doc_id"] for r in runbooks if any(k in r["title"].lower() for k in kws)][:2]

    facts = [
        {"statement": f"{a['entity_id']} {a['description']}", "evidence_ids": a["evidence_ids"]}
        for a in tel.get("anomalies", [])[:8]
    ]
    selected = hyps[:1] + [
        h
        for h in hyps[1:3]
        if h["confidence"] != "weak" and h["component_id"] != hyps[0]["component_id"]
    ]
    steps = []
    for h in selected:
        for c in h["diagnostic_checks"][:3]:
            steps.append(
                {
                    "step": c,
                    "rationale": f"tests {h['hypothesis_id']} "
                    f"({h['category']} on {h['component_id']})",
                    "runbook_ids": runbooks_for(h["category"]),
                    "evidence_ids": h["supporting_evidence_ids"][:2],
                }
            )
    gaps = tel.get("data_gaps", [])
    for g in gaps[:2]:
        steps.append(
            {
                "step": f"Collect the missing telemetry: {g}"[:300],
                "rationale": "data gap limits confidence",
                "runbook_ids": [],
                "evidence_ids": [],
            }
        )
    if hyps:
        steps.append(
            {
                "step": "Escalate to network on-call with this report; any remediation must "
                "go through change approval",
                "rationale": "OpsPilot is read-only",
                "runbook_ids": [],
                "evidence_ids": [],
            }
        )
    elif tel.get("status") == "completed" and not tel.get("anomalies"):
        steps.append(
            {
                "step": "Monitor service latency and packet loss for recurrence",
                "rationale": "no anomaly found in the window",
                "runbook_ids": [],
                "evidence_ids": [],
            }
        )
    services = tel.get("affected_services", [])
    components = list(
        dict.fromkeys(
            [
                h["component_id"]
                for h in hyps
                if h["confidence"] != "weak" and h["category"] != "application_side"
            ]
            + tel.get("affected_entities", [])
        )
    )
    if hyps:
        h = hyps[0]
        explained = ", ".join(h["explained_services"]) or "no observed service impact"
        summary = (
            f"Leading hypothesis ({h['confidence']} confidence): {h['statement']} "
            f"It would explain {explained}."
        )
        others = [f"{o['category']} on {o['component_id']} ({o['confidence']})" for o in hyps[1:3]]
        if others:
            summary += f" Other candidates: {'; '.join(others)}."
    elif an.get("status") == "failed":
        summary = (
            "Incident analysis did not complete, so no root-cause hypothesis is offered. "
            f"Observed anomalies: {len(tel.get('anomalies', []))}."
        )
    elif tel.get("status") == "failed":
        summary = "Telemetry could not be retrieved; the investigation cannot assess the network."
    else:
        summary = "No network-level anomalies were detected in the investigation window."
    if services:
        summary += f" Affected services: {', '.join(services)}."
    questions = list(an.get("unexplained_observations", [])) + gaps
    risk = (
        f"{len(services)} service(s) affected"
        + (f"; leading component {hyps[0]['component_id']}" if hyps else "")
        + "."
    )
    return {
        "summary": summary[:2000],
        "key_facts": facts,
        "affected_services": services,
        "affected_components": components,
        "recommended_steps": steps[:12],
        "risk_and_impact": risk,
        "open_questions": questions[:10],
    }


def drafter_policy(t: Turn):
    draft = _plan_draft(t.context)
    cited = sorted(
        {e for f in draft["key_facts"] for e in f["evidence_ids"]}
        | {e for s in draft["recommended_steps"] for e in s["evidence_ids"]}
    )
    if not t.called("validate_evidence_ids"):
        return ToolCalls(
            [
                ("validate_evidence_ids", {"evidence_ids": cited}),
                (
                    "check_recommendation_policy",
                    {"steps": [s["step"] for s in draft["recommended_steps"]]},
                ),
            ]
        )
    v = t.last("validate_evidence_ids") or {}
    invalid = set(v.get("invalid", [])) if v.get("status") == "ok" else set()
    p = t.last("check_recommendation_policy") or {}
    disallowed = {r["step"] for r in p.get("results", []) if not r["allowed"]}
    draft["key_facts"] = [f for f in draft["key_facts"] if not invalid & set(f["evidence_ids"])]
    draft["recommended_steps"] = [
        {**s, "evidence_ids": [e for e in s["evidence_ids"] if e not in invalid]}
        for s in draft["recommended_steps"]
        if s["step"] not in disallowed
    ]
    return Final(draft)


def review_policy(t: Turn):
    draft = t.context.get("report_draft") or {}
    an = t.context.get("incident_analysis") or {}
    concerns = []
    text = (draft.get("summary") or "").lower()
    top = (an.get("hypotheses") or [{}])[0]
    if (
        top
        and top.get("confidence") != "strong"
        and any(w in text for w in ("confirmed", "definitely", "root cause is"))
    ):
        concerns.append(
            {
                "severity": "warning",
                "description": "draft overstates certainty",
                "related_ids": [top.get("hypothesis_id", "")],
            }
        )
    return Final({"overall": "concerns" if concerns else "no_concerns", "concerns": concerns})


POLICIES: dict[str, Callable[[Turn], ToolCalls | Final]] = {
    "telemetry_analyst": telemetry_policy,
    "topology_analyst": topology_policy,
    "knowledge_researcher": knowledge_policy,
    "incident_analyst": incident_policy,
    "report_drafter": drafter_policy,
    "review_verifier": review_policy,
}
