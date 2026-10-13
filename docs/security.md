# Security model

The system is read-only and runs on synthetic data. Nothing here changes a network.

| Threat | Control | Test |
|---|---|---|
| Prompt injection in operator text | Free text is untrusted, sanitized, and never sole support for a cause; actions come only from the static catalog; `executed` is always `False` | `tests/test_security.py::test_injected_*` |
| Injection in retrieved runbooks | Retrieved text is sanitized, flagged and marked `trusted=false` | `tests/test_retrieval.py` |
| Label leakage into the investigator | `netpulse/` never imports `synthgen`/`eval` or reads `eval/labels`; the Docker image does not copy them | `test_netpulse_sources_never_reference_ground_truth`, `test_docker_image_excludes_labels_and_generator` |
| Code execution | No `eval`/`exec`/subprocess/pickle in `netpulse/` | `tests/test_repository.py` |
| Spoofed reviewer identity or role | Identity and role come only from the bearer token; role is checked again inside the graph | `tests/test_human_approval.py`, `tests/test_ui_client.py` |
| Open API | `NETPULSE_API_TOKENS` has no default; the API refuses to start without it | `netpulse/api/main.py` |
| Audit log tampering by someone with file access | HMAC-SHA256 hash chain keyed by `NETPULSE_AUDIT_KEY` (kept outside the database); `GET /audit/verify` and `netpulse audit-verify`; writers take the SQLite write lock so two processes cannot fork the chain | `tests/test_audit_chain.py` |
| Secrets in logs | Structured logs carry identifiers, node names, outcomes and durations only; credentials are masked by `netpulse.observability.redact` | `test_redaction_*`, `test_json_logs_*`, `test_node_logs_*` |
| Data leaving the machine via tracing | LangSmith tracing is off unless `NETPULSE_LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` are both set; ambient `LANGSMITH_TRACING` is overridden | `test_tracing_is_off_unless_explicitly_enabled` |
| Path traversal via report names | `GET /eval/reports/{name}` serves only names present in the reports directory | `test_eval_reports_listing_and_fetch` |

Audit limits: truncating the newest rows is only detectable against a head you recorded elsewhere
(`netpulse audit-verify --expect-head <seq>:<hash>`); whoever holds the key can forge a chain; losing the key makes the
existing log unverifiable; without a key the chain is plain SHA-256 and `keyed` is reported false.

Limitations: bearer tokens are static, with no expiry or rotation; the redaction patterns are best effort, not a
guarantee; traces sent to LangSmith contain prompts and state, which is synthetic data here but would not be elsewhere.
