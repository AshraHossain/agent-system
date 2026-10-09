"""Read-only operating policy for recommendations.

OpsPilot may recommend *observation* and *escalation* only. Any step that would
change device state must be routed to humans via an escalation/change request.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

READ_ONLY_VERBS = (
    "show",
    "check",
    "review",
    "compare",
    "confirm",
    "verify",
    "inspect",
    "identify",
    "correlate",
    "monitor",
    "query",
    "collect",
    "gather",
    "examine",
    "analyze",
    "analyse",
    "measure",
    "list",
    "trace",
    "look",
    "observe",
    "read",
    "validate",
    "determine",
)
ESCALATION_VERBS = ("escalate", "notify", "request", "open", "page", "raise", "engage")

MUTATING = re.compile(
    r"\b(reboot|reload|restart|shut\s*down|shutdown|no\s+shutdown|power[\s-]?cycle|"
    r"power\s+(off|down)|move\s+traffic|"
    r"clear\s+(counters?|interface|arp|bgp|sessions?|cache)|configure|conf\s+t|"
    r"delete|erase|wipe|write\s+mem(ory)?|copy\s+run|apply|roll\s*back|rollback|"
    r"disable|enable|drain|fail\s*over|failover|swap|replace|reseat|re-?route|"
    r"update\s+(the\s+)?(config|firmware)|upgrade|downgrade|kill|flush|reset|"
    r"block|unblock|null[\s-]?route|commit)\b",
    re.I,
)
SHELL = re.compile(r"(`|\bsudo\b|\brm\s+-|&&|\|\||;\s*\w+\s*=|\$\()")
APPROVAL = re.compile(
    r"\b(change[\s-]?approved|approval|change\s+(request|process|control)|"
    r"for\s+humans?|human[\s-]?approved)\b",
    re.I,
)


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str


def check_step(step: str) -> PolicyDecision:
    text = (step or "").strip()
    if not text:
        return PolicyDecision(False, "empty step")
    if SHELL.search(text):
        return PolicyDecision(False, "contains command/shell syntax")
    first = re.split(r"\W+", text.lower(), maxsplit=1)[0]
    mutation = MUTATING.search(text)
    if first in ESCALATION_VERBS:
        if mutation and not APPROVAL.search(text):
            return PolicyDecision(
                False, f"escalation mentions '{mutation.group(0)}' without a change-approval path"
            )
        return PolicyDecision(True, "escalation to humans")
    if first not in READ_ONLY_VERBS:
        return PolicyDecision(False, f"step must start with a read-only verb, got '{first}'")
    if mutation:
        return PolicyDecision(False, f"mutating action '{mutation.group(0)}' is not permitted")
    return PolicyDecision(True, "read-only observation")
