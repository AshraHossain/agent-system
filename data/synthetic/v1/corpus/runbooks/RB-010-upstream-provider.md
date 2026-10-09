---
{"runbook_id": "RB-010", "title": "Suspected upstream provider issue", "categories": ["upstream_dependency"], "entity_types": ["service"], "version": 2, "last_reviewed": "2025-08-02", "provenance": "synthetic"}
---
# Suspected upstream provider issue

## Symptoms
Internet-facing service probes degrade while all internal link and device telemetry stays normal.
`isp-transit` is not monitored, so we have no direct telemetry for it.

## Diagnostics
1. `run_path_trace` toward external destinations.
2. `check_upstream_provider_status`.

## Remediation (requires approval)
- `open_provider_ticket`.
Do not change the internal network to compensate before the upstream fault is confirmed.
