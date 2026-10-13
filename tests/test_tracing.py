"""OpenTelemetry trace export (docs/observability.md).

The unit tests need nothing extra. The end-to-end tests run the real CLI in a
subprocess against a local OTLP/HTTP receiver, so they need the exporter:

    uv run --with 'opentelemetry-exporter-otlp-proto-http==1.42.1' pytest tests/test_tracing.py
"""

import http.server
import os
import re
import subprocess
import sys
import threading
import types

import pytest

from opspilot import cli, observability
from opspilot.observability import (
    EXPORTER_REQUIREMENT,
    OTLP_ENDPOINT_VARS,
    TracingUnavailable,
    configure_tracing,
    tracing_requested,
)

EXPORTER_MODULE = "opentelemetry.exporter.otlp.proto.http.trace_exporter"


def exporter_installed() -> bool:
    try:
        __import__(EXPORTER_MODULE)
    except ImportError:
        return False
    return True


requires_exporter = pytest.mark.skipif(
    not exporter_installed(), reason=f"needs {EXPORTER_REQUIREMENT}"
)


@pytest.fixture(autouse=True)
def clean_otel_env(monkeypatch):
    for name in (*OTLP_ENDPOINT_VARS, "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS", "OTEL_SERVICE_NAME"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def adk_setup(monkeypatch):
    """Record calls to ADK's provider setup instead of installing global providers."""
    calls = []
    monkeypatch.setattr(
        "google.adk.telemetry.setup.maybe_set_otel_providers", lambda *a, **k: calls.append(1)
    )
    return calls


@pytest.fixture
def exporter_present(monkeypatch):
    monkeypatch.setitem(sys.modules, EXPORTER_MODULE, types.ModuleType(EXPORTER_MODULE))


@pytest.fixture
def exporter_absent(monkeypatch):
    monkeypatch.setitem(sys.modules, EXPORTER_MODULE, None)  # makes the import raise


# --- configure_tracing ---------------------------------------------------------------------


@pytest.mark.parametrize("var", OTLP_ENDPOINT_VARS)
def test_any_otlp_endpoint_variable_requests_tracing(monkeypatch, var):
    assert not tracing_requested()
    monkeypatch.setenv(var, "http://collector:4318")
    assert tracing_requested()


def test_nothing_happens_without_an_endpoint(adk_setup, exporter_absent):
    assert configure_tracing() is False
    assert adk_setup == []


def test_an_endpoint_installs_adks_providers(monkeypatch, adk_setup, exporter_present):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    assert configure_tracing() is True
    assert adk_setup == [1]


def test_check_only_mode_leaves_providers_to_adk(monkeypatch, adk_setup, exporter_present):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://collector:4318/v1/traces")
    assert configure_tracing(install_providers=False) is True
    assert adk_setup == []


def test_a_missing_exporter_fails_fast_with_the_fix(monkeypatch, adk_setup, exporter_absent):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    with pytest.raises(TracingUnavailable) as err:
        configure_tracing()
    assert f"uv run --with '{EXPORTER_REQUIREMENT}'" in str(err.value)
    assert adk_setup == []


# --- CLI wiring ---------------------------------------------------------------------------------


@pytest.fixture
def traced(monkeypatch):
    """Stub the command handlers and record how tracing was configured for each run."""
    calls = []
    monkeypatch.setattr(cli, "configure_tracing", lambda **kw: calls.append(kw) or True)

    async def ok(args, settings):
        return 0

    for handler in ("_demo", "_investigate", "_resume", "_list", "_show", "_eval"):
        monkeypatch.setattr(cli, handler, ok)
    monkeypatch.setattr(cli, "_serve", lambda args, settings: 0)
    return calls


@pytest.mark.parametrize(
    "argv",
    [
        ["demo"],
        ["investigate", "--dataset", "C02", "--request", "x"],
        ["resume", "sid"],
        ["eval"],
    ],
)
def test_commands_that_run_investigations_export_traces(traced, argv):
    assert cli.main(argv) == 0
    assert traced == [{"install_providers": True}]


@pytest.mark.parametrize("argv", [["list"], ["show", "sid"], ["build-data", "C01"]])
def test_other_commands_never_touch_tracing(traced, monkeypatch, tmp_path, argv):
    monkeypatch.setenv("OPSPILOT_DATA_DIR", str(tmp_path))
    cli.main(argv)
    assert traced == []


def test_serve_only_checks_because_adk_installs_its_own_exporters(traced):
    assert cli.main(["serve"]) == 0
    assert traced == [{"install_providers": False}]


def test_a_tracing_failure_stops_the_command_before_any_work(monkeypatch, capsys):
    ran = []

    def refuse(**kw):
        raise TracingUnavailable("exporter missing")

    async def demo(args, settings):
        ran.append(1)
        return 0

    monkeypatch.setattr(cli, "configure_tracing", refuse)
    monkeypatch.setattr(cli, "_demo", demo)
    assert cli.main(["demo"]) == 2
    assert "error: exporter missing" in capsys.readouterr().err and ran == []


def test_serve_with_an_endpoint_and_no_exporter_exits_cleanly(monkeypatch, capsys, exporter_absent):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    monkeypatch.setattr(observability, "tracing_requested", lambda: True)
    assert cli.main(["serve"]) == 2
    assert "uv run --with" in capsys.readouterr().err


# --- end to end: the real CLI exporting to a local OTLP/HTTP receiver ---------------------


class Receiver:
    def __init__(self):
        self.requests: list[tuple[str, bytes]] = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                outer.requests.append((self.path, self.rfile.read(length)))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    @property
    def paths(self) -> set[str]:
        return {path for path, _ in self.requests}

    def spans(self) -> list[dict]:
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
            ExportTraceServiceRequest,
        )

        out = []
        for path, body in self.requests:
            if path != "/v1/traces":
                continue
            request = ExportTraceServiceRequest()
            request.ParseFromString(body)
            for resource_spans in request.resource_spans:
                service = {a.key: a.value.string_value for a in resource_spans.resource.attributes}
                for scope_spans in resource_spans.scope_spans:
                    for span in scope_spans.spans:
                        attrs = {a.key: a.value.string_value for a in span.attributes}
                        out.append({"name": span.name, "service": service, "attrs": attrs})
        return out


