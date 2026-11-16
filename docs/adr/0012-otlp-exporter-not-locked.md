# ADR-0012: Keep the OTLP exporter out of the lockfile until ADK allows OpenTelemetry 1.43+

**Status:** Accepted. Revisit when `google-adk` raises its `opentelemetry-sdk` cap.

Tracing export works today via `uv run --with opentelemetry-exporter-otlp-proto-http==1.42.1`
([observability.md](../observability.md)). The open question in `TASK.md` was whether
to ship the exporter as an `otel` extra instead.

**Finding (checked against PyPI and a trial `uv add --optional otel …`):**

* `google-adk` 2.11.0, and the newer 2.12.0, require `opentelemetry-sdk<=1.42.1`.
* The OTLP exporter must match the SDK version, and `opentelemetry-proto` 1.42.1
  requires `protobuf<7`. `opentelemetry-proto` 1.43.0 and later allow `protobuf<8`.
* `uv` resolves every extra into one universal lockfile, so even an *optional*
  `otel` extra moves `protobuf` from 7.36.2 to 6.33.6 for every install, Docker
  image and CI run, not only for people who enable tracing.

**Decision (Updated):** keep `opentelemetry-exporter-otlp-proto-http==1.42.1` out of
production `[project] dependencies` (so `--no-dev` Docker images only install core
runtime dependencies), and pin `opentelemetry-exporter-otlp-proto-http==1.42.1` inside
`[dependency-groups] dev` so all 23 OpenTelemetry unit and end-to-end subprocess tests
in `tests/test_tracing.py` run and pass unskipped during default `uv run pytest` and
`make check`.

**Consequences:**

* Default developer installs (`uv sync`) run 100% of the OTLP trace test suite with
  zero skipped tests and can export traces immediately with `uv run opspilot ...`.
* Production container builds (`uv sync --frozen --no-dev`) do not install the OTLP
  exporter unless requested; if an `OTEL_EXPORTER_OTLP_*` variable is set without the
  exporter installed, `configure_tracing()` fails fast with the exact `uv run --with`
  instruction.
* **Revisit trigger:** a `google-adk` release that accepts `opentelemetry-sdk>=1.43`.
  Then an `otel` extra with `opentelemetry-exporter-otlp-proto-http` no longer
  forces a downgrade and should be added (check with `uv add --optional otel …`
  and compare `protobuf` in `uv.lock` before and after).
