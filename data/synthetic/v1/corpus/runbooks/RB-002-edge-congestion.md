---
{"runbook_id": "RB-002", "title": "Edge link congestion: do not reroute during business hours", "categories": ["link_congestion"], "entity_types": ["link"], "version": 2, "last_reviewed": "2025-10-03", "provenance": "synthetic"}
---
# Edge link congestion: do not reroute during business hours

## Scope
Links between `pe-1` and the firewalls (`fw-1`, `fw-2`).

## Policy
Do **not** shift traffic between edge firewalls between 08:00 and 18:00 UTC. The firewalls do not
share session state, so rerouting drops every established session on the moved flows.
This conflicts with RB-001 for edge links. RB-002 takes precedence for edge links.

## Diagnostics
1. `inspect_interface_counters` on the edge link.
2. `compare_traffic_matrix` to identify the dominant traffic class.
3. `check_upstream_provider_status`. Inbound surges sometimes originate upstream.

## Remediation (requires approval)
- `apply_rate_limit` on the dominant non-critical class.
- Outside business hours only: `shift_traffic_to_redundant_path`.
