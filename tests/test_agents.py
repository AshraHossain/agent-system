"""Tests for app/agents.py helpers that don't require the OpenAI API."""

from app.agents import parse_steps


def test_parse_steps_numbered_list():
    raw = "1. Do the first thing\n2. Do the second thing\n3. Finish up"
    assert parse_steps(raw) == [
        "Do the first thing",
        "Do the second thing",
        "Finish up",
    ]


def test_parse_steps_bullet_list():
    raw = "- Step one\n* Step two"
    assert parse_steps(raw) == ["Step one", "Step two"]


def test_parse_steps_ignores_blank_lines():
    raw = "1. First\n\n2. Second\n\n"
    assert parse_steps(raw) == ["First", "Second"]


def test_parse_steps_falls_back_to_raw_text():
    raw = "just one unstructured sentence with no markers"
    assert parse_steps(raw) == [raw]
