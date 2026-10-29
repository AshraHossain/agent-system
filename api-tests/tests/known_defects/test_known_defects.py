"""Defects this suite found in the target, pinned as strict xfails.

Each test asserts the *correct* behaviour. While the defect exists it xfails;
once the service is fixed it passes, strict mode turns that into a failure,
and the test moves into the regular suite as a regression guard (and any
patch in ``api_tests.contract`` goes). Details: docs/FINDINGS.md.

No findings are open right now; KD-01..KD-08 are fixed and their tests live in
``negative/``, ``functional/`` and ``contract/``. Pin a new one like this::

    @kd("KD-09", "what is wrong, in one line")
    def test_correct_behaviour(api): ...
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.known_defect


def kd(finding: str, summary: str):
    return pytest.mark.xfail(reason=f"{finding}: {summary}", strict=True)
