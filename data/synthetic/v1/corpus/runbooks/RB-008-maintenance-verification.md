---
{"runbook_id": "RB-008", "title": "Alarms during planned maintenance", "categories": ["maintenance_side_effect"], "entity_types": ["link", "node"], "version": 2, "last_reviewed": "2025-05-09", "provenance": "synthetic"}
---
# Alarms during planned maintenance

## Guidance
When a link is shut down for planned work, traffic moves to alternate links, and they may raise utilization
alarms. These anomalies are real, but they are expected.

## Diagnostics
1. `verify_maintenance_schedule`. Does a change ticket cover the entity and the time?
2. `inspect_interface_counters` on the alternate links. Is there enough headroom?

## Remediation
Normally none. Escalate only if the alternates approach saturation or if customers are impacted beyond what was planned.
