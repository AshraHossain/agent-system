"""Phase 3: synthetic dataset reproducibility, schema validity, and label isolation."""

import ast
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from netpulse.models import IncidentSubmission, RootCauseCategory
from synthgen import generate as gen

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data/synthetic/v1"
CASES_FILE = ROOT / "eval/datasets/v1/cases.jsonl"
LABELS_FILE = ROOT / "eval/labels/v1/labels.jsonl"
CATALOG = json.loads((ROOT / "netpulse/policy/catalog.json").read_text())
CATALOG_IDS = {a["catalog_id"] for a in CATALOG["actions"]}
TOPOLOGY = json.loads((DATA / "topology.json").read_text())
ENTITY_IDS = (
    {n["node_id"] for n in TOPOLOGY["nodes"]}
    | {ln["link_id"] for ln in TOPOLOGY["links"]}
    | {s["service_id"] for s in TOPOLOGY["services"]}
)


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


CASES = _jsonl(CASES_FILE)
LABELS = {lab["case_id"]: lab for lab in _jsonl(LABELS_FILE)}


def _table(case_id: str, name: str) -> pd.DataFrame:
    df = pd.read_csv(DATA / "cases" / case_id / f"{name}.csv.gz")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


def _by_scenario(key: str) -> tuple[dict, dict]:
    label = next(lab for lab in LABELS.values() if lab["scenario"] == key)
    case = next(c for c in CASES if c["case_id"] == label["case_id"])
    return case, label


# --- reproducibility ------------------------------------------------------


def test_committed_data_matches_fresh_generation():
    assert gen.main(["--check"]) == 0


def test_manifest_checksums_match_files():
    manifest = json.loads((DATA / "MANIFEST.json").read_text())
    assert manifest["provenance"] == "synthetic"
    for rel, digest in {**manifest["generated_files"], **manifest["static_files"]}.items():
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == digest, rel


def test_different_seed_changes_telemetry():
    scenario = gen.SCENARIOS[0]
    day = datetime.fromisoformat("2026-03-02T08:00:00+00:00")
    a, _, _ = gen.build_case(scenario, "case-x", day, seed=1)
    b, _, _ = gen.build_case(scenario, "case-x", day, seed=2)
    assert a["cases/case-x/links.csv.gz"] != b["cases/case-x/links.csv.gz"]


# --- schema validity ------------------------------------------------------


def test_case_inputs_validate_as_submissions():
    assert len(CASES) == 20
    for case in CASES:
        assert set(case) == {"case_id", "submitted_at", "submission"}
        sub = IncidentSubmission(**case["submission"])
        assert sub.dataset_id == case["case_id"]
        assert datetime.fromisoformat(case["submitted_at"]) > sub.window_end


def test_telemetry_tables_match_declared_schema():
    for case in CASES:
        meta = json.loads((DATA / "cases" / case["case_id"] / "meta.json").read_text())
        assert meta["provenance"] == "synthetic" and "SIMULATED DATA" in meta["notice"]
        for name, columns in meta["tables"].items():
            df = _table(case["case_id"], name)
            assert list(df.columns) == columns
            assert df["timestamp"].min() >= pd.Timestamp(meta["start"])
            assert df["timestamp"].max() <= pd.Timestamp(meta["end"])
            assert set(df["entity_id"]) <= ENTITY_IDS
            assert (df.drop(columns=["timestamp", "entity_id"]) >= 0).all().all()


def test_impossible_utilization_only_in_telemetry_fault_case():
    for case in CASES:
        links = _table(case["case_id"], "links")
        has_impossible = bool((links["utilization_pct"] > 100).any())
        is_glitch_case = LABELS[case["case_id"]]["scenario"] == "detector_misleading"
        assert has_impossible == is_glitch_case, case["case_id"]


def test_labels_reference_known_entities_categories_and_actions():
    categories = {c.value for c in RootCauseCategory}
    for label in LABELS.values():
        assert set(label["acceptable_actions"]) <= CATALOG_IDS
        assert "restart_device" not in label["acceptable_actions"]
        assert label["expected_outcome"] in {"root_cause", "inconclusive"}
        for rc in label["root_causes"]:
            assert rc["category"] in categories
            assert set(rc["entities"]) <= ENTITY_IDS
        for interval in label["anomaly_intervals"]:
            assert interval["entity_id"] in ENTITY_IDS
            assert interval["start"] <= interval["end"]


def test_required_difficult_cases_are_present():
    tags = {t for lab in LABELS.values() for t in lab["tags"]}
    required = {
        "normal",
        "traffic_spike",
        "congestion",
        "packet_loss",
        "link_degradation",
        "device_resource",
        "correlated",
        "missing_data",
        "delayed_telemetry",
        "noisy",
        "multi_fault",
        "ambiguous",
        "incorrect_detector_output",
        "conflicting_runbooks",
        "irrelevant_history",
        "prompt_injection",
        "outside_evidence",
        "maintenance",
    }
    assert required <= tags


# --- ground-truth semantics ----------------------------------------------


def test_fault_free_cases_have_no_ground_truth_anomalies():
    for label in LABELS.values():
        if label["scenario"] in {"normal_quiet", "normal_noisy", "ambiguous"}:
            assert label["anomaly_intervals"] == [], label["scenario"]


