"""Financial checks in plain Python, with Decimal throughout.

This module is the source of truth for pass/fail. The LLM may plan, explore
and explain, but a verdict about money always comes from arithmetic here. It
never comes from a model's opinion, and never from a status code alone.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

CENT = Decimal("0.01")


def dec(value: Any) -> Decimal | None:
    """Parse a JSON money/qty value. ``str()`` first so 0.1 stays 0.1, not 0.1000000000000000055."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def has_sub_cent(value: Any) -> bool:
    """True when a money value carries more than 2 decimal places (e.g. 56.99999999999999)."""
    d = dec(value)
    return d is not None and d != d.quantize(CENT)


def expected_amount(lines: list[tuple[str, Decimal, Decimal]]) -> Decimal:
    """Invoice/PO value: each line rounded to the cent, then summed."""
    return sum((money(qty * price) for _, qty, price in lines), Decimal("0"))


def pick(body: Any, *names: str) -> Any:
    """First present key among ``names`` (APIs disagree on field names)."""
    if isinstance(body, dict):
        for name in names:
            if name in body:
                return body[name]
    return None


def gl_problems(entry: dict[str, Any]) -> list[str]:
    """Problems with one GL entry: imbalance and sub-cent amounts."""
    lines = pick(entry, "lines", "entries", "postings") or []
    debit = Decimal("0")
    credit = Decimal("0")
    problems: list[str] = []
    for ln in lines:
        d, c = (
            dec(pick(ln, "debit", "dr")) or Decimal("0"),
            dec(pick(ln, "credit", "cr")) or Decimal("0"),
        )
        debit += d
        credit += c
        for label, raw in (
            ("debit", pick(ln, "debit", "dr")),
            ("credit", pick(ln, "credit", "cr")),
        ):
            if has_sub_cent(raw):
                problems.append(f"{label} {raw} on {pick(ln, 'account')} has sub-cent precision")
    if not lines:
        problems.append("GL entry has no lines")
    if debit != credit:
        problems.append(f"unbalanced: debits {debit} != credits {credit} (diff {debit - credit})")
    return problems


def gl_totals(entry: dict[str, Any]) -> dict[str, str]:
    lines = pick(entry, "lines", "entries", "postings") or []
    debit = sum((dec(pick(ln, "debit", "dr")) or Decimal("0") for ln in lines), Decimal("0"))
    credit = sum((dec(pick(ln, "credit", "cr")) or Decimal("0") for ln in lines), Decimal("0"))
    return {"debits": str(debit), "credits": str(credit)}


def is_ap_account(account: Any) -> bool:
    name = str(account or "").lower()
    return name.startswith("2000") or "payable" in name


def open_ap_from_gl(entries: list[dict[str, Any]]) -> Decimal:
    """Net AP liability in these GL entries: AP credits (approvals) minus AP debits (payments)."""
    net = Decimal("0")
    for entry in entries:
        for ln in pick(entry, "lines", "entries", "postings") or []:
            if is_ap_account(pick(ln, "account", "account_name")):
                net += dec(pick(ln, "credit", "cr")) or Decimal("0")
                net -= dec(pick(ln, "debit", "dr")) or Decimal("0")
    return money(net)


PARTIAL_WORDS = ("partial", "short_receipt", "under_received", "not_fully_received")


def surfaces_partial(match_body: Any) -> bool:
    """Did a match response flag the partial receipt anywhere (status, exceptions, warnings)?"""
    if isinstance(match_body, dict):
        return any(surfaces_partial(v) for v in match_body.values())
    if isinstance(match_body, list):
        return any(surfaces_partial(v) for v in match_body)
    if isinstance(match_body, str):
        text = match_body.lower()
        return any(word in text for word in PARTIAL_WORDS)
    return False


def match_is_clean(match_body: Any) -> bool:
    """A match that passed with no exceptions or warnings at all."""
    status = str(pick(match_body, "status", "result", "match_status") or "").lower()
    flags = pick(match_body, "exceptions", "warnings", "flags", "issues") or []
    return status in {"matched", "passed", "ok", "success"} and not flags
