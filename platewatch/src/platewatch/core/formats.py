"""Regional licence-plate format rules.

Deterministic and framework-free: patterns and the enforced-region list live here, never in a
prompt. Patterns match plates **after** `normalize_plate`, so separators are already gone and
"AB12 CDE" arrives as "AB12CDE".

A regex can only describe the ordinary passenger-car sequence of a region. Personalised, military,
diplomatic, trade, trailer and historic plates legitimately do not match, so a non-match is a
reason to look, not proof that a plate is wrong. That is why `tag_plate` flags rather than
discards, and why regions are enforced only when `PLATEWATCH_REGIONS` names them.
"""

from __future__ import annotations

import os
import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PlateFormat:
    """One region's ordinary passenger-car sequence, with the source it was taken from."""

    code: str
    description: str
    pattern: re.Pattern[str]
    source: str
    examples: tuple[str, ...]
    counter_examples: tuple[str, ...] = field(default=())


FORMATS: tuple[PlateFormat, ...] = (
    PlateFormat(
        code="gb",
        description="UK post-2001: 2 area letters (no I/Q/Z), 2 age digits, 3 letters (no I/Q)",
        pattern=re.compile(r"[A-HJ-PR-Y]{2}\d{2}[A-HJ-PR-Z]{3}"),
        source="https://en.wikipedia.org/wiki/Vehicle_registration_plates_of_the_United_Kingdom",
        examples=("AB12CDE", "BD51SMR", "LO06YRV"),
        counter_examples=("AB1CDE", "AB12CIE"),
    ),
    PlateFormat(
        code="fr",
        description="France SIV post-2009: 2 letters, 3 digits, 2 letters; no I/O/U, not SS or WW",
        pattern=re.compile(r"(?!WW)(?!SS)[A-HJ-NP-TV-Z]{2}\d{3}(?!SS)[A-HJ-NP-TV-Z]{2}"),
        source="https://en.wikipedia.org/wiki/Vehicle_registration_plates_of_France",
        examples=("AA229DB", "AB123CD", "CV456ZX"),
        counter_examples=("AI123CD", "WW123AB"),
    ),
    PlateFormat(
        code="es",
        description="Spain post-2000: 4 digits then 3 consonants (no vowels, no Q)",
        pattern=re.compile(r"\d{4}[BCDFGHJKLMNPRSTVWXYZ]{3}"),
        source="https://en.wikipedia.org/wiki/Vehicle_registration_plates_of_Spain",
        examples=("1234BCD", "0000BBB", "9876ZZZ"),
        counter_examples=("1234ABC", "123BCD"),
    ),
    PlateFormat(
        code="it",
        description="Italy post-1994: 2 letters, 3 digits, 2 letters; no I/O/Q/U",
        pattern=re.compile(r"[A-HJ-NPR-TV-Z]{2}\d{3}[A-HJ-NPR-TV-Z]{2}"),
        source="https://en.wikipedia.org/wiki/Vehicle_registration_plates_of_Italy",
        examples=("AB123CD", "GA456HJ", "FL789MN"),
        counter_examples=("AI123CD", "AB12CD"),
    ),
    PlateFormat(
        code="nl",
        description="Netherlands sidecode 11 (current): 3 letters, 2 digits, 1 letter; "
        "no vowels, C, Q, M or W. Sidecodes 1-10 and 12 remain legal and do not match",
        pattern=re.compile(r"[BDFGHJKLNPRSTVXZ]{3}\d{2}[BDFGHJKLNPRSTVXZ]"),
        source="https://en.wikipedia.org/wiki/Vehicle_registration_plates_of_the_Netherlands",
        examples=("NBB01B", "GBB01B", "KZT49R"),
        counter_examples=("ABB01B", "NBB013"),
    ),
    PlateFormat(
        code="us-ca",
        description="California passenger: current 123ABC1 and the exhausted 1ABC234 series; "
        "I/O/Q only as the middle letter",
        pattern=re.compile(
            r"(?:\d{3}[A-HJ-NPR-Z][A-Z][A-HJ-NPR-Z]\d|\d[A-HJ-NPR-Z][A-Z][A-HJ-NPR-Z]\d{3})"
        ),
        source="https://en.wikipedia.org/wiki/Vehicle_registration_plates_of_California",
        examples=("123ABC1", "123AOC4", "1ABC234", "5AOC123"),
        counter_examples=("123OBC1", "1OBC234"),
    ),
    PlateFormat(
        code="us-ny",
        description="New York post-2001: 3 letters then 4 digits; no I/O/Q",
        pattern=re.compile(r"[A-HJ-NPR-Z]{3}\d{4}"),
        source="https://en.wikipedia.org/wiki/Vehicle_registration_plates_of_New_York",
        examples=("KAA1000", "MLA8374", "KCH9999"),
        counter_examples=("KIA1000", "KAA100"),
    ),
    PlateFormat(
        code="us-tx",
        description="Texas Classic since 2012: 3 letters then 4 digits. Whether I/O/Q are still "
        "skipped is not stated by the source, so the full alphabet is allowed",
        pattern=re.compile(r"[A-Z]{3}\d{4}"),
        source="https://en.wikipedia.org/wiki/Vehicle_registration_plates_of_Texas",
        examples=("BBB0001", "YNH7978", "RKT4821"),
        counter_examples=("BBB001", "BB12345"),
    ),
    PlateFormat(
        code="us-fl",
        description="Florida current base: 3 letters then 3 digits, no O. Six further passenger "
        "formats remain valid and do not match",
        pattern=re.compile(r"[A-HJ-NPR-Z]{3}\d{3}"),
        source="https://en.wikipedia.org/wiki/Vehicle_registration_plates_of_Florida",
        examples=("XAA000", "XYZ999", "XBG417"),
        counter_examples=("XOA000", "XAA0000"),
    ),
)

