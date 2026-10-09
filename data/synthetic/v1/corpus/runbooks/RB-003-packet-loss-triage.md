---
{"runbook_id": "RB-003", "title": "Packet loss triage", "categories": ["link_degradation", "link_congestion"], "entity_types": ["link"], "version": 5, "last_reviewed": "2025-08-22", "provenance": "synthetic"}
---
# Packet loss triage

## Decision table
| Utilization | Interface errors | Likely cause |
|---|---|---|
| high (>90%) | flat | congestion (see RB-001 / RB-002) |
| normal | rising CRC | physical degradation (see RB-004) |
| normal | flat | marginal optic, or loss elsewhere on the path |

## Diagnostics
1. `inspect_interface_counters` on both link endpoints.
2. `check_optic_levels`.
3. `run_path_trace` for an affected service, to confirm where the loss occurs.

## Remediation (requires approval)
- Physical: `schedule_optic_replacement`. If a redundant path exists, `shift_traffic_to_redundant_path` meanwhile.
