"""Tamper-evident audit log: an HMAC-SHA256 hash chain over the audit rows.

Each row stores ``prev_hash`` (the previous row's ``row_hash``, or ``GENESIS`` for the first chained row) and
``row_hash = HMAC(key, [seq, incident_id, event, actor, payload, at, prev_hash])``. Changing, deleting, inserting or
reordering any row breaks the chain from that point on, and an attacker who can edit the database file but does not hold
the key cannot recompute valid hashes.

What this does not protect against:

* **Tail truncation.** Deleting the newest rows leaves a valid shorter chain. Detecting it needs the head
  (``seq:row_hash``) recorded somewhere the attacker cannot write; pass it to ``verify_rows(expected_head=...)``.
* **Key compromise.** Whoever holds the key can forge a chain. Keep it out of the database and the image.
* **Unkeyed mode.** Without a key the hash is plain SHA-256, which catches accidents and naive edits only.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

GENESIS = "0" * 64
MIN_KEY_LENGTH = 32

# (seq, incident_id, event, actor, payload_json, at, prev_hash, row_hash)
Row = Sequence[Any]


def row_hash(
    key: bytes | None, seq: int, incident_id: str, event: str, actor: str, payload: str, at: str, prev: str
) -> str:
    message = json.dumps([seq, incident_id, event, actor, payload, at, prev], separators=(",", ":")).encode()
    if key is None:
        return hashlib.sha256(message).hexdigest()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class AuditVerification:
    ok: bool
    rows: int
    legacy_rows: int
    keyed: bool
    head_seq: int | None
    head_hash: str | None
    error: str | None = None
    first_bad_seq: int | None = None

    @property
    def head(self) -> str | None:
        return f"{self.head_seq}:{self.head_hash}" if self.head_seq is not None and self.head_hash else None


def _fail(msg: str, seq: int | None, total: int, legacy: int, keyed: bool, head: tuple[int | None, str | None]):
    return AuditVerification(False, total, legacy, keyed, head[0], head[1], msg, seq)


def verify_rows(rows: Iterable[Row], key: bytes | None, expected_head: str | None = None) -> AuditVerification:
    """Check ordering, linkage and every hash. ``rows`` must be ordered by ``seq``."""
    keyed = key is not None
    ordered = list(rows)
    total = len(ordered)
    legacy = 0
    prev_seq = 0
    prev_hash = GENESIS
    chained_started = False
    by_seq: dict[int, str] = {}
    head: tuple[int | None, str | None] = (None, None)
    for seq, incident_id, event, actor, payload, at, stored_prev, stored_hash in ordered:
        if seq != prev_seq + 1:
            what = "rows are missing before" if seq > prev_seq + 1 else "out of order at"
            return _fail(f"{what} seq {seq} (expected {prev_seq + 1})", seq, total, legacy, keyed, head)
        if stored_hash is None:  # written before chaining existed; only a leading run is allowed
            if chained_started:
                return _fail(f"unchained row at seq {seq} after chained rows", seq, total, legacy, keyed, head)
            legacy += 1
            prev_seq = seq
            continue
        chained_started = True
        if stored_prev != prev_hash:
            return _fail(f"chain broken at seq {seq}: previous hash does not match", seq, total, legacy, keyed, head)
        expected = row_hash(key, seq, incident_id, event, actor, payload, at, stored_prev)
        if not hmac.compare_digest(expected, stored_hash):
            return _fail(f"row content or key does not match its hash at seq {seq}", seq, total, legacy, keyed, head)
        prev_seq, prev_hash = seq, stored_hash
        by_seq[seq] = stored_hash
        head = (seq, stored_hash)
    result = AuditVerification(True, total, legacy, keyed, head[0], head[1])
    if expected_head is not None:
        try:
            want_seq, want_hash = expected_head.split(":", 1)
            want_seq_i = int(want_seq)
        except ValueError:
            return _fail("expected head must look like <seq>:<hash>", None, total, legacy, keyed, head)
        if by_seq.get(want_seq_i) is None:
            return _fail(
                f"log is shorter than the recorded head (seq {want_seq_i}): rows were truncated",
                None, total, legacy, keyed, head,
            )  # fmt: skip
        if not hmac.compare_digest(by_seq[want_seq_i], want_hash):
            return _fail(
                f"row at recorded head seq {want_seq_i} differs from the record", want_seq_i, total, legacy, keyed, head
            )
    return result
