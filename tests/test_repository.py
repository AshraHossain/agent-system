"""Repository-level guards: no dynamic code execution, no executor, legacy scaffold removed."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SOURCES = sorted(p for p in (ROOT / "netpulse").rglob("*.py") if "__pycache__" not in p.parts)


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_dynamic_code_execution_or_shell(path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"eval", "exec", "compile", "__import__"}, path
        if isinstance(node, ast.Import | ast.ImportFrom):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            assert not any(n.split(".")[0] in {"subprocess", "pickle", "paramiko", "netmiko"} for n in names), path


def test_proposed_actions_can_never_be_marked_executed():
    from netpulse.models import ProposedAction

    assert ProposedAction.model_fields["executed"].default is False


def test_legacy_scaffold_is_gone():
    for gone in ("app", "tools", "memory", "requirements.txt", "PLANNING.md", "TASK.md"):
        assert not (ROOT / gone).exists(), gone


def test_required_docs_exist():
    for doc in (
        "README.md",
        "CLAUDE.md",
        "PLAN.md",
        "ARCHITECTURE.md",
        "docs/graph_workflow.md",
        "docs/state_model.md",
        "docs/adr/README.md",
    ):
        assert (ROOT / doc).is_file(), doc
