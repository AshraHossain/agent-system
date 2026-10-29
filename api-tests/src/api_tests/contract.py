"""OpenAPI document preparation for contract fuzzing.

The served spec has known gaps (see docs/FINDINGS.md). Fuzzing the raw spec
would fail on those same gaps every run and hide anything new, so each gap is
patched here under its finding ID, and a strict-xfail test in
``tests/known_defects/`` keeps the gap itself visible. When the service fixes
one, that xfail starts passing, the run fails, and the patch should go.
"""

from __future__ import annotations

import copy
from typing import Any

# KD-02: horizon/days have no upper bound; huge values take minutes to answer.
FUZZ_BOUNDS = {"horizon": (1, 180), "days": (0.01, 30.0)}

# Operations whose path parameters can name a missing segment.
_PATH_PARAM_OPS = ("/forecast/{segment_id}", "/history/{segment_id}", "/path/{source}/{target}")

# Never fuzzed: an endless SSE response.
EXCLUDED_PATHS = ("/stream",)


def _walk(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def patched_spec(spec: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(spec)
    paths = out.get("paths", {})

    for path in EXCLUDED_PATHS:
        paths.pop(path, None)

    # KD-05: 404 is returned but not documented.
    for path in _PATH_PARAM_OPS:
        for op in paths.get(path, {}).values():
            op.setdefault("responses", {}).setdefault(
                "404",
                {
                    "description": "Not found",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"detail": {"type": "string"}},
                                "required": ["detail"],
                            }
                        }
                    },
                },
            )

    # KD-02: bound the parameters fuzzing may generate.
    for op in (op for item in paths.values() for op in item.values()):
        for param in op.get("parameters", []):
            if param.get("name") in FUZZ_BOUNDS:
                low, high = FUZZ_BOUNDS[param["name"]]
                param["schema"].update(minimum=low, maximum=high)

    # KD-08: /ask accepts any string map as a history item but 500s without
    # "role" and "content"; fuzz only the items it can handle.
    ask = out.get("components", {}).get("schemas", {}).get("AskRequest", {})
    for variant in ask.get("properties", {}).get("history", {}).get("anyOf", []):
        if variant.get("type") == "array":
            variant["items"]["required"] = ["role", "content"]
            variant["items"]["properties"] = {
                "role": {"type": "string"},
                "content": {"type": "string"},
            }

    # KD-06: timestamps are served without a UTC offset, which RFC 3339
    # "date-time" requires. Shape and type are still checked.
    for node in _walk(out):
        if node.get("format") == "date-time":
            del node["format"]

    return out
