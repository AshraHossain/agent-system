"""Plate text normalisation and rule-based tagging. Thresholds live here, never in prompts."""

from __future__ import annotations

import re

LOW_CONFIDENCE = 0.5
MIN_LEN, MAX_LEN = 4, 8

_NON_ALNUM = re.compile(r"[^A-Z0-9]")


def normalize_plate(raw: str) -> str:
    """Upper-case and strip everything except A-Z and 0-9."""
    return _NON_ALNUM.sub("", raw.upper())


def plausible_format(plate: str) -> bool:
    """Generic shape check: 4-8 chars with at least one letter and one digit.

    Deliberately region-agnostic; regional formats belong in a pluggable rule set.
    """
    return (
        MIN_LEN <= len(plate) <= MAX_LEN
        and any(c.isalpha() for c in plate)
        and any(c.isdigit() for c in plate)
    )


def tag_plate(plate: str, confidence: float) -> list[str]:
    tags = ["plausible-format" if plausible_format(plate) else "odd-format"]
    if confidence < LOW_CONFIDENCE:
        tags.append("low-confidence")
    if "odd-format" in tags or "low-confidence" in tags:
        tags.append("needs-review")
    return tags
