"""Generate the NetPulse synthetic dataset, evaluation cases, and ground-truth labels.

    uv run python -m synthgen.generate            # regenerate committed v1 data
    uv run python -m synthgen.generate --check    # verify committed files match a fresh generation

Outputs (paths relative to the repo root):

* data/synthetic/v1/        investigator-visible data: topology, per-case telemetry,
                            events, maintenance, historical incidents, runbooks
* eval/datasets/v1/         investigator-visible case inputs (what an operator submits)
* eval/labels/v1/           GROUND TRUTH. Scoring only; never read by netpulse.

Output is byte-for-byte deterministic for a given seed: gzip mtime is zeroed,
JSON keys are sorted, floats are rounded before writing.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from synthgen.incidents import generate_incidents
from synthgen.scenarios import SCENARIOS, Scenario
from synthgen.telemetry import N_SAMPLES, SAMPLE_MINUTES, draw_noise, render, to_frames, truth_intervals
from synthgen.topology import LINKS, build_topology, monitored_nodes

GENERATOR_VERSION = "1.0.0"
DEFAULT_SEED = 20260301
DATASET_VERSION = "v1"
REPO_ROOT = Path(__file__).resolve().parent.parent

NOTICE = (
    "SIMULATED DATA. Every file in this directory was produced by synthgen for NetPulse AI "
    "development and evaluation. It does not describe, and was not observed from, any real network."
)

_BACKGROUND_EVENTS = [
    ("interface_flap", "info", "Interface flapped and recovered within 2 s"),
    ("auth", "info", "Administrative login from jump host"),
    ("ntp", "info", "NTP resynchronised"),
    ("bgp_notice", "info", "BGP keepalive from peer delayed 1.8 s"),
    ("snmp", "info", "SNMP poll retried once"),
]


def _json_bytes(obj: object) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode()


def _jsonl_bytes(rows: list[dict]) -> bytes:
    return "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows).encode()


def _csv_gz_bytes(df) -> bytes:
    buf = io.StringIO()
    df.to_csv(buf, index=False, date_format="%Y-%m-%dT%H:%M:%SZ", float_format="%.3f", lineterminator="\n")
    return gzip.compress(buf.getvalue().encode(), compresslevel=9, mtime=0)


def _background_events(rng: np.random.Generator, timestamps: list[datetime]) -> list[dict]:
    entities = monitored_nodes() + [ln["link_id"] for ln in LINKS]
    events = []
    for _ in range(int(rng.integers(5, 10))):
        etype, sev, msg = _BACKGROUND_EVENTS[int(rng.integers(len(_BACKGROUND_EVENTS)))]
        events.append(
            {
                "timestamp": timestamps[int(rng.integers(0, N_SAMPLES))].isoformat(),
                "entity_id": entities[int(rng.integers(len(entities)))],
                "event_type": etype,
                "severity": sev,
                "message": msg,
            }
        )
    # One benign config change well before the incident window, as a distractor.
    device = monitored_nodes()[int(rng.integers(len(monitored_nodes())))]
    events.append(
        {
            "timestamp": timestamps[int(rng.integers(20, 200))].isoformat(),
            "entity_id": device,
            "event_type": "config_change",
            "severity": "notice",
            "message": "Configuration committed by user netops-automation: SNMP location string update",
        }
    )
    return events


def _observable(interval: dict, frames: dict, submitted_at: datetime) -> bool:
    for df in frames.values():
        rows = df[df["entity_id"] == interval["entity_id"]]
        if rows.empty:
            continue
        ts = rows["timestamp"]
        start, end = datetime.fromisoformat(interval["start"]), datetime.fromisoformat(interval["end"])
        in_range = rows[(ts >= start) & (ts <= end)]
        visible = in_range["timestamp"] + pd.to_timedelta(in_range["delay_s"], unit="s") <= submitted_at
        return bool(visible.any())
    return False


def build_case(scenario: Scenario, case_id: str, day: datetime, seed: int) -> tuple[dict[str, bytes], dict, dict]:
    rng = np.random.default_rng(seed)
    timestamps = [day + timedelta(minutes=SAMPLE_MINUTES * i) for i in range(N_SAMPLES)]
    submitted_at = timestamps[-1] + timedelta(minutes=SAMPLE_MINUTES)
    noise = draw_noise(rng, scenario.noise)
    clean = render(day, noise, [])
    faulted = render(day, noise, scenario.faults)
    frames = to_frames(faulted, timestamps, scenario.issues, rng)

    intervals = truth_intervals(clean, faulted, timestamps)
    for interval in intervals:
        interval["observable_at_submission"] = _observable(interval, frames, submitted_at)

    events = _background_events(rng, timestamps) + [
        {
            "timestamp": timestamps[e.sample].isoformat(),
            "entity_id": e.entity_id,
            "event_type": e.event_type,
            "severity": e.severity,
            "message": e.message,
        }
        for e in scenario.events
    ]
    events.sort(key=lambda e: (e["timestamp"], e["entity_id"], e["event_type"]))
    maintenance = [
        {
            "entity_id": m.entity_id,
            "start": timestamps[m.start].isoformat(),
            "end": timestamps[min(m.end, N_SAMPLES - 1)].isoformat(),
            "ticket": m.ticket,
            "description": m.description,
        }
        for m in scenario.maintenance
    ]
    meta = {
        "dataset_id": case_id,
        "provenance": "synthetic",
        "notice": NOTICE,
        "sample_minutes": SAMPLE_MINUTES,
        "start": timestamps[0].isoformat(),
        "end": timestamps[-1].isoformat(),
        "tables": {
            "links": [
                "timestamp",
                "entity_id",
                "utilization_pct",
                "latency_ms",
                "packet_loss_pct",
                "error_rate",
                "delay_s",
            ],
            "nodes": ["timestamp", "entity_id", "cpu_pct", "memory_pct", "delay_s"],
            "services": ["timestamp", "entity_id", "service_latency_ms", "service_success_pct", "delay_s"],
        },
    }
    files = {
        f"cases/{case_id}/meta.json": _json_bytes(meta),
        f"cases/{case_id}/links.csv.gz": _csv_gz_bytes(frames["links"]),
        f"cases/{case_id}/nodes.csv.gz": _csv_gz_bytes(frames["nodes"]),
        f"cases/{case_id}/services.csv.gz": _csv_gz_bytes(frames["services"]),
        f"cases/{case_id}/events.json": _json_bytes(events),
        f"cases/{case_id}/maintenance.json": _json_bytes(maintenance),
    }
    case_input = {
        "case_id": case_id,
        "submitted_at": submitted_at.isoformat(),
        "submission": {
            "title": scenario.title,
            "description": scenario.description,
            "dataset_id": case_id,
            "window_start": timestamps[scenario.window[0]].isoformat(),
            "window_end": timestamps[scenario.window[1]].isoformat(),
            "suspected_entities": scenario.suspected_entities,
            "severity_hint": scenario.severity_hint,
        },
    }
    label = {
        "case_id": case_id,
        "scenario": scenario.key,
        "tags": scenario.tags,
        "root_causes": [{"category": rc.category, "entities": list(rc.entities)} for rc in scenario.root_causes],
        "expected_outcome": scenario.expected_outcome,
        "should_escalate": scenario.should_escalate,
        "acceptable_actions": scenario.acceptable_actions,
        "anomaly_intervals": intervals,
        "data_issues": [
            {
                "kind": i.kind,
                "entities": list(i.entities),
                "start": timestamps[i.start].isoformat(),
                "end": timestamps[min(i.end, N_SAMPLES) - 1].isoformat(),
                "delay_s": i.delay_s,
            }
            for i in scenario.issues
        ],
        "notes": scenario.notes,
    }
    return files, case_input, label


def generate(seed: int = DEFAULT_SEED) -> dict[str, dict[str, bytes]]:
    """Return {root: {relative_path: bytes}} for data, eval inputs and labels."""
    data: dict[str, bytes] = {}
    cases: list[dict] = []
    labels: list[dict] = []

    order = np.random.default_rng(seed).permutation(len(SCENARIOS))
    base_day = datetime(2026, 3, 2, 8, 0, tzinfo=UTC)
    for n, k in enumerate(order, start=1):
        case_id = f"case-{n:02d}"
        day = base_day + timedelta(days=2 * (n - 1))
        files, case_input, label = build_case(SCENARIOS[k], case_id, day, seed + 1000 * n)
        data.update(files)
        cases.append(case_input)
        labels.append(label)

    data["topology.json"] = _json_bytes(build_topology())
    data["corpus/incidents.jsonl"] = _jsonl_bytes(generate_incidents(seed + 7))
    data["README.md"] = f"# Synthetic dataset {DATASET_VERSION}\n\n{NOTICE}\n".encode()

    labels_readme = (
        b"# Ground-truth labels (scoring only)\n\n"
        b"These labels are read only by the evaluation scorer, after an investigation has finished.\n"
        b"The `netpulse` package has no code path to this directory, and a test enforces that.\n"
        b"Do not copy these labels into prompts, corpora, or fixtures that the investigator reads.\n"
    )
    return {
        f"data/synthetic/{DATASET_VERSION}": data,
        f"eval/datasets/{DATASET_VERSION}": {"cases.jsonl": _jsonl_bytes(cases)},
        f"eval/labels/{DATASET_VERSION}": {"labels.jsonl": _jsonl_bytes(labels), "README.md": labels_readme},
    }


def manifest(outputs: dict[str, dict[str, bytes]], seed: int, repo_root: Path) -> bytes:
    files = {}
    for root, entries in outputs.items():
        for rel, content in entries.items():
            files[f"{root}/{rel}"] = hashlib.sha256(content).hexdigest()
    static_dir = repo_root / f"data/synthetic/{DATASET_VERSION}/corpus/runbooks"
    static = {
        str(p.relative_to(repo_root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(static_dir.glob("*.md"))
    }
    return _json_bytes(
        {
            "dataset_version": DATASET_VERSION,
            "generator_version": GENERATOR_VERSION,
            "seed": seed,
            "provenance": "synthetic",
            "generated_files": files,
            "static_files": static,
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="repository root to write into")
    parser.add_argument("--check", action="store_true", help="verify files on disk instead of writing")
    args = parser.parse_args(argv)

    outputs = generate(args.seed)
    outputs[f"data/synthetic/{DATASET_VERSION}"]["MANIFEST.json"] = manifest(outputs, args.seed, args.root)

    mismatched = []
    for root, entries in outputs.items():
        for rel, content in entries.items():
            path = args.root / root / rel
            if args.check:
                if not path.exists() or path.read_bytes() != content:
                    mismatched.append(str(path.relative_to(args.root)))
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
    if args.check:
        if mismatched:
            print("Out of date:\n  " + "\n  ".join(mismatched), file=sys.stderr)
            return 1
        print("Synthetic data matches generator output.")
    else:
        print(f"Wrote {sum(len(e) for e in outputs.values())} files (seed={args.seed}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
