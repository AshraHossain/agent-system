"""Labelled dataset for the violation classifier: both labels, no leakage, stable split."""

from __future__ import annotations

import pytest

from ledger_hound.qa.labels import build_dataset, split


@pytest.fixture(scope="module")
async def rows():
    return await build_dataset(stress_n=12)


async def test_dataset_has_both_labels_and_both_sources(rows):
    assert {r["violation"] for r in rows} == {True, False}
    assert {r["id"].split(":")[0] for r in rows} == {"attack", "stress"}


async def test_no_oracle_verdict_leaks_into_model_inputs(rows):
    for r in rows:
        text = " ".join((r["rule"], r["attempt"], r["exchanges"]))
        assert "BREACHED" not in text and "HELD" not in text


async def test_split_is_stratified_and_reproducible(rows):
    train, dev = split(rows, seed=3)
    assert (train, dev) == split(rows, seed=3)
    assert {r["violation"] for r in train} == {r["violation"] for r in dev} == {True, False}
    assert len(train) + len(dev) == len(rows)
