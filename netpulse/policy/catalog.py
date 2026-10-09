"""Static action catalog: the only actions NetPulse may ever propose."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

CATALOG_PATH = Path(__file__).with_name("catalog.json")


class CatalogAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    catalog_id: str
    kind: Literal["diagnostic", "remediation"]
    description: str
    reversible: bool
    required_role: Literal["none", "operator", "senior_operator"]
    target_type: Literal["link", "node", "service", "any"]
    applies_to: list[str]


@cache
def load_catalog() -> dict[str, CatalogAction]:
    raw = json.loads(CATALOG_PATH.read_text())
    return {a["catalog_id"]: CatalogAction(**a) for a in raw["actions"]}
