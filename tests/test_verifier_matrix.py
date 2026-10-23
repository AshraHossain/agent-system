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


def test_closed_blind_spots_stay_closed(matrix):
    """Gaps found by the first matrix run and closed in core/verification.py."""
    m = matrix["mutations"]
    assert m["false_all_clear"]["flagged"] == 2  # summary_contradicts_findings
    assert m["swapped_ranking"]["flagged_cases"] == ["C05"]  # ranking_differs_from_rules (tie)
    assert m["blamed_neighbour"]["missed"] == 0
    assert m["dropped_service"]["neutralized"] == 2  # finalizer rebuilds the service list
    rows = {(r["mutation"], r["case"]): r for r in matrix["rows"]}
    assert "summary_contradicts_findings:error" in rows["false_all_clear", "C03"]["signals"]
    assert rows["swapped_ranking", "C05"]["signals"] == ["ranking_differs_from_rules:warning"]


def test_remaining_blind_spot_is_reported_not_hidden(matrix):
    """Paraphrased overconfidence needs a language judgement: the live reviewer's job.
    Pinned so that closing it shows up as a deliberate test change."""
    assert matrix["mutations"]["paraphrased_certainty"]["missed"] == 2


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
