"""Catalogue of defects that can be planted in the practice P2P API.

Each bug is off by default. Turning one on makes the API violate exactly one
financial rule in a realistic way, so the QA agent's detection rate can be
measured: a bug counts as *detected* when the attack named in ``caught_by``
reports BREACHED while the bug is on, and HELD while it is off.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Bug:
    name: str
    rule: str
    caught_by: str
    description: str


BUGS: dict[str, Bug] = {
    b.name: b
    for b in [
        Bug(
            "unmatched_approve",
            "match_gate",
            "approve_unmatched",
            "Approve does not require the invoice to have passed 3-way match.",
        ),
        Bug(
            "over_receive_cumulative",
            "receipt_quantity",
            "over_receive_cumulative",
            "Receipt checks each call against the ordered qty, not the running total.",
        ),
        Bug(
            "overpay_ordered",
            "overpayment_protection",
            "invoice_exceeds_received",
            "Invoice cap uses the ordered value instead of the received value.",
        ),
        Bug(
            "overpay_cumulative",
            "overpayment_protection",
            "invoice_cumulative_overbill",
            "Invoice cap ignores what was already invoiced on the same PO.",
        ),
        Bug(
            "inactive_po",
            "inactive_vendor_gate",
            "inactive_vendor_po",
            "Purchase orders can be raised against inactive vendors.",
        ),
        Bug(
            "dup_case",
            "duplicate_invoice_detection",
            "duplicate_invoice_normalized",
            "Duplicate check is case- and whitespace-sensitive (INV-1 vs ' inv-1').",
        ),
        Bug(
            "float_money",
            "gl_balance",
            "gl_balance_precision",
            "Money is computed with float, so GL debits and credits drift apart.",
        ),
        Bug(
            "silent_partial",
            "partial_receipt_flag",
            "partial_receipt_flag",
            "Match passes silently when goods are only partially received.",
        ),
        Bug(
            "double_post",
            "exposure_integrity",
            "double_approve",
            "Approving an already-approved invoice posts it to the GL again.",
        ),
        Bug(
            "race_approve",
            "concurrency",
            "concurrent_approve",
            "Approve has no lock: two simultaneous approvals both post.",
        ),
        Bug(
            "cross_vendor",
            "vendor_consistency",
            "cross_vendor_invoice",
            "An invoice from vendor B is accepted against vendor A's PO.",
        ),
        Bug(
            "non_positive",
            "input_validation",
            "non_positive_values",
            "Zero or negative quantities and prices are accepted.",
        ),
        Bug(
            "no_price_check",
            "price_variance",
            "price_variance",
            "Match ignores unit-price differences between PO and invoice.",
        ),
        Bug(
            "uom_ignored",
            "uom_consistency",
            "invoice_uom_mismatch",
            "Match compares quantities without units: 10 LB is matched against 10 EA.",
        ),
        Bug(
            "fractional_uom",
            "uom_consistency",
            "fractional_discrete_qty",
            "Fractional quantities are accepted for discrete units (2.5 EA, 0.5 BOX).",
        ),
        Bug(
            "wrong_uom_factor",
            "uom_consistency",
            "wrong_uom_conversion_factor",
            "Unit conversion uses 2× the correct factor; qty is understated so overbilling passes.",
        ),
    ]
}


def parse_bugs(spec: str | None) -> frozenset[str]:
    """Parse ``"a,b"`` / ``"all"`` / ``""`` into a validated set of bug names."""
    if not spec:
        return frozenset()
    if spec.strip() == "all":
        return frozenset(BUGS)
    names = {s.strip() for s in spec.split(",") if s.strip()}
    unknown = names - BUGS.keys()
    if unknown:
        raise ValueError(f"unknown bug(s): {', '.join(sorted(unknown))}")
    return frozenset(names)
