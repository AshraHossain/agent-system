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

**Decision:** do not add the exporter to `pyproject.toml` / `uv.lock`. Keep the
documented `uv run --with ...==1.42.1` path, which affects only that run.

**Consequences:**

* Default installs keep `protobuf` 7; nothing changes for users who do not trace.
* Tracing users type a longer command; the CLI already fails fast with the exact
  `uv run --with` instruction when an OTLP endpoint is set without the exporter.
* **Revisit trigger:** a `google-adk` release that accepts `opentelemetry-sdk>=1.43`.
  Then an `otel` extra with `opentelemetry-exporter-otlp-proto-http` no longer
  forces a downgrade and should be added (check with `uv add --optional otel …`
  and compare `protobuf` in `uv.lock` before and after).