# Germany is deliberately absent. Its plate is an area code of 1-3 letters, 1-2 further letters
# and 1-4 digits, separated on the plate but not after normalisation, so "BAB1234" is equally
# B-AB 1234 and BA-B 1234 and the split cannot be recovered without the district-code list. What
# is left, 2-5 letters followed by 1-4 digits, matches ordinary strings such as "ABC123" and
# collides with the New York, Texas and Florida patterns, so it would add noise, not information.
# See https://en.wikipedia.org/wiki/Vehicle_registration_plates_of_Germany.


def known_region_codes() -> frozenset[str]:
    return frozenset(f.code for f in FORMATS)


def match_regions(plate: str) -> tuple[str, ...]:
    """Region codes whose ordinary-plate pattern matches `plate`, sorted for determinism."""
    return tuple(sorted(f.code for f in FORMATS if f.pattern.fullmatch(plate)))


def validate_regions(regions: Iterable[str]) -> tuple[str, ...]:
    """Normalise and check region codes, sorted; raise on anything not in the table."""
    wanted = tuple(sorted({r.strip().lower() for r in regions if r.strip()}))
    unknown = [r for r in wanted if r not in known_region_codes()]
    if unknown:
        known = ", ".join(sorted(known_region_codes()))
        raise ValueError(f"unknown plate region(s): {', '.join(unknown)}; known regions: {known}")
    return wanted


def parse_regions(raw: str) -> tuple[str, ...]:
    """Parse a comma-separated region list, e.g. `PLATEWATCH_REGIONS=gb,fr`."""
    return validate_regions(raw.split(","))


def enforced_regions(regions: Collection[str] | None = None) -> tuple[str, ...]:
    """Explicit regions win; otherwise the environment's list, which may be empty."""
    return validate_regions(regions) if regions is not None else ENFORCED_REGIONS


ENFORCED_REGIONS: tuple[str, ...] = parse_regions(os.getenv("PLATEWATCH_REGIONS", ""))