def run_cli(data_dir, tmp_path, otel_env, *args):
    receiver = Receiver()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "ADK_CAPTURE"))}
    env.update(
        OPSPILOT_MODEL_PROVIDER="mock",
        OPSPILOT_DATA_DIR=str(data_dir),
        OPSPILOT_SESSION_DB=f"sqlite+aiosqlite:///{tmp_path / 'sessions.db'}",
        OTEL_SERVICE_NAME="opspilot-test",
    )
    env.update({k: v.format(url=receiver.url) for k, v in otel_env.items()})
    result = subprocess.run(
        [sys.executable, "-m", "opspilot.cli", "demo", "--case", "C02", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    receiver.server.shutdown()
    assert result.returncode == 0, result.stderr[-500:]
    return result, receiver


@pytest.fixture(scope="module")
def module_tmp(tmp_path_factory):
    return tmp_path_factory.mktemp("tracing")


@pytest.fixture(scope="module")
def default_run(data_dir, module_tmp):
    return run_cli(data_dir, module_tmp, {"OTEL_EXPORTER_OTLP_ENDPOINT": "{url}"})


@pytest.fixture(scope="module")
def minimal_run(data_dir, module_tmp):
    return run_cli(
        data_dir,
        module_tmp,
        {
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "{url}/v1/traces",
            "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS": "false",
        },
        "--no-persist",
    )


CONTENT_ATTRS = (
    "gcp.vertex.agent.llm_request",
    "gcp.vertex.agent.llm_response",
    "gcp.vertex.agent.tool_call_args",
    "gcp.vertex.agent.tool_response",
)
PLACEHOLDERS = ("", "{}", "N/A")  # what ADK writes instead of content


@requires_exporter
def test_cli_run_exports_agent_model_and_tool_spans(default_run):
    _, receiver = default_run
    kinds = {span["name"].split(" ")[0] for span in receiver.spans()}
    assert {"invoke_agent", "call_llm", "execute_tool"} <= kinds
    assert {span["service"]["service.name"] for span in receiver.spans()} == {"opspilot-test"}
    assert "/v1/traces" in receiver.paths


@requires_exporter
def test_the_trace_carries_the_run_id_that_get_runs_uses(default_run):
    result, receiver = default_run
    run_id = re.search(r"session: (\S+) \(resume with", result.stdout).group(1)
    conversation_ids = {s["attrs"].get("gen_ai.conversation.id") for s in receiver.spans()}
    assert run_id in conversation_ids


@requires_exporter
def test_message_content_is_exported_unless_switched_off(default_run, minimal_run):
    def captured(receiver):
        return {
            value
            for span in receiver.spans()
            for key, value in span["attrs"].items()
            if key in CONTENT_ATTRS and value not in PLACEHOLDERS
        }

    assert captured(default_run[1])  # prompts and tool results leave the process by default
    assert not captured(minimal_run[1])


@requires_exporter
def test_a_traces_only_endpoint_sends_nothing_else(minimal_run):
    _, receiver = minimal_run
    assert receiver.paths == {"/v1/traces"} and receiver.spans()


def free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@requires_exporter
def test_opspilot_serve_exports_traces_keyed_by_the_run_id(data_dir, tmp_path):
    import json
    import signal
    import time
    import urllib.request

    from opspilot.datasets.spec import get_case

    receiver, port = Receiver(), free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "ADK_CAPTURE"))}
    env.update(
        OPSPILOT_MODEL_PROVIDER="mock",
        OPSPILOT_DATA_DIR=str(data_dir),
        OPSPILOT_SESSION_DB=f"sqlite+aiosqlite:///{tmp_path / 'sessions.db'}",
        OTEL_SERVICE_NAME="opspilot-serve-test",
        OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=f"{receiver.url}/v1/traces",
    )

    def call(method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=data,
            method=method,
            headers={"content-type": "application/json"},
        )
        return json.load(urllib.request.urlopen(request, timeout=120))

    with open(tmp_path / "serve.log", "w") as log:
        server = subprocess.Popen(
            [sys.executable, "-m", "opspilot.cli", "serve", "--port", str(port)],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            for _ in range(60):
                try:
                    call("GET", "/list-apps")
                    break
                except OSError:
                    time.sleep(1)
            call("POST", "/apps/opspilot_ai/users/u1/sessions/trace-run", {"dataset_id": "C02"})
            message = {"role": "user", "parts": [{"text": get_case("C02")["request"]}]}
            call(
                "POST",
                "/run",
                {
                    "app_name": "opspilot_ai",
                    "user_id": "u1",
                    "session_id": "trace-run",
                    "new_message": message,
                },
            )
            assert call("GET", "/runs/trace-run")["status"] == "investigated"
        finally:
            server.send_signal(signal.SIGINT)  # SIGTERM can drop the last unflushed batch
            server.wait(timeout=60)
            receiver.server.shutdown()

    spans = receiver.spans()
    assert receiver.paths == {"/v1/traces"} and spans
    assert {s["service"]["service.name"] for s in spans} == {"opspilot-serve-test"}
    assert "trace-run" in {s["attrs"].get("gen_ai.conversation.id") for s in spans}
