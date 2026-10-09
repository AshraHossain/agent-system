"""Logging setup. ADK itself emits OpenTelemetry spans for agent/model/tool calls;
configure an OTel exporter through the standard OTEL_* environment variables to
collect them. OpsPilot adds structured log lines (optionally JSON) for model and
tool calls, budget events and run outcomes. Secrets are never logged: requests
are redacted at intake and tool arguments are not logged.
"""

from __future__ import annotations

import json
import logging
import os


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "ts": self.formatTime(record),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            }
        )


def configure_logging(level: str | None = None) -> None:
    level = level or os.getenv("OPSPILOT_LOG_LEVEL", "WARNING")
    handler = logging.StreamHandler()
    if os.getenv("OPSPILOT_LOG_JSON") == "1":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # ADK warns on every mock call without token usage; that is expected offline.
    logging.getLogger("google_adk.google.adk.telemetry._metrics").setLevel(logging.ERROR)
