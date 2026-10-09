import pytest
from pydantic import ValidationError

from opspilot.contracts.evidence import Evidence, EvidenceKind, is_evidence_id


def test_ids_are_content_addressed_and_deterministic():
    a = Evidence.make(
        EvidenceKind.TELEMETRY, "x", "telemetry:a/m", entity_id="a", data={"v": 1.23456}
    )
    b = Evidence.make(
        EvidenceKind.TELEMETRY,
        "different summary",
        "telemetry:a/m",
        entity_id="a",
        data={"v": 1.23461},
    )
    assert a.evidence_id == b.evidence_id  # summary not hashed; floats rounded to 3dp
    assert is_evidence_id(a.evidence_id) and a.evidence_id.startswith("EV-TEL-")
    c = Evidence.make(EvidenceKind.TELEMETRY, "x", "telemetry:a/m", entity_id="a", data={"v": 2})
    assert c.evidence_id != a.evidence_id


def test_evidence_data_must_stay_small():
    with pytest.raises(ValidationError):
        Evidence.make(EvidenceKind.TOPOLOGY, "x", "s", data={str(i): i for i in range(20)})


def test_bad_id_rejected():
    with pytest.raises(ValidationError):
        Evidence(evidence_id="EV-XXX-1", kind=EvidenceKind.TOPOLOGY, summary="s", source="s")
    assert not is_evidence_id("EV-TEL-ZZZZZZZZ")
