"""Phase 8: deterministic policy engine."""

import pytest
from graph_helpers import shared_deps

from netpulse.models import ConfidenceAssessment, ProposedAction
from netpulse.policy.catalog import load_catalog
from netpulse.policy.engine import PolicyContext, review_action, review_incident

CATALOG = load_catalog()


def assessment(sufficient=True, conclusive=True) -> ConfidenceAssessment:
    return ConfidenceAssessment(
        overall="high" if sufficient else "low", evidence_sufficient=sufficient, conclusive=conclusive, rationale="t"
    )


def ctx(sufficient=True, conclusive=True, severity="medium", signal=True, roots=1, conflicted=()) -> PolicyContext:
    return PolicyContext(
        assessment=assessment(sufficient, conclusive),
        severity=severity,
        has_signal=signal,
        leading_root_count=roots,
        conflicted_actions=frozenset(conflicted),
    )


def action(catalog_id: str, target: str, action_id: str = "act-01") -> ProposedAction:
    entry = CATALOG[catalog_id]
    return ProposedAction(
        action_id=action_id,
        catalog_id=catalog_id,
        kind=entry.kind,
        description=entry.description,
        target_entities=[target],
        reversible=entry.reversible,
    )


def decide(a: ProposedAction, c: PolicyContext):
    return review_action(a, c, CATALOG, shared_deps().topology)


def test_diagnostics_are_allowed():
    d = decide(action("inspect_interface_counters", "link-core-1-agg-1"), ctx(sufficient=False))
    assert d.outcome == "allow" and d.required_role == "none"


def test_unknown_catalog_entry_is_denied():
    bogus = ProposedAction(
        action_id="act-01", catalog_id="format_router", kind="remediation", description="x", reversible=True
    )
    assert decide(bogus, ctx()).outcome == "deny"


def test_remediation_without_sufficient_evidence_is_denied():
    d = decide(action("apply_rate_limit", "link-core-1-agg-1"), ctx(sufficient=False))
    assert d.outcome == "deny" and "insufficient" in d.reasons[0]


def test_shifting_traffic_off_a_single_homed_link_is_denied():
    d = decide(action("shift_traffic_to_redundant_path", "link-agg-3-acc-6"), ctx())
    assert d.outcome == "deny" and "no redundant path" in d.reasons[0]


def test_reversible_remediation_needs_operator_approval():
    d = decide(action("apply_rate_limit", "link-core-1-agg-1"), ctx())
    assert d.outcome == "require_approval" and d.required_role == "operator"
    assert "remediation always requires human approval" in d.reasons


@pytest.mark.parametrize(
    ("catalog_id", "target", "context", "reason"),
    [
        ("schedule_optic_replacement", "link-core-1-agg-1", ctx(), "not reversible"),
        ("apply_rate_limit", "link-pe-1-fw-2", ctx(), "large blast radius"),  # internet: 15 500 customers
        ("apply_rate_limit", "link-core-1-agg-1", ctx(conflicted={"apply_rate_limit"}), "conflicting guidance"),
        ("apply_rate_limit", "link-core-1-agg-1", ctx(severity="critical"), "severity is critical"),
    ],
)
def test_escalating_conditions_require_senior_operator(catalog_id, target, context, reason):
    d = decide(action(catalog_id, target), context)
    assert d.outcome == "require_approval" and d.required_role == "senior_operator"
    assert any(reason in r for r in d.reasons)


def test_incident_requires_approval_with_highest_role():
    decisions = [
        decide(action("inspect_interface_counters", "link-core-1-agg-1", "act-01"), ctx()),
        decide(action("apply_rate_limit", "link-core-1-agg-1", "act-02"), ctx()),
        decide(action("schedule_optic_replacement", "link-core-1-agg-1", "act-03"), ctx()),
    ]
    incident = review_incident(decisions, ctx())
    assert incident.action_id is None and incident.outcome == "require_approval"
    assert incident.required_role == "senior_operator"


@pytest.mark.parametrize(
    ("context", "reason"),
    [
        (ctx(sufficient=False, conclusive=False), "insufficient"),
        (ctx(severity="critical"), "critical"),
        (ctx(roots=2), "simultaneous root causes"),
    ],
)
def test_incident_escalation_conditions(context, reason):
    incident = review_incident([], context)
    assert incident.outcome == "escalate" and any(reason in r for r in incident.reasons)


def test_escalation_with_pending_approval_raises_role_and_keeps_reasons():
    decisions = [decide(action("apply_rate_limit", "link-core-1-agg-1"), ctx(roots=2))]
    incident = review_incident(decisions, ctx(roots=2))
    assert incident.outcome == "require_approval" and incident.required_role == "senior_operator"
    assert any("simultaneous" in r for r in incident.reasons)


def test_diagnostics_only_with_nothing_to_escalate_is_allowed():
    decisions = [decide(action("run_path_trace", "voip"), ctx(signal=False, conclusive=False, sufficient=False))]
    assert review_incident(decisions, ctx(signal=False, conclusive=False, sufficient=False)).outcome == "allow"
