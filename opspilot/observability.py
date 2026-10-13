"""Logging and tracing setup. ADK emits OpenTelemetry spans for agent/model/tool
calls; `configure_tracing` exports them over OTLP/HTTP when the standard
OTEL_EXPORTER_OTLP_* variables are set (docs/observability.md). OpsPilot adds
structured log lines (optionally JSON) for model and tool calls, budget events
and run outcomes. Secrets are never logged: requests are redacted at intake and
tool arguments are not logged.
"""

from __future__ import annotations

import json
import logging
import os

OTLP_ENDPOINT_VARS = (
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
)
# Pinned to what google-adk 2.11.0 allows (opentelemetry-sdk <= 1.42.1) and what was tested.
EXPORTER_REQUIREMENT = "opentelemetry-exporter-otlp-proto-http==1.42.1"


class TracingUnavailable(RuntimeError):
    """Trace export was requested through the environment but cannot be set up."""


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


def tracing_requested() -> bool:
    return any(os.getenv(name) for name in OTLP_ENDPOINT_VARS)


def configure_tracing(*, install_providers: bool = True) -> bool:
    """Turn on OTLP export if the environment asks for it; returns whether it did.

    ADK wires this up itself for `adk web`, `adk api_server` and `opspilot serve`
    (pass `install_providers=False` there: only check that export can work). Runs
    started by the CLI go straight to the runner, which does not, so we call ADK's
    own setup. Fails fast rather than silently running untraced.
    """
    if not tracing_requested():
        return False
    try:
        import opentelemetry.exporter.otlp.proto.http.trace_exporter  # noqa: F401
    except ImportError as e:
        raise TracingUnavailable(
            "an OTEL_EXPORTER_OTLP_* variable is set but the OTLP/HTTP exporter is not installed; "
            f"run with: uv run --with '{EXPORTER_REQUIREMENT}' opspilot ..."
        ) from e
    if install_providers:
        from google.adk.telemetry.setup import maybe_set_otel_providers

        maybe_set_otel_providers()
    return True
