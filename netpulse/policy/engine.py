"""Deterministic policy review of proposed actions and of the incident as a whole.

No LLM is involved. Every rule below is code, and each decision lists the
rules that fired.

Per action:

| Rule | Effect |
|---|---|
| diagnostic (read-only) | ALLOW |
| not in the static catalog | DENY |
| evidence insufficient for remediation | DENY |
| `shift_traffic_to_redundant_path` on an entity without a redundant path | DENY |
| any remediation | REQUIRE_APPROVAL, at least the catalog's `required_role` |
| irreversible | REQUIRE_APPROVAL by senior_operator |
| blast radius isolates nodes or puts ≥ 10 000 customers at risk | REQUIRE_APPROVAL by senior_operator |
| a high-criticality service depends on the target | reason recorded (role unchanged) |
| cited by runbooks that declare a conflict | REQUIRE_APPROVAL by senior_operator |
| operator-reported severity is critical | REQUIRE_APPROVAL by senior_operator |

Incident level (``action_id=None``): ESCALATE when the assessment is not
conclusive but real signal exists, when severity is critical, or when several
complementary root causes were found (multiple simultaneous faults).
REQUIRE_APPROVAL when any remediation needs approval. ALLOW otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass

from netpulse.models import (
    ROLE_RANK,
    ActionKind,
    ConfidenceAssessment,
    PolicyDecision,
    PolicyOutcome,
    ProposedAction,
)
from netpulse.policy.catalog import CatalogAction
from netpulse.topology.analysis import TopologyGraph

LARGE_CUSTOMER_IMPACT = 10_000


@dataclass(frozen=True)
class PolicyContext:
    assessment: ConfidenceAssessment
    severity: str
    has_signal: bool
    leading_root_count: int  # complementary leading hypotheses (more than 1 means multiple faults)
    conflicted_actions: frozenset[str]  # catalog ids cited by runbooks that declare a conflict


def _max_role(*roles: str) -> str:
    return max(roles, key=ROLE_RANK.__getitem__)


def review_action(
    action: ProposedAction, ctx: PolicyContext, catalog: dict[str, CatalogAction], graph: TopologyGraph
) -> PolicyDecision:
    entry = catalog.get(action.catalog_id)
    if entry is None:
        return PolicyDecision(
            action_id=action.action_id, outcome=PolicyOutcome.DENY, reasons=["action is not in the static catalog"]
        )
    if action.kind == ActionKind.DIAGNOSTIC and entry.kind == "diagnostic":
        return PolicyDecision(action_id=action.action_id, outcome=PolicyOutcome.ALLOW, reasons=["read-only diagnostic"])
    if not ctx.assessment.evidence_sufficient:
        return PolicyDecision(
            action_id=action.action_id,
            outcome=PolicyOutcome.DENY,
            reasons=["evidence is insufficient to justify remediation"],
        )

    reasons, role = ["remediation always requires human approval"], entry.required_role
    if role == "none":
        role = "operator"
    targets = [t for t in action.target_entities if graph.topology.kind(t) in {"node", "link"}]
    radii = [graph.blast_radius(t) for t in targets]
    if action.catalog_id == "shift_traffic_to_redundant_path" and any(not r.redundant for r in radii):
        return PolicyDecision(
            action_id=action.action_id,
            outcome=PolicyOutcome.DENY,
            reasons=["no redundant path exists for the target; shifting traffic would isolate nodes"],
        )
    if not entry.reversible:
        reasons.append("action is not reversible")
        role = _max_role(role, "senior_operator")
    if any(r.isolated_nodes or r.customers_at_risk >= LARGE_CUSTOMER_IMPACT for r in radii):
        reasons.append(f"large blast radius (isolates nodes or puts ≥ {LARGE_CUSTOMER_IMPACT} customers at risk)")
        role = _max_role(role, "senior_operator")
    elif any(r.max_service_criticality == "high" for r in radii):
        reasons.append("a high-criticality service depends on the target")
    if action.catalog_id in ctx.conflicted_actions:
        reasons.append("retrieved runbooks give conflicting guidance about this action")
        role = _max_role(role, "senior_operator")
    if ctx.severity == "critical":
        reasons.append("operator-reported severity is critical")
        role = _max_role(role, "senior_operator")
    return PolicyDecision(
        action_id=action.action_id, outcome=PolicyOutcome.REQUIRE_APPROVAL, reasons=reasons, required_role=role
    )


def escalation_reasons(ctx: PolicyContext) -> list[str]:
    reasons = []
    if not ctx.assessment.conclusive and ctx.has_signal:
        reasons.append("evidence is insufficient to single out a root cause")
    if ctx.severity == "critical":
        reasons.append("operator-reported severity is critical")
    if ctx.leading_root_count > 1:
        reasons.append(f"{ctx.leading_root_count} simultaneous root causes identified")
    return reasons


def review_incident(decisions: list[PolicyDecision], ctx: PolicyContext) -> PolicyDecision:
    approvals = [d for d in decisions if d.outcome == PolicyOutcome.REQUIRE_APPROVAL]
    escalate = escalation_reasons(ctx)
    if approvals:
        role = _max_role("operator", *(d.required_role for d in approvals), *(["senior_operator"] if escalate else []))
        return PolicyDecision(
            action_id=None,
            outcome=PolicyOutcome.REQUIRE_APPROVAL,
            reasons=[f"{len(approvals)} remediation proposal(s) await approval", *escalate],
            required_role=role,
        )
    if escalate:
        return PolicyDecision(
            action_id=None, outcome=PolicyOutcome.ESCALATE, reasons=escalate, required_role="senior_operator"
        )
    return PolicyDecision(action_id=None, outcome=PolicyOutcome.ALLOW, reasons=["only read-only diagnostics proposed"])
