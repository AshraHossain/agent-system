# Security model

The system is read-only and runs on synthetic data. Nothing here changes a network.

| Threat | Control | Test |
|---|---|---|
| Prompt injection in operator text | Free text is untrusted, sanitized, and never sole support for a cause; actions come only from the static catalog; `executed` is always `False` | `tests/test_security.py::test_injected_*` |
| Injection in retrieved runbooks | Retrieved text is sanitized, flagged and marked `trusted=false` | `tests/test_retrieval.py` |
| Label leakage into the investigator | `netpulse/` never imports `synthgen`/`eval` or reads `eval/labels`; the Docker image does not copy them | `test_netpulse_sources_never_reference_ground_truth`, `test_docker_image_excludes_labels_and_generator` |
| Code execution | No `eval`/`exec`/subprocess/pickle in `netpulse/` | `tests/test_repository.py` |
| Claiming a reviewer identity on the command line | `netpulse review` goes through the API and takes identity and role from the validated token; `--reviewer`/`--role` exist only under `--local`, which is off unless `NETPULSE_ALLOW_LOCAL_REVIEW=true` and is stamped `channel=local-cli` in the audit | `tests/test_cli_identity.py` |
| Spoofed reviewer identity or role | Identity and role come only from the validated bearer token (OIDC JWT in production); role is checked again inside the graph | `tests/test_human_approval.py`, `tests/test_ui_client.py` |
| One tenant reading or changing another tenant's incidents | Incidents are stored under per-tenant namespaced keys built in one place; another tenant's ID is simply a missing key (404, no existence leak); `~` is rejected in client-supplied IDs; a test requires every `{incident_id}` route to use the scoping dependency | `tests/test_tenancy.py` |
| One tenant analysing another's telemetry | Per-tenant dataset allowlist (`NETPULSE_TENANT_DATASETS`), deny by default in multi-tenant mode | `tests/test_tenancy.py` |
| One person both raising and approving a remediation | Four-eyes: the submitter cannot approve (service, graph node and API all enforce it; denials audited); reject and request-more stay allowed | `tests/test_four_eyes.py` |
| Forged, expired or replayed-after-expiry identity tokens | OIDC: signature against IdP-published keys, asymmetric algorithms only, `iss`/`aud`/`exp`/`nbf`/`sub` required, `kid` required; algorithm-confusion and `alg: none` refused; unmapped users get 403 | `tests/test_oidc.py` |
| Traffic over plain http, header spoofing for scheme or client address | `NETPULSE_REQUIRE_HTTPS` (default on in oidc mode); forwarded headers believed only from `FORWARDED_ALLOW_IPS`; HSTS and security headers | `tests/test_protection.py` |
| Credential brute force and request floods | Per-address limit, lockout after repeated 401s (even for valid tokens from that address), per-identity read/write limits, 256 KiB body cap including chunked uploads | `tests/test_protection.py` |
| Open API | `NETPULSE_API_TOKENS` has no default; the API refuses to start without it | `netpulse/api/main.py` |
| Audit log tampering by someone with file access | HMAC-SHA256 hash chain keyed by `NETPULSE_AUDIT_KEY` (kept outside the database); `GET /audit/verify` and `netpulse audit-verify`; writers take the SQLite write lock so two processes cannot fork the chain | `tests/test_audit_chain.py` |
| Duplicate or lost work when several processes share the database | Job queue in the database: one active job per incident enforced atomically, leases with automatic takeover, schema migrations under the write lock | `tests/test_jobs.py`, `tests/test_persistence_ops.py` |
| Secrets in logs | Structured logs carry identifiers, node names, outcomes and durations only; credentials are masked by `netpulse.observability.redact` | `test_redaction_*`, `test_json_logs_*`, `test_node_logs_*` |
| Data leaving the machine via tracing | LangSmith tracing is off unless `NETPULSE_LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` are both set; ambient `LANGSMITH_TRACING` is overridden | `test_tracing_is_off_unless_explicitly_enabled` |
| Path traversal via report names | `GET /eval/reports/{name}` serves only names present in the reports directory | `test_eval_reports_listing_and_fetch` |

Audit limits: truncating the newest rows is only detectable against a head you recorded elsewhere
(`netpulse audit-verify --expect-head <seq>:<hash>`); whoever holds the key can forge a chain; losing the key makes the
existing log unverifiable; without a key the chain is plain SHA-256 and `keyed` is reported false.

Limitations: OIDC has no revocation (access lasts until the token expires); static development tokens have no expiry or rotation; the redaction patterns are best effort, not a
guarantee; traces sent to LangSmith contain prompts and state, which is synthetic data here but would not be elsewhere.
