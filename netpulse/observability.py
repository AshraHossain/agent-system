"""Structured JSON logging with secret redaction, and an explicit opt-in for LangSmith tracing.

| Variable | Default | Meaning |
|---|---|---|
| ``NETPULSE_LOG_LEVEL`` | ``INFO`` | Python log level |
| ``NETPULSE_LOG_FORMAT`` | ``json`` | ``json`` or ``text`` |
| ``NETPULSE_LANGSMITH_TRACING`` | ``false`` | Send traces to LangSmith (also needs ``LANGSMITH_API_KEY``) |

Log records carry identifiers, node names, outcomes and durations. They never carry incident free text,
evidence summaries, prompts or model output. Anything that looks like a credential is masked on the way out.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

LOGGER_NAME = "netpulse"
REDACTED = "[REDACTED]"

_PATTERNS = [
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{4,}"),
    re.compile(r"(?i)\b(authorization|api[_-]?key|token|secret|password)(\s*[=:]\s*)[^\s,;\"']+"),
    re.compile(r"\b(?:sk|lsv2|ghp|xox[bap])[-_][A-Za-z0-9_-]{8,}"),
]
_SENSITIVE_KEYS = re.compile(r"(?i)(authorization|api[_-]?key|token|secret|password)")
_STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime", "taskName"}


def redact(text: str) -> str:
    """Mask bearer tokens, key=value credentials and well-known key prefixes."""
    text = _PATTERNS[0].sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    text = _PATTERNS[1].sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    return _PATTERNS[2].sub(REDACTED, text)


def redact_value(key: str, value: Any) -> Any:
    if _SENSITIVE_KEYS.search(key):
        return REDACTED
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact_value(key, v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "event": redact(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS:
                payload[key] = redact_value(key, value)
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, sort_keys=True)


class RedactingTextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def configure_logging(level: str | None = None, fmt: str | None = None) -> logging.Logger:
    """Install one stderr handler on the ``netpulse`` logger. Safe to call repeatedly."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel((level or os.environ.get("NETPULSE_LOG_LEVEL", "INFO")).upper())
    logger.propagate = False
    for handler in list(logger.handlers):
        if getattr(handler, "_netpulse", False):
            logger.removeHandler(handler)
    handler = logging.StreamHandler()
    handler._netpulse = True  # type: ignore[attr-defined]
    if (fmt or os.environ.get("NETPULSE_LOG_FORMAT", "json")) == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(RedactingTextFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def langsmith_enabled() -> bool:
    """Tracing is off unless explicitly requested AND a key is present. Traces leave this machine."""
    on = os.environ.get("NETPULSE_LANGSMITH_TRACING", "false").strip().lower() in {"1", "true", "yes", "on"}
    return on and bool(os.environ.get("LANGSMITH_API_KEY"))


def apply_tracing_policy() -> bool:
    """Force LangChain/LangGraph tracing to follow ``langsmith_enabled`` so an ambient env var cannot leak data."""
    enabled = langsmith_enabled()
    for var in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"):
        os.environ[var] = "true" if enabled else "false"
    return enabled