def test_root_entities_appear_in_ground_truth():
    for label in LABELS.values():
        anomalous = {i["entity_id"] for i in label["anomaly_intervals"]}
        for rc in label["root_causes"]:
            if rc["category"] == "upstream_dependency":
                assert not anomalous & {"isp-transit", "link-pe-1-isp-transit"}  # invisible by design
                continue
            assert anomalous & set(rc["entities"]), (label["scenario"], rc)


def test_missing_data_case_drops_rows_and_marks_fault_unobservable():
    case, label = _by_scenario("missing_data")
    gap = label["data_issues"][0]
    links = _table(case["case_id"], "links")
    in_gap = links[(links["entity_id"] == "link-agg-3-acc-6") & (links["timestamp"] >= pd.Timestamp(gap["start"]))]
    assert in_gap.empty
    by_entity = {i["entity_id"]: i["observable_at_submission"] for i in label["anomaly_intervals"]}
    assert by_entity["link-agg-3-acc-6"] is False
    assert by_entity["video"] is True


def test_delayed_rows_are_not_visible_at_submission_time():
    case, label = _by_scenario("delayed_telemetry")
    submitted = pd.Timestamp(case["submitted_at"])
    nodes = _table(case["case_id"], "nodes")
    agg4 = nodes[nodes["entity_id"] == "agg-4"]
    visible = agg4["timestamp"] + pd.to_timedelta(agg4["delay_s"], unit="s") <= submitted
    assert not visible[agg4["timestamp"] > submitted - pd.Timedelta(hours=2)].any()
    agg3 = nodes[nodes["entity_id"] == "agg-3"]
    assert (agg3["timestamp"] + pd.to_timedelta(agg3["delay_s"], unit="s") <= submitted).all()


# --- label isolation / leakage -------------------------------------------

LABEL_ONLY_KEYS = ("anomaly_intervals", "should_escalate", "expected_outcome", "acceptable_actions", "root_causes")


def test_investigator_visible_files_contain_no_label_fields_or_scenario_names():
    scenario_keys = {s.key for s in gen.SCENARIOS}
    visible = [p for p in DATA.rglob("*") if p.is_file() and p.suffix in {".json", ".jsonl", ".md"}]
    visible.append(CASES_FILE)
    for path in visible:
        text = path.read_text()
        for key in LABEL_ONLY_KEYS:
            assert f'"{key}"' not in text, (path, key)
        if path == CASES_FILE or "cases" in path.relative_to(DATA).parts:  # corpus may use category words
            for key in scenario_keys:
                assert not re.search(rf"(?<![a-z_]){key}(?![a-z_])", text), (path, key)


def test_case_ids_do_not_reveal_scenarios():
    assert all(c["case_id"].startswith("case-") for c in CASES)
    assert [LABELS[c["case_id"]]["scenario"] for c in CASES] != [s.key for s in gen.SCENARIOS]


def _python_sources(package: Path) -> list[Path]:
    return [p for p in package.rglob("*.py") if "__pycache__" not in p.parts]


@pytest.mark.parametrize("path", _python_sources(ROOT / "netpulse"), ids=lambda p: str(p.relative_to(ROOT)))
def test_netpulse_never_imports_or_reads_ground_truth(path: Path):
    source = path.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        assert not any(n.split(".")[0] in {"synthgen", "eval"} for n in names), path
    for forbidden in ("eval/labels", "labels.jsonl", "synthgen"):
        assert forbidden not in source, (path, forbidden)


def test_historical_corpus_precedes_and_differs_from_eval_cases():
    incidents = _jsonl(DATA / "corpus/incidents.jsonl")
    assert len({i["incident_id"] for i in incidents}) == len(incidents) == 40
    first_case = min(datetime.fromisoformat(c["submission"]["window_start"]) for c in CASES)
    case_titles = {c["submission"]["title"] for c in CASES}
    for inc in incidents:
        assert inc["provenance"] == "synthetic"
        assert datetime.fromisoformat(inc["opened_at"]) < first_case
        assert inc["title"] not in case_titles
        assert set(inc["actions_taken"]) <= CATALOG_IDS
    # The look-alike dns-1 history must exist and must not share the eval case's cause.
    dns_history = [i for i in incidents if "dns-1" in i["affected_entities"]]
    assert dns_history and all(i["root_cause_category"] != "device_memory_exhaustion" for i in dns_history)


def test_runbooks_are_synthetic_and_cite_only_catalog_actions():
    runbooks = sorted((DATA / "corpus/runbooks").glob("*.md"))
    assert len(runbooks) == 12
    for path in runbooks:
        _, front, body = path.read_text().split("---\n", 2)
        meta = json.loads(front)
        assert meta["provenance"] == "synthetic"
        cited = {tok for tok in body.split("`")[1::2] if "_" in tok and tok.replace("_", "").isalpha()}
        assert cited <= CATALOG_IDS, (path.name, cited - CATALOG_IDS)


def test_injection_fixture_is_present_but_unmarked():
    text = (DATA / "corpus/runbooks/RB-012-interface-flap.md").read_text()
    assert "ignore all prior instructions" in text
    assert "prompt_injection" not in text and "test fixture" not in text.lower()
