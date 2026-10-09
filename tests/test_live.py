"""Live Gemini tests. Skipped unless explicitly selected AND credentials exist:

    GOOGLE_API_KEY=... uv run pytest -m live

They record the model in the report metrics; results are not asserted against
the strict offline thresholds (live models are nondeterministic).
"""

import os
from dataclasses import replace

import pytest

pytestmark = pytest.mark.live

HAS_CREDS = bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_GENAI_USE_VERTEXAI"))


@pytest.mark.skipif(not HAS_CREDS, reason="no Gemini credentials")
async def test_live_investigation_produces_valid_report(run_case, settings):
    live = replace(settings, provider="gemini", model=os.getenv("OPSPILOT_MODEL", settings.model))
    out = await run_case("C03", settings_override=live)
    r = out.report
    assert r.run_metrics.provider == "gemini"
    assert r.status.value in {"investigated", "inconclusive", "requires_human_review", "failed"}
    assert r.verification is not None
    assert all(f"evidence:{e.evidence_id}" in out.state for e in r.supporting_evidence)
