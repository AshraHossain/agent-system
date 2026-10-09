# Security

OpsPilot is **read-only decision support**. Its strongest controls are
architectural; heuristics add defence in depth. Prompt injection is mitigated,
**not solved**.

## Threats and controls

| Threat | Control | Where | Tested by |
|---|---|---|---|
| Agent changes infrastructure | No mutating tools exist; datasets opened SQLite `mode=ro`; tools expose no path/SQL/URL/command parameters | `adk/tools.py`, `core/dataset.py` | `test_dataset_connection_is_read_only`, `test_tools_expose_no_dataset_path_or_command_parameters`, `test_no_tool_can_mutate_state_outside_the_session` |
| Recommendation to change state | Read-only policy (`check_step`): steps must start with an observation verb or escalate through change approval; mutating verbs/shell syntax rejected; finalizer removes violations and forces `requires_human_review` | `core/policy.py`, `core/report.py` | `test_policy.py`, `mutating_recommendation` perturbation |
| Prompt injection in retrieved documents | Redaction → injection-pattern scan → payload withheld; all document text wrapped in `<<untrusted_document>>`; delimiter spoofing neutralised; documents marked `trusted=false`; security rules in every instruction say data ≠ instructions | `core/security.py`, `core/knowledge.py`, `adk/prompts.py` | `test_malicious_document_cannot_steer_the_investigation`, `test_isolation_withholds_payload_and_blocks_delimiter_spoofing` |
| Injection in the user request | Request redacted + flagged at intake; models never see the raw request (`sanitize_user_content` replaces it); security team escalation | `adk/deterministic.py`, `adk/callbacks.py` | `test_injection_in_the_request_is_flagged_not_followed`, `test_raw_request_never_reaches_the_model` |
| Retrieved content overriding policy | Status, escalation and policy are computed by code after all LLM stages; LLM reviewer can only add concerns | `core/report.py` | `test_report.py`, guardrail perturbations |
| Secret leakage | Secrets never in config files (`.env` ignored, `.env.example` only); redaction of keys/tokens/emails in requests, documents and report text; verifier flags secret-like output | `core/security.py`, `core/verification.py` | `test_secrets_never_persist_in_session_state`, `injection_echo_and_secret` perturbation |
| Fabricated evidence / unsupported claims | Content-addressed evidence registry; verifier checks every cited ID, hypothesis support kinds, draft claims ⊆ findings, unknown components | `core/verification.py` | `test_verification.py`, perturbations |
| Conflicting / outdated documents | Deprecated/superseded flags; verifier errors on steps citing them; report excludes them | `core/knowledge.py`, `core/verification.py` | `test_conflicting_deprecated_runbook_is_not_followed` |
| Tool misuse across agents | Per-agent tool allowlist enforced in `BudgetPlugin.before_tool_callback` (in addition to each agent only being given its own tools) | `adk/plugins.py` | `test_cross_agent_tool_use_is_denied` |
| Resource exhaustion | Model/tool/iteration/duration budgets; bounded retries | `adk/plugins.py`, `adk/runner.py` | `test_failures.py` |
| Dataset/path traversal | Dataset IDs regex-validated, resolved path must stay in data dir; dataset bound by runner/intake, not by models | `core/dataset.py`, `adk/runtime.py` | `test_dataset_id_validation` |
| Ground-truth leakage into agents | Labels/fault specs never written to datasets; runner/tools never import case labels | `datasets/generate.py` | `test_ground_truth_never_stored`, `test_agents_never_receive_ground_truth` |

## Known limitations

* Injection detection is regex-based: paraphrased, obfuscated, multilingual or
  encoded payloads can evade it. The architecture limits the blast radius (no
  write tools, code-decided status), but a live model could still be misled
  into a wrong *hypothesis*; the verifier checks support, not truth.
* Redaction patterns cover common key formats only.
* The read-only policy is a verb/keyword filter over English text.
* Session state is stored unencrypted in local SQLite.
* `adk web` has no authentication; keep it on loopback.
