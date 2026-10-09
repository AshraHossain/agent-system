"""Sanitization and quarantine of untrusted retrieved text.

Retrieved runbooks, past incidents and operator free text are *data*. We
never delete suspicious content (the evidence must stay faithful), but we:

* strip control and bidirectional-override characters and cap the length;
* flag instruction-like patterns so downstream code and reviewers can see them;
* wrap text in explicit delimiters when it goes into a prompt, after
  neutralising any attempt to close the delimiter early.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏  ‪-‮⁦-⁩]")
_WS = re.compile(r"[ \t]+")

INJECTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "ignore_instructions": re.compile(
        r"\b(ignore|disregard|forget|override)\b[^.\n]{0,60}\b(instructions?|polic(y|ies)|rules|prompt|guidance)\b",
        re.I,
    ),
    "role_override": re.compile(r"\b(system override|system prompt|you are now|note to ai|ai assistant)\b", re.I),
    "imperative_action": re.compile(
        r"\b(immediately|now)\b[^.\n]{0,40}\b(execute|run|perform|approve|restart|reboot|roll ?back)\b"
        r"|\b(execute|approve)\b[^.\n]{0,40}\b(restart_device|rollback_config_change|restart|reboot)\b",
        re.I,
    ),
    "bypass_approval": re.compile(r"\bwithout (any )?(human )?(approval|review|authori[sz]ation)\b", re.I),
    "forced_conclusion": re.compile(
        r"\b(root cause is always|mark (the )?incident (as )?resolved|classify this as)\b", re.I
    ),
}

OPEN_TAG = "<untrusted_document"
CLOSE_TAG = "</untrusted_document>"


class SanitizedText(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    truncated: bool
    injection_flags: list[str]


def sanitize(text: str, max_chars: int = 2000) -> SanitizedText:
    cleaned = _CONTROL.sub("", text)
    cleaned = "\n".join(_WS.sub(" ", line).rstrip() for line in cleaned.splitlines())
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    flags = sorted(name for name, rx in INJECTION_PATTERNS.items() if rx.search(cleaned))
    truncated = len(cleaned) > max_chars
    return SanitizedText(text=cleaned[:max_chars], truncated=truncated, injection_flags=flags)


def untrusted_block(doc_id: str, text: str) -> str:
    """Delimit untrusted text for a prompt. Embedded delimiters are defanged."""
    safe_id = re.sub(r"[^A-Za-z0-9_.:-]", "", doc_id)[:64]
    body = re.sub(r"</?\s*untrusted_document", "[delimiter removed]", text, flags=re.I)
    return f'{OPEN_TAG} id="{safe_id}" trust="untrusted">\n{body}\n{CLOSE_TAG}'
