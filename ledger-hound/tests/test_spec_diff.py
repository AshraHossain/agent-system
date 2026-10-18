"""API surface diff between runs: structural, deterministic, flags breaking changes."""

from __future__ import annotations

import copy
import json

from ledger_hound.cli import main
from ledger_hound.p2p_api.app import create_app
from ledger_hound.qa.spec_diff import describe, diff, load_surface, surface_from_spec


def _spec() -> dict:
    return create_app(frozenset()).openapi()


def test_surface_flattens_request_fields():
    s = surface_from_spec(_spec())
    inv = s["operations"]["POST /invoices"]
    assert inv["role"] == "create_invoice"
    assert inv["fields"]["lines[].uom"] == {"type": "null|string", "required": False}
    assert inv["fields"]["vendor_id"]["required"] is True


def test_same_spec_is_unchanged():
    assert diff(surface_from_spec(_spec()), surface_from_spec(_spec()))["unchanged"]
    assert describe(diff(surface_from_spec(_spec()), surface_from_spec(_spec()))) == ["no change"]


def test_breaking_and_additive_changes_are_separated():
    old = _spec()
    new = copy.deepcopy(old)
    del new["paths"]["/purchase-orders/{po_id}/submit"]
    line = new["components"]["schemas"]["InvoiceLineIn"]
    line["required"].append("uom")
    line["properties"]["qty"] = {"type": "number"}
    line["properties"]["tax_code"] = {"type": "string"}
    new["paths"]["/credit-notes"] = {"post": {"summary": "raise a credit note"}}

    d = diff(surface_from_spec(old), surface_from_spec(new))
    assert d["operations_added"] == ["POST /credit-notes"]
    assert d["operations_removed"] == ["POST /purchase-orders/{po_id}/submit"]
    assert {
        "role": "submit_po",
        "before": "POST /purchase-orders/{po_id}/submit",
        "after": None,
    } in d["role_changes"]
    assert d["changed"]["POST /invoices"]["fields_added"] == ["lines[].tax_code"]
    assert d["breaking"] == [
        "operation removed: POST /purchase-orders/{po_id}/submit",
        "role lost: submit_po (was POST /purchase-orders/{po_id}/submit)",
        "POST /invoices: lines[].qty type number|string -> number",
        "POST /invoices: lines[].uom is now required",
    ]  # the optional new field is additive, not breaking


def test_load_surface_accepts_reports_and_specs():
    s = surface_from_spec(_spec())
    assert load_surface({"api_surface": s}) is s
    assert load_surface(_spec()) == s
    assert load_surface({"meta": {}}) is None


def test_run_records_changes_against_previous_report(tmp_path, capsys):
    out = str(tmp_path)
    assert main(["run", "--mock", "--out", out, "--quiet"]) == 0
    first = json.loads((tmp_path / "latest.json").read_text())
    assert first["api_changes"] is None  # nothing to compare with yet
    assert "POST /invoices" in first["api_surface"]["operations"]

    first["api_surface"]["operations"]["DELETE /vendors/{vendor_id}"] = {"role": None, "fields": {}}
    (tmp_path / "latest.json").write_text(json.dumps(first))
    assert main(["run", "--mock", "--out", out, "--quiet"]) == 0
    second = json.loads((tmp_path / "latest.json").read_text())
    changes = second["api_changes"]
    assert changes["baseline"]["report_generated_at"] == first["meta"]["generated_at"]
    assert changes["breaking"] == ["operation removed: DELETE /vendors/{vendor_id}"]


def test_diff_command_exit_codes(tmp_path, capsys):
    old, new = tmp_path / "old.json", tmp_path / "new.json"
    spec = _spec()
    old.write_text(json.dumps(spec))
    new.write_text(json.dumps(spec))
    assert main(["diff", str(old), str(new)]) == 0
    del spec["paths"]["/vendors/{vendor_id}/exposure"]
    new.write_text(json.dumps(spec))
    assert main(["diff", str(old), str(new)]) == 1
    assert (
        "BREAKING operation removed: GET /vendors/{vendor_id}/exposure" in capsys.readouterr().out
    )
    (tmp_path / "junk.json").write_text("{}")
    assert main(["diff", str(old), str(tmp_path / "junk.json")]) == 2
