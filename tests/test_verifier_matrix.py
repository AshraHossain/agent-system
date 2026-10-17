"""Verifier detection matrix: designed-for defects caught, benign edits not flagged.

Runs a slice (two cases) so the suite stays fast; the full matrix is
`uv run opspilot eval --verifier-matrix` (results in docs/eval_results_verifier.md).
"""

from __future__ import annotations

import asyncio

import pytest

from opspilot.eval.verifier_matrix import (
    MUTATIONS,
    STATUS_RANK,
    Signals,
    all_case_ids,
    run_verifier_matrix,
    to_markdown,
)

pytestmark = pytest.mark.slow

SLICE = ["C03", "C05"]  # C05 has two hypotheses, so ranking/support swaps apply


@pytest.fixture(scope="module")
def matrix(settings):
    return asyncio.run(run_verifier_matrix(settings, SLICE))


def test_designed_for_defects_are_caught(matrix):
    for name, p in matrix["mutations"].items():
        if p["kind"] == "catch":
            assert p["applicable"] >= 1, name
            assert p["missed"] == 0, (name, p["missed_cases"])


def test_benign_edits_are_never_flagged(matrix):
    for name, p in matrix["mutations"].items():
        if p["kind"] == "benign":
            assert p["applicable"] >= 1, name
            assert p["flagged"] == 0, (name, p["flagged_cases"])
            assert p["clean"] == p["applicable"]


def test_known_blind_spots_are_reported_not_hidden(matrix):
    """Pins today's gaps so a future fix shows up as a deliberate test change."""
    m = matrix["mutations"]
    assert m["false_all_clear"]["missed"] == 2
    assert m["paraphrased_certainty"]["missed"] == 2
    assert m["swapped_ranking"]["missed_cases"] == ["C05"]
    assert m["dropped_service"]["neutralized"] == 2  # finalizer rebuilds the service list


def test_inapplicable_mutations_are_marked_not_scored(matrix):
    na = [r for r in matrix["rows"] if r["outcome"] == "n/a"]
    assert {r["mutation"] for r in na} >= {"swapped_support", "inflated_confidence"}
    scored = sum(p["applicable"] for p in matrix["mutations"].values())
    assert scored + len(na) == len(MUTATIONS) * len(SLICE)


def test_signals_only_count_what_the_mutation_added():
    clean = Signals(status="inconclusive", codes={"x:warning"}, concerns=1, removed=0)
    same = Signals(status="inconclusive", codes={"x:warning"}, concerns=1, removed=0)
    worse = Signals(status="requires_human_review", codes={"x:warning", "y:error"}, concerns=2)
    assert same.new_since(clean) == []
    assert worse.new_since(clean) == [
        "y:error",
        "review_concern",
        "status:inconclusive->requires_human_review",
    ]
    assert STATUS_RANK["investigated"] < STATUS_RANK["requires_human_review"]


def test_matrix_covers_both_splits_and_renders(matrix):
    ids = all_case_ids()
    assert "C01" in ids and "H01" in ids
    md = to_markdown(matrix)
    assert "false positives on benign edits: **0/" in md
    assert "| `false_all_clear` | blind_spot |" in md


async def test_refuses_live_provider(settings):
    from dataclasses import replace

    with pytest.raises(ValueError, match="mock"):
        await run_verifier_matrix(replace(settings, provider="gemini"), ["C03"])
