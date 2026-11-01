"""Pure export regression: new efficiency text fields keep cohort scope and formula-injection protection."""
import csv
import io
import json

from service.queries.codex_claude import export_codex_claude

FILTERS = {"origin": "all", "category": "docs", "as_of": "2026-10-01T00:00:00+00:00"}


def _report():
    row = {"work_item_id": "w1", "category": "docs", "executor": "claude", "requested_model": "m",
           "reported_model": "m", "project": None, "profile": "sonnet", "complexity": "low",
           "route_reason": "task_preset", "override": "auto", "status": "completed",
           "evidence_status": "parent_accepted", "duration_seconds": 0, "cost_usd_equivalent": "0",
           "applied_preset": "=HYPERLINK(1)", "preset_evidence": "+applied", "configured_effort": "-1",
           "parent_outcome": "@accepted", "efficiency_model": "m", "efficiency_effort": "medium",
           "coverage": {"duration": True, "cost": "receipt_backed", "counters": {"input_tokens": True}}}
    legacy = {**row, "work_item_id": "w2", "applied_preset": None, "preset_evidence": "legacy_no_preset",
              "configured_effort": None, "parent_outcome": "unknown", "cost_usd_equivalent": None}
    return {"definition_version": "codex-claude-v1", "as_of": "2026-10-01T00:00:00+00:00",
            "filters": FILTERS, "total": 2, "scope_note": "scope", "rows": [row, legacy]}


def test_csv_appends_efficiency_columns_and_escapes_formula_prefixes():
    rows = list(csv.DictReader(io.StringIO(export_codex_claude(_report(), "csv"))))
    meta, first, legacy = rows
    assert list(meta)[-4:] == ["applied_preset", "preset_evidence", "configured_effort", "parent_outcome"]
    assert meta["record_type"] == "metadata" and meta["as_of"] == "2026-10-01T00:00:00+00:00"
    assert json.loads(meta["filters"]) == FILTERS and meta["total"] == "2"
    assert first["applied_preset"] == "'=HYPERLINK(1)" and first["preset_evidence"] == "'+applied"
    assert first["configured_effort"] == "'-1" and first["parent_outcome"] == "'@accepted"
    # True zero stays zero; missing stays empty, never zero-filled.
    assert first["duration_seconds"] == "0" and first["cost_usd_equivalent"] == "0"
    assert legacy["applied_preset"] == "" and legacy["configured_effort"] == ""
    assert legacy["cost_usd_equivalent"] == "" and legacy["preset_evidence"] == "legacy_no_preset"


def test_jsonl_rows_carry_every_new_field_with_report_scope():
    lines = [json.loads(line) for line in export_codex_claude(_report(), "jsonl").splitlines()]
    assert lines[0]["type"] == "metadata" and lines[0]["filters"] == FILTERS and lines[0]["total"] == 2
    assert [r["work_item_id"] for r in lines[1:]] == ["w1", "w2"]
    assert lines[1]["coverage"]["cost"] == "receipt_backed" and lines[1]["efficiency_effort"] == "medium"
    assert lines[2]["applied_preset"] is None and lines[2]["cost_usd_equivalent"] is None
