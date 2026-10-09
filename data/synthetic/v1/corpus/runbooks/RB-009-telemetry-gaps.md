---
{"runbook_id": "RB-009", "title": "Missing, delayed, or impossible telemetry", "categories": ["telemetry_fault"], "entity_types": ["link", "node", "service"], "version": 3, "last_reviewed": "2025-09-01", "provenance": "synthetic"}
---
# Missing, delayed, or impossible telemetry

## Guidance
- **Gaps:** do not assume the entity is healthy, or unhealthy, from missing data. Report the gap.
- **Delays:** data that arrives late may make a window look empty at query time.
- **Impossible values** (utilization above 100%, negative counters) usually mean counter discontinuities after a
  collector restart, not real traffic.

## Diagnostics
1. `check_collector_health` for the affected entities.
2. `inspect_interface_counters` directly on the device, to bypass the collector.
