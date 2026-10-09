---
{"runbook_id": "RB-005", "title": "High CPU on routers and switches", "categories": ["device_cpu_saturation", "configuration_change"], "entity_types": ["node"], "version": 6, "last_reviewed": "2025-11-11", "provenance": "synthetic"}
---
# High CPU on routers and switches

## Symptoms
CPU above 90% for 10 minutes or more. Services that traverse the device show added latency.

## Diagnostics
1. `check_device_process_table`. Which process is consuming CPU?
2. `review_recent_config_changes`. A recent ACL or policy change can punt traffic to the CPU.
3. `run_path_trace` for affected services.

## Remediation (requires approval)
- If a recent change correlates: `rollback_config_change`, which needs a senior operator.
- `restart_device` is a last resort. It is not reversible, has a high blast radius, and needs a senior operator.
