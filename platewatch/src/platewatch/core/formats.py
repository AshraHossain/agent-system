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


FORMATS: tuple[PlateFormat, ...] = ()


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
