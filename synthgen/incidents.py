"""Historical incident corpus (resolved past incidents the investigator may retrieve).

Generated from a *different* seed and template set than the evaluation
scenarios, dated in 2025 (all eval cases are in 2026). These records are
legitimate operational knowledge, not evaluation labels. Some are
intentionally similar-looking but irrelevant (e.g. dns-1 incidents with a
different cause than the dns-1 eval case).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

from synthgen.topology import LINKS

ACCESS_LINKS = [ln["link_id"] for ln in LINKS if "-acc-" in ln["link_id"]]
CORE_LINKS = [ln["link_id"] for ln in LINKS if ln["link_id"].startswith("link-core-")]
EDGE_LINKS = [ln["link_id"] for ln in LINKS if ln["link_id"].startswith(("link-pe-", "link-fw-"))]
DEVICES = ["core-1", "core-2", "fw-1", "fw-2", "agg-1", "agg-2", "agg-3", "agg-4", "pe-1"]

# category -> (entity pool, title, symptoms, cause, resolution, actions)
TEMPLATES: dict[str, list[tuple[list[str], str, str, str, str, list[str]]]] = {
    "link_degradation": [
        (
            ACCESS_LINKS + CORE_LINKS,
            "CRC errors and loss on {e}",
            "Rising CRC errors and 1-3% packet loss on {e}; customers behind it reported drops.",
            "Dirty connector on {e} degraded the optical signal.",
            "Connector cleaned during an approved window; errors returned to baseline.",
            ["inspect_interface_counters", "check_optic_levels", "schedule_optic_replacement"],
        ),
        (
            EDGE_LINKS,
            "Packet loss on edge link {e}",
            "Intermittent loss on {e} without a utilization change.",
            "Marginal optic on {e}; receive power near the low threshold.",
            "Traffic shifted to the redundant edge path, optic replaced next day.",
            ["check_optic_levels", "shift_traffic_to_redundant_path", "schedule_optic_replacement"],
        ),
    ],
    "link_congestion": [
        (
            CORE_LINKS + EDGE_LINKS,
            "Sustained high utilization on {e}",
            "Utilization above 95% on {e} for over an hour; latency and loss rose with it.",
            "Capacity shortfall on {e} after organic traffic growth.",
            "Traffic engineered onto the parallel path; capacity upgrade raised.",
            ["inspect_interface_counters", "compare_traffic_matrix", "shift_traffic_to_redundant_path"],
        ),
    ],
    "traffic_surge": [
        (
            CORE_LINKS + ACCESS_LINKS,
            "Traffic spike on {e}",
            "Sudden utilization jump on {e}; video customers reported buffering.",
            "Large scheduled streaming event drove demand on {e}.",
            "Temporary rate limit on bulk traffic; event finished and traffic normalized.",
            ["compare_traffic_matrix", "apply_rate_limit"],
        ),
    ],
    "device_cpu_saturation": [
        (
            DEVICES,
            "High CPU on {e}",
            "CPU above 95% on {e}; control-plane protocols slow to converge.",
            "Routing table churn from a flapping peer drove CPU on {e}.",
            "Flapping peer dampened; CPU recovered.",
            ["check_device_process_table", "review_recent_config_changes"],
        ),
    ],
    "device_memory_exhaustion": [
        (
            ["fw-1", "fw-2", "core-1", "core-2"],
            "Memory leak on {e}",
            "Memory on {e} climbed steadily over hours; sessions began to drop.",
            "Software defect leaking memory in a logging process on {e}.",
            "Process restarted in an approved window; vendor fix scheduled.",
            ["check_memory_allocation", "check_device_process_table"],
        ),
    ],
    "configuration_change": [
        (
            DEVICES,
            "Loss after configuration change on {e}",
            "Packet loss began minutes after a commit on {e}.",
            "ACL change on {e} punted traffic to the CPU path.",
            "Change rolled back after review; loss stopped.",
            ["review_recent_config_changes", "rollback_config_change"],
        ),
    ],
    "maintenance_side_effect": [
        (
            CORE_LINKS,
            "Alarms during planned work on {e}",
            "Utilization alarms on neighbours of {e} during a maintenance window.",
            "Planned shutdown of {e} shifted traffic to alternates as designed.",
            "No action; alarms cleared when {e} returned.",
            ["verify_maintenance_schedule"],
        ),
    ],
    "telemetry_fault": [
        (
            CORE_LINKS + ACCESS_LINKS,
            "Impossible readings on {e}",
            "Dashboards showed utilization far above 100% on {e} for two samples.",
            "Counter discontinuity after a collector restart.",
            "Collector cache fixed; no network impact.",
            ["check_collector_health"],
        ),
    ],
    "upstream_dependency": [
        (
            ["isp-transit"],
            "Upstream provider degradation",
            "Internet latency rose for all customers while internal links were normal.",
            "Congestion inside the transit provider's network.",
            "Provider ticket opened; provider rerouted.",
            ["check_upstream_provider_status", "open_provider_ticket"],
        ),
    ],
}

# Fixed look-alike records for dns-1 with causes that differ from the eval case.
FIXED = [
    (
        "configuration_change",
        "dns-1",
        "DNS resolution failures on dns-1",
        "Name resolution timed out for some zones served by dns-1.",
        "Resolver forwarding misconfiguration on dns-1 after a template update.",
        "Forwarders corrected; resolution recovered.",
        ["review_recent_config_changes", "rollback_config_change"],
    ),
    (
        "upstream_dependency",
        "dns-1",
        "dns-1 slow lookups for external domains",
        "External lookups slow via dns-1; internal zones unaffected.",
        "Upstream root/TLD reachability issue at the transit provider.",
        "Provider resolved routing issue.",
        ["check_upstream_provider_status", "run_path_trace"],
    ),
]


def generate_incidents(seed: int, count: int = 40) -> list[dict]:
    rng = np.random.default_rng(seed)
    categories = sorted(TEMPLATES)
    base = datetime(2025, 1, 6, tzinfo=UTC)
    records = []
    for i in range(count - len(FIXED)):
        category = categories[i % len(categories)]
        options = TEMPLATES[category]
        pool, title, symptoms, cause, resolution, actions = options[int(rng.integers(len(options)))]
        entity = pool[int(rng.integers(len(pool)))]
        records.append((category, entity, title, symptoms, cause, resolution, actions))
    records.extend(FIXED)
    order = rng.permutation(len(records))
    out = []
    for n, k in enumerate(order, start=1):
        category, entity, title, symptoms, cause, resolution, actions = records[k]
        opened = base + timedelta(days=int(n * 8 + rng.integers(0, 6)), minutes=int(rng.integers(0, 1440)))
        out.append(
            {
                "incident_id": f"PI-2025-{n:03d}",
                "provenance": "synthetic",
                "opened_at": opened.isoformat(),
                "duration_minutes": int(rng.integers(20, 360)),
                "title": title.format(e=entity),
                "symptoms": symptoms.format(e=entity),
                "affected_entities": [entity],
                "root_cause_category": category,
                "root_cause_summary": cause.format(e=entity),
                "resolution": resolution.format(e=entity),
                "actions_taken": actions,
            }
        )
    return out
