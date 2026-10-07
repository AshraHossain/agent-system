"""Tests for memory/store.py (offline, isolated from the real data/ dir)."""

import memory.store as store_module
from memory.store import save_run, load_run, list_runs


def test_save_and_load_run(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "STORE_PATH", tmp_path / "runs.jsonl")

    state = {"query": "q", "steps": ["a"], "tool_results": ["1"], "errors": [], "attempts": 1, "result": "done"}
    run_id = save_run(state)

    loaded = load_run(run_id)
    assert loaded == {"run_id": run_id, **state}


def test_load_run_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "STORE_PATH", tmp_path / "runs.jsonl")

    assert load_run("does-not-exist") is None


def test_list_runs_oldest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "STORE_PATH", tmp_path / "runs.jsonl")

    first = save_run({"query": "1", "steps": [], "tool_results": [], "errors": [], "attempts": 0, "result": ""})
    second = save_run({"query": "2", "steps": [], "tool_results": [], "errors": [], "attempts": 0, "result": ""})

    assert list_runs() == [first, second]
