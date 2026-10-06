"""Append-only JSON-Lines persistence for AgentState runs.

Each /run call's final state is appended as one line; nothing is mutated
in place, so concurrent writers can't corrupt previous records.
"""

import json
import uuid
from pathlib import Path
from typing import List, Optional

from app.state import AgentState

STORE_PATH = Path(__file__).parent.parent / "data" / "runs.jsonl"


def save_run(state: AgentState) -> str:
    """Append a run's final state to the store, returning its run_id."""
    run_id = str(uuid.uuid4())
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {"run_id": run_id, **state}
    with STORE_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")
    return run_id


def load_run(run_id: str) -> Optional[dict]:
    """Return the stored record for run_id, or None if it isn't found."""
    if not STORE_PATH.exists():
        return None
    with STORE_PATH.open() as f:
        for line in f:
            record = json.loads(line)
            if record["run_id"] == run_id:
                return record
    return None


def list_runs() -> List[str]:
    """Return all stored run_ids, oldest first."""
    if not STORE_PATH.exists():
        return []
    with STORE_PATH.open() as f:
        return [json.loads(line)["run_id"] for line in f]
