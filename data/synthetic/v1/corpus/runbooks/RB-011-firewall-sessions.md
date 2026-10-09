---
{"runbook_id": "RB-011", "title": "Firewall session table pressure", "categories": ["device_memory_exhaustion", "device_cpu_saturation"], "entity_types": ["node"], "version": 1, "last_reviewed": "2025-04-15", "provenance": "synthetic"}
---
# Firewall session table pressure

## Diagnostics
1. `check_firewall_session_table`.
2. `check_memory_allocation`.
3. `check_device_process_table`.

## Remediation (requires approval)
- `shift_traffic_to_redundant_path` outside business hours (see RB-002).
