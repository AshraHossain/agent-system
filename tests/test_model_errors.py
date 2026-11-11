from opspilot.adk.callbacks import describe_model_error
from opspilot.adk.models import generate_config
from opspilot.config import Limits, Settings


class _ResourceExhaustedError(Exception):
    pass


def test_quota_error_keeps_status_and_detail():
    boilerplate = (
        "\nOn how to mitigate this issue, please refer to:\n\n"
        "https://google.github.io/adk-docs/agents/models/google-gemini/"
        "#error-code-429-resource_exhausted\n\n\n"
    )
    err = _ResourceExhaustedError(
        boilerplate + "429 RESOURCE_EXHAUSTED. Quota exceeded for requests per minute."
    )
    reason = describe_model_error(err)
    assert reason.startswith("model error (_ResourceExhaustedError): 429 RESOURCE_EXHAUSTED")
    assert "requests per minute" in reason
    assert "\n" not in reason


def test_error_without_status_is_collapsed_and_bounded():
    reason = describe_model_error(RuntimeError("boom\n  " + "x" * 1000), limit=50)
    assert reason.startswith("model error (RuntimeError): boom x")
    assert len(reason) <= len("model error (RuntimeError): ") + 50


def test_retry_delays_come_from_limits(monkeypatch):
    monkeypatch.setenv("OPSPILOT_RETRY_INITIAL_DELAY_S", "5")
    monkeypatch.setenv("OPSPILOT_RETRY_MAX_DELAY_S", "30")
    monkeypatch.setenv("OPSPILOT_MAX_MODEL_ATTEMPTS", "5")
    retry = generate_config(Settings.from_env()).http_options.retry_options
    assert (retry.attempts, retry.initial_delay, retry.max_delay) == (5, 5.0, 30.0)


def test_retry_defaults_unchanged():
    limits = Limits()
    assert (limits.max_model_attempts, limits.retry_initial_delay_s, limits.retry_max_delay_s) == (
        2,
        1.0,
        8.0,
    )
