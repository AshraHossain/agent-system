"""Tests for tools/calculator_tool.py and tools/search_tool.py (offline, no API keys)."""

from tools.calculator_tool import calculator_tool
from tools.search_tool import search_tool


def test_calculator_tool_basic_arithmetic():
    assert calculator_tool("2 + 2") == "4"
    assert calculator_tool("10 / 4") == "2.5"
    assert calculator_tool("2 ** 3") == "8"


def test_calculator_tool_nested_expression():
    assert calculator_tool("(2 + 3) * 4") == "20"


def test_calculator_tool_negative_numbers():
    assert calculator_tool("-5 + 3") == "-2"


def test_calculator_tool_rejects_non_numeric_syntax():
    assert calculator_tool("not an expression") == "Error in calculation"


def test_calculator_tool_blocks_code_execution():
    # Would run arbitrary code under eval(); the safe evaluator must reject it.
    assert calculator_tool("__import__('os').system('echo pwned')") == "Error in calculation"
    assert calculator_tool("[].__class__") == "Error in calculation"


def test_search_tool_echoes_query():
    assert "hello" in search_tool("hello")
