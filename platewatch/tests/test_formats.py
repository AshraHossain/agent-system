"""Regional plate-format rules: self-consistency of the table, and tagging behaviour."""

from __future__ import annotations

import pytest

from platewatch.core import formats
from platewatch.core.formats import FORMATS, known_region_codes, match_regions
from platewatch.core.text import plausible_format, tag_plate


def test_table_is_self_consistent():
    """Every format documents a source and its own examples agree with its pattern."""
    assert FORMATS, "the format table must not be empty"
    codes = [f.code for f in FORMATS]
    assert len(codes) == len(set(codes)), "region codes must be unique"
    for f in FORMATS:
        assert f.source.startswith("https://"), f"{f.code} needs a source URL"
        assert f.description, f"{f.code} needs a plain-language description"
        assert f.examples, f"{f.code} needs at least one example"
        for plate in f.examples:
            assert plate == plate.upper() and plate.isalnum(), f"{f.code}: {plate} not normalised"
            assert f.pattern.fullmatch(plate), f"{f.code}: own example {plate} does not match"
        for plate in f.counter_examples:
            assert not f.pattern.fullmatch(plate), f"{f.code}: {plate} should not match"


def test_match_regions_finds_the_declared_region():
    for f in FORMATS:
        for plate in f.examples:
            assert f.code in match_regions(plate), f"{plate} should match {f.code}"


def test_match_regions_is_empty_for_nonsense():
    assert match_regions("K9") == ()
    assert match_regions("") == ()


def test_known_region_codes_matches_the_table():
    assert known_region_codes() == frozenset(f.code for f in FORMATS)


# --- behaviour of the permissive default (no regions enforced) -------------------------


def test_default_keeps_the_generic_shape_check():
    """Unset regions must not start rejecting plates the generic check accepted."""
    assert plausible_format("ABC123")
    assert not plausible_format("K9")
    assert "needs-review" not in tag_plate("ABC123", 0.9)
    assert "needs-review" in tag_plate("K9", 0.9)


def test_recognised_plates_are_tagged_with_their_region():
    gb = next(f for f in FORMATS if f.code == "gb")
    tags = tag_plate(gb.examples[0], 0.9)
    assert "region:gb" in tags
    assert "plausible-format" in tags and "needs-review" not in tags


def test_unrecognised_but_plausible_plate_has_no_region_tag():
    tags = tag_plate("ABC123", 0.9)
    assert not [t for t in tags if t.startswith("region:")]
    assert "plausible-format" in tags


# --- strict mode: only the enforced regions count as plausible -------------------------


def test_strict_mode_rejects_a_plate_from_another_region():
    gb = next(f for f in FORMATS if f.code == "gb")
    assert plausible_format(gb.examples[0], regions=["gb"])
    assert not plausible_format("ABC123", regions=["gb"])
    tags = tag_plate("ABC123", 0.9, regions=["gb"])
    assert "odd-format" in tags and "needs-review" in tags


def test_strict_mode_accepts_any_of_several_regions():
    gb = next(f for f in FORMATS if f.code == "gb")
    fr = next(f for f in FORMATS if f.code == "fr")
    both = ["gb", "fr"]
    assert plausible_format(gb.examples[0], regions=both)
    assert plausible_format(fr.examples[0], regions=both)


def test_unknown_region_code_is_rejected_loudly():
    with pytest.raises(ValueError, match="unknown"):
        plausible_format("ABC123", regions=["atlantis"])


def test_enforced_regions_comes_from_the_environment(monkeypatch):
    monkeypatch.setattr(formats, "ENFORCED_REGIONS", ("gb",))
    gb = next(f for f in FORMATS if f.code == "gb")
    assert plausible_format(gb.examples[0])
    assert not plausible_format("ABC123")


def test_parse_regions_rejects_a_typo():
    assert formats.parse_regions("") == ()
    assert formats.parse_regions(" gb , fr ") == ("fr", "gb")
    with pytest.raises(ValueError, match="unknown"):
        formats.parse_regions("gb,nowhere")
