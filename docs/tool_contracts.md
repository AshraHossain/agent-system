# Tool contracts

All tools live in `opspilot/adk/tools.py` and wrap pure functions in
`opspilot/core/`. Common behaviour:

* **Inputs** are validated with Pydantic (IDs `^[a-z0-9][a-z0-9:-]{0,39}$`,
  document IDs `^[A-Z]{2,4}-\d{3,4}(-\d{4})?$`, bounded list sizes, metric
  names from the rule table). The dataset and time windows come from session
  state (`scope`), never from model arguments.
* **Outputs** are JSON objects with `status: "ok"` plus data and
  `evidence_ids`, or `status: "error"` with `error_type`
  (`invalid_argument`, `not_found`, `data_unavailable`, `budget_exceeded`,
  `permission_denied`, `internal_error`) and a short message. Unexpected
  exceptions are logged and returned as `internal_error` without internals.
* **Side effects**: none on data sources (SQLite opened `mode=ro`). The only
  write is recording `Evidence` into ADK session state.
* **Evidence**: content-addressed `EV-<KIND>-<8 hex>`; kinds `TEL` telemetry,
  `TOP` topology, `DOC` document, `INC` incident, `DQ` data quality.

| Tool | Agent | Inputs | Output (`data`) | Core function |
|---|---|---|---|---|
| `summarize_anomalies` | telemetry | `entity_ids?` ≤40 | `AnomalySummary`: anomalies (verdict, baseline/window mean, peak, delta, z, first_seen, rule), data_quality (missing/delayed/unknown) | `telemetry.summarize_anomalies` |
| `compare_to_baseline` | telemetry | `entity_id`, `metric` | `BaselineComparison` incl. explicit rule text | `telemetry.compare_to_baseline` |
| `get_telemetry` | telemetry | `entity_id`, `metrics?` | `TelemetrySnapshot` window stats | `telemetry.get_telemetry` |
| `lookup_component` | topology | `component_id` | `ComponentInfo` (unknown → `known=false`) | `Topology.lookup` |
| `get_service_dependencies` | topology | `services` 1–20 | per-service dependencies, `shared_ranked` (specificity), uncertainties | `Topology.dependencies` |
| `get_blast_radius` | topology | `component_id` | `BlastRadius` impacts by rule table | `Topology.blast_radius` |
| `trace_dependency_paths` | topology | `service`, `component_id` | ≤3 paths | `Topology.paths` |
| `search_runbooks` / `search_incidents` / `search_technical_docs` | knowledge | `query` ≤300, `top_k` 1–8 | `SearchResult` hits with sanitised snippet, flags, score, matched_by | `knowledge.search` |
| `get_document` | knowledge | `doc_id` | sanitised body ≤1500 chars, flags | `knowledge.get_document` |
| `generate_hypothesis_candidates` | incident | — | ranked `Hypothesis` list, affected services, unexplained observations, evidence requests | `analysis.generate_candidates` |
| `compare_with_incident` | incident | `incident_id` | similarities, differences, signature match | `analysis.compare_with_incident` |
| `validate_evidence_ids` | drafter | `evidence_ids` ≤100 | valid / invalid | registry lookup |
| `check_recommendation_policy` | drafter | `steps` ≤20 | per-step allowed + reason | `policy.check_step` |

"Evidence validation" and "report generation" are also exposed as the
deterministic `evidence_verifier` and `finalizer` stages (`core/verification.py`,
`core/report.py`).

## Telemetry rules (`core/telemetry.py::METRIC_RULES`)

Current value = median of the last 6 samples (30 min) in the window.

| Metric | Rule |
|---|---|
| utilization_pct | absolute: elevated ≥80, critical ≥90, rise ≥10 |
| cpu_pct / session_util_pct | absolute: 80 / 90, rise ≥15 |
| mem_pct | absolute: 85 / 95, rise ≥10 |
| packet_loss_pct / probe_loss_pct | absolute: 0.3 / 1.0, rise ≥0.2 |
| error_rate (CRC/s) | absolute: 5 / 20, rise ≥4 |
| error_pct | absolute: 1.0 / 2.0, rise ≥0.5 |
| latency_ms / query_latency_ms | relative: +50% / +100%, z ≥3 |
| latency_p95_ms (services) | relative: +20% / +50%, z ≥3 |

<50% coverage in window or baseline → `insufficient_data`. Missing ≥3 samples
→ `missing`; all metrics older than 2 intervals → `delayed`.

## Topology rules (`core/topology.py`)

Edges point dependent → requirement; same `group` = redundant alternatives.
`exposure`: single_point (service fails if component fails), redundant
(alternative survives), indirect (only via another service). Impact table:

| exposure \ tier | 1 | 2 | 3 |
|---|---|---|---|
| single_point | critical | high | medium |
| redundant | high | medium | low |
| indirect | medium | low | low |

## Retrieval (`core/retrieval.py`)

BM25 (k1 1.5, b 0.75) + hashing-vector cosine (512 dims; unigrams, bigrams,
char trigrams; min cosine 0.2), fused with RRF (k 60). The hashing embedder is
**not semantic** — it captures lexical/morphological overlap. Justification:
queries mix exact identifiers (`lnk-l3-s2`, `CRC`) with paraphrase; RRF keeps
both signals without score calibration.

## Hypothesis rules (`core/analysis.py`)

| Signature on a component | Category |
|---|---|
| link: utilization anomalous | `link_congestion` (errors anomalous → contradicting) |
| link: error_rate + loss/probe anomalous | `link_physical_degradation` |
| link: loss anomalous, probes and errors normal | `telemetry_artifact` (capped moderate) + contradicted `link_physical_degradation` |
| link: loss + probes anomalous, errors missing | `link_physical_degradation` (gaps lower confidence) |
| WAN link: latency/loss | `wan_degradation` |
| firewall/LB/switch/router: cpu/mem/session | `device_resource_saturation` |
| DNS server: query latency/cpu | `dns_degradation` |
| service anomalies, no network anomaly | `application_side` (weak) |

Confidence: strong (≥2 independent metrics, no contradiction, complete data),
moderate (1 metric, or ≥2 with gaps), weak (contradicted or indirect only).
