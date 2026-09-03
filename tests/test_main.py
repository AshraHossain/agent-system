"""Tests for agent-system FastAPI app (framework verification, no API keys required)."""

import pytest
from pathlib import Path


def test_pyproject_toml_exists():
    """Verify pyproject.toml exists (build configuration)."""
    assert Path("pyproject.toml").exists()


def test_uv_lock_exists():
    """Verify uv.lock exists (reproducible dependencies)."""
    assert Path("uv.lock").exists()


def test_framework_files_exist():
    """Verify Framework structure is in place."""
    framework_files = [
        "PLANNING.md",
        "TASK.md",
        "CLAUDE.md",
        "CONTRIBUTING.md",
        "README.md",
        "LICENSE",
    ]
    for f in framework_files:
        assert Path(f).exists(), f"Missing {f}"


def test_framework_directories_exist():
    """Verify Framework directories are in place."""
    framework_dirs = ["plugins", "docs"]
    for d in framework_dirs:
        assert Path(d).is_dir(), f"Missing /{d}/ directory"


def test_app_structure():
    """Verify app/ directory has required modules."""
    app_files = [
        "app/main.py",
        "app/state.py",
        "app/graph.py",
        "app/agents.py",
    ]
    for f in app_files:
        assert Path(f).exists(), f"Missing {f}"


def test_dockerfile_exists():
    """Verify Dockerfile exists for containerization."""
    assert Path("Dockerfile").exists()


def test_git_repo_configured():
    """Verify git repository is initialized and configured."""
    assert Path(".git").is_dir()
    assert Path(".gitignore").exists()


def test_known_issue_documented():
    """Verify known OpenAI API key issue is documented in CLAUDE.md."""
    claude_content = Path("CLAUDE.md").read_text()
    assert "OPENAI_API_KEY" in claude_content, "Known issue not documented"
    assert "OPENROUTER_API_KEY" in claude_content, "Known issue not documented"
