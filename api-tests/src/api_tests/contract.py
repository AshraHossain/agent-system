"""OpenAPI document preparation for contract fuzzing.

When the served spec has a known gap (see docs/FINDINGS.md), fuzzing the raw
spec fails on that same gap every run and hides anything new. Patch such a gap
here under its finding ID while a strict-xfail test in ``tests/known_defects/``
keeps it visible; remove the patch when the finding is fixed. None are open now.
"""

from __future__ import annotations

import copy
from typing import Any

# Never fuzzed: an endless SSE response.
EXCLUDED_PATHS = ("/stream",)


def patched_spec(spec: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(spec)
    paths = out.get("paths", {})
    for path in EXCLUDED_PATHS:
        paths.pop(path, None)
    return out
