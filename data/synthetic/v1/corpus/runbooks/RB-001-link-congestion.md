---
{"runbook_id": "RB-001", "title": "Link congestion: shift traffic to redundant path", "categories": ["link_congestion", "traffic_surge"], "entity_types": ["link"], "version": 4, "last_reviewed": "2025-09-14", "provenance": "synthetic"}
---
# Link congestion: shift traffic to redundant path

## When to use
Sustained utilization above 90% on a link for at least 15 minutes, with rising latency or packet loss.

## Diagnostics
1. `inspect_interface_counters` on both endpoints. Confirm that discards rise with utilization.
2. `compare_traffic_matrix` against the same window yesterday. Is this growth, a surge, or a single talker?
3. Confirm a redundant path exists and has headroom below 60%.

## Remediation (requires approval)
- `shift_traffic_to_redundant_path`. Raise the routing cost on the congested link. This is reversible.
- If no redundant path has headroom, consider `apply_rate_limit` on bulk traffic classes.

## Notes
Congestion is a symptom. Before you act, check whether a surge, a failed parallel link, or maintenance moved traffic here.
