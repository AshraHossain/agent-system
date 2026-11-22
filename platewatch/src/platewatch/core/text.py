"""Plate text normalisation and rule-based tagging. Thresholds live here, never in prompts."""

from __future__ import annotations

import re
from collections.abc import Collection

from platewatch.core.formats import enforced_regions, match_regions

LOW_CONFIDENCE = 0.5
MIN_LEN, MAX_LEN = 4, 8

_NON_ALNUM = re.compile(r"[^A-Z0-9]")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_TAG_ESCAPE = re.compile(r"<\s*/?\s*untrusted_context[^>]*>", re.I)


def sanitize_ocr_text(raw: str, max_chars: int = 128) -> str:
    """Strip control characters and boundary-escape tags, then wrap inside `<untrusted_context>`."""
    cleaned = _TAG_ESCAPE.sub("", _CONTROL.sub(" ", raw or "")).strip()[:max_chars]
    return f"<untrusted_context>{cleaned}</untrusted_context>"


def normalize_plate(raw: str) -> str:
    """Upper-case and strip everything except A-Z and 0-9."""
    return _NON_ALNUM.sub("", raw.upper())


def generic_format(plate: str) -> bool:
    """Region-agnostic shape check: 4-8 chars with at least one letter and one digit."""
    return (
        MIN_LEN <= len(plate) <= MAX_LEN
        and any(c.isalpha() for c in plate)
        and any(c.isdigit() for c in plate)
    )


def plausible_format(plate: str, regions: Collection[str] | None = None) -> bool:
    """Whether `plate` has an acceptable shape.

    With regions enforced (argument, or `PLATEWATCH_REGIONS`), only those regions' ordinary
    passenger-car patterns count. With none enforced, a plate passes on either a known regional
    pattern or the generic shape check, so enabling the rules never rejects what it used to accept.
    """
    wanted = enforced_regions(regions)
    if wanted:
        return bool(set(match_regions(plate)) & set(wanted))
    return bool(match_regions(plate)) or generic_format(plate)


def tag_plate(plate: str, confidence: float, regions: Collection[str] | None = None) -> list[str]:
    tags = ["plausible-format" if plausible_format(plate, regions) else "odd-format"]
    tags += [f"region:{code}" for code in match_regions(plate)]
    if confidence < LOW_CONFIDENCE:
        tags.append("low-confidence")
    if "odd-format" in tags or "low-confidence" in tags:
        tags.append("needs-review")
    return tags
