"""Root conftest: ensures the src-layout platewatch package is importable from the monorepo root.

platewatch/ is a standalone package whose sources live in platewatch/src/platewatch/.  Without
this insert the bare platewatch/ directory on sys.path shadows the installed editable package
and makes ``import platewatch.contracts`` etc. fail.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "platewatch" / "src"))
