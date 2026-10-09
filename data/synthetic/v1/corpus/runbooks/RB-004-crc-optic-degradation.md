---
{"runbook_id": "RB-004", "title": "CRC errors and optical degradation", "categories": ["link_degradation"], "entity_types": ["link"], "version": 3, "last_reviewed": "2025-07-30", "provenance": "synthetic"}
---
# CRC errors and optical degradation

## Symptoms
CRC or input errors rise steadily, often together with packet loss, while utilization stays normal.

## Diagnostics
1. `inspect_interface_counters`. Is the error rate trending up or flat?
2. `check_optic_levels`. Receive power within 2 dB of the low alarm threshold is suspect.

## Remediation (requires approval)
- `schedule_optic_replacement`. This is not reversible once a field visit is dispatched.
- Single-homed access links have no redundant path. Coordinate the replacement window with the customer.
