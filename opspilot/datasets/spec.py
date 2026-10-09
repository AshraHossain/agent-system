"""Loaders for the committed synthetic sources (YAML + Markdown with frontmatter)."""

from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
KNOWLEDGE_DIR = HERE / "knowledge"
EXTRA_KNOWLEDGE_DIR = HERE / "knowledge_extra"


@cache
def topology_spec() -> dict:
    return yaml.safe_load((HERE / "topology.yaml").read_text())


@cache
def cases_spec() -> dict:
    return yaml.safe_load((HERE / "cases" / "cases.yaml").read_text())


def case_ids() -> list[str]:
    return [c["id"] for c in cases_spec()["cases"]]


def get_case(case_id: str) -> dict:
    spec = cases_spec()
    for c in spec["cases"]:
        if c["id"] == case_id:
            merged = dict(c)
            merged.setdefault("request", spec["defaults"]["request"])
            merged.setdefault("reported_at", spec["defaults"]["reported_at"])
            merged.setdefault("run_faults", {})
            return merged
    raise KeyError(case_id)


def parse_markdown_doc(path: Path) -> dict:
    text = path.read_text()
    if not text.startswith("---"):
        raise ValueError(f"{path} lacks frontmatter")
    _, front, body = text.split("---", 2)
    meta = yaml.safe_load(front)
    meta["body"] = body.strip()
    meta["updated"] = str(meta["updated"])
    return meta


def corpus(extra_ids: list[str] | None = None) -> list[dict]:
    docs = [parse_markdown_doc(p) for p in sorted(KNOWLEDGE_DIR.glob("*.md"))]
    for doc_id in extra_ids or []:
        docs.append(parse_markdown_doc(EXTRA_KNOWLEDGE_DIR / f"{doc_id}.md"))
    return docs
