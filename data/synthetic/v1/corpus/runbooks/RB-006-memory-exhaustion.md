---
{"runbook_id": "RB-006", "title": "Memory exhaustion", "categories": ["device_memory_exhaustion"], "entity_types": ["node"], "version": 2, "last_reviewed": "2025-06-18", "provenance": "synthetic"}
---
# Memory exhaustion

## Symptoms
Memory climbs steadily over hours (a leak) and does not follow the daily traffic pattern. Late stage: sessions drop or the device reloads.

## Diagnostics
1. `check_memory_allocation`. Identify the process that is growing.
2. Firewalls: `check_firewall_session_table`. Is the session table growing, or a process?

## Remediation (requires approval)
- `shift_traffic_to_redundant_path` away from the affected device, if redundant.
- A process or device restart requires a senior operator and a maintenance window.
