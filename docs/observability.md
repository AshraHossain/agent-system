# Observability: exporting traces with OpenTelemetry

ADK instruments every agent, model call and tool call as OpenTelemetry spans.
With one OTLP endpoint configured they go to any OTLP/HTTP backend (Jaeger,
Grafana Tempo, Honeycomb, an OpenTelemetry Collector, ...). Nothing is exported
unless you ask.

## Quick start

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318   # OTLP over HTTP, not gRPC (4317)
export OTEL_SERVICE_NAME=opspilot

uv run --with 'opentelemetry-exporter-otlp-proto-http==1.42.1' opspilot demo --case C03
uv run --with 'opentelemetry-exporter-otlp-proto-http==1.42.1' opspilot serve
```

* **The exporter is not a project dependency.** `google-adk` ships the hooks but
  leaves the exporter optional, and adding it to `uv.lock` would force `protobuf`
  below 7 for everyone (see [TASK.md](../TASK.md)). `uv run --with` adds it for that
  run only. 1.42.1 is the version tested, and the newest OpenTelemetry release that
  `google-adk` 2.11.0 accepts (it caps `opentelemetry-sdk` at 1.42.1). Without it, the commands below stop at once with this exact
  instruction instead of running untraced.
* A local backend to look at the result (not run here; any OTLP/HTTP receiver works):
  `docker run --rm -p 16686:16686 -p 4318:4318 jaegertracing/all-in-one`, then open
  http://localhost:16686.

## Which commands export

| Command | Exports | How |
|---|---|---|
| `opspilot demo`, `investigate`, `resume`, `eval` | yes | `observability.configure_tracing()` calls ADK's own setup; the CLI runs agents without ADK's server, which would otherwise do it |
| `opspilot serve` | yes | ADK sets it up when it builds the API server; OpsPilot only checks that the exporter is installed |
| `adk web`, `adk api_server` | yes | same ADK setup (run with `uv run --with ... adk api_server adk_apps`; not run separately here) |
| `opspilot list`, `show`, `build-data` | no | they run no agents |

Setting an `OTEL_EXPORTER_OTLP_*` endpoint without the exporter installed exits
with status 2 (for the commands above) rather than silently dropping traces.

## Configuration

| Variable | Effect |
|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Base URL; ADK sends traces to `/v1/traces` and also logs to `/v1/logs` (and metrics) |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | Full URL including `/v1/traces`; sends **traces only** |
| `OTEL_SERVICE_NAME` | `service.name` on every span |
| `OTEL_BSP_SCHEDULE_DELAY` | Batch interval in ms (default 5000); see "Shutdown" below |
| `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false` | Empties prompt, response and tool-result attributes; see "What the spans contain" |
| `OTEL_EXPORTER_OTLP_HEADERS`, `OTEL_RESOURCE_ATTRIBUTES` | Standard OpenTelemetry variables read by the SDK (credentials for hosted backends, extra resource attributes); not exercised by the tests here |

ADK always uses the OTLP/HTTP protobuf exporter, so `OTEL_EXPORTER_OTLP_PROTOCOL`
does not switch it to gRPC.

## What is exported

One mock investigation of `C02` produces about 65 spans: `invoke_agent <agent>` for
each agent in the tree, `call_llm` and `generate_content` for each model call,
`execute_tool <tool>` for each tool call, and one `invocation` root. Useful
attributes: `gen_ai.agent.name`, `gen_ai.tool.name`, `gen_ai.request.model`
(`scripted-mock` offline), and `gen_ai.conversation.id`.

**`gen_ai.conversation.id` is the run id.** It equals the `session:` line printed by
the CLI and the `{run_id}` in `GET /runs/{run_id}` (ADR-0010), so you can go from a
report to its trace and back. The mock model returns no token usage, so token
attributes appear only with a live model.

## What the spans contain

By default the spans carry **content**: `gcp.vertex.agent.llm_request`,
`llm_response` and `tool_response` hold the full prompts, model output and tool
results (telemetry summaries, document snippets, evidence). Requests and documents
are redacted before they reach the model, but a trace backend then holds
investigation content, so treat it like the session database. To export structure
and timing only:

```bash
export ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false
```

Every content attribute is then `{}` (or ADK's `N/A` placeholder). Both behaviours
are covered by `tests/test_tracing.py`.

## Caveats

* **Shutdown.** Spans are sent in batches every 5 seconds. A server stopped with
  `SIGTERM` (`docker stop`, Kubernetes) can lose the last batch, because uvicorn
  re-raises the signal and Python skips its exit hooks; `SIGINT` flushes.
  `OTEL_BSP_SCHEDULE_DELAY=500` shrinks the window (checked: a run followed 2 s later
  by `SIGTERM` exported nothing at the default and everything at 500). CLI commands
  flush when the process exits normally.
* **Not run against a real backend.** The tests and the checks behind this page use
  a local OTLP/HTTP receiver, which proves what is sent, not how a given backend
  displays it.
* The Docker image does not include the exporter.
* If a `TracerProvider` is already set by other code in the same process, ADK leaves
  it alone and logs a warning; the exporter configured here is then not used.

## Testing it

`tests/test_tracing.py` runs the real CLI and the real server against an in-process
OTLP receiver. The end-to-end tests skip when the exporter is missing, so run them with:

```bash
uv run --with 'opentelemetry-exporter-otlp-proto-http==1.42.1' pytest tests/test_tracing.py
```
