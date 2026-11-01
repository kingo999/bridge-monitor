"""Fixture-backed Codex/Claude report: presets, snapshot effort and honest coverage."""
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.types.json import Jsonb

from bridge.router.codex_claude import evaluate_codex_claude
from contracts.codex_claude import CodexClaudePolicy, CodexClaudeTaskFacts, HostRoutingFacts
from contracts.queries import CohortQuery
from service.queries.codex_claude import export_codex_claude, query_codex_claude

OLD_ROW_KEYS = {"work_item_id", "task_id", "category", "origin", "created_at", "executor", "requested_model",
    "reported_model", "project", "complexity", "risk", "profile", "policy_digest", "route_reason", "override",
    "decision_id", "status", "caller_reported_complete", "parent_accepted", "human_accepted", "checks_passed",
    "rework", "duration_seconds", "cost_usd_equivalent", "usage", "receipt_hashes", "evidence_status"}
OLD_TOP_KEYS = {"definition_version", "as_of", "filters", "total", "rows", "models", "executors", "outcomes",
    "latency_seconds", "cost", "readiness", "scope_note"}
MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]


def _policy(haiku_effort=None):
    policy = CodexClaudePolicy(enabled=True)
    for profile in policy.profiles.values():
        profile.enabled = True
    if haiku_effort:
        policy.profiles["haiku"].effort = haiku_effort
    return policy


def _decide(policy, complexity, risk, override="auto", preset=None, category="x"):
    facts = CodexClaudeTaskFacts(category=category, complexity=complexity, risk=risk)
    host = HostRoutingFacts(authorized=True, context_available=True, supplied_context_only=True,
                            profile_models=list(MODELS))
    return evaluate_codex_claude(policy, facts, host, override, task_preset=preset).model_dump(mode="json")


@pytest.fixture
def seeded(runtime_dsn):
    category = "eff-" + uuid.uuid4().hex[:10]
    with psycopg.connect(runtime_dsn) as conn:
        conn.execute("TRUNCATE cc_receipts,cc_reservations,cc_decisions,cc_records")
    # The active policy is absent; snapshot effort must come from the stored decision.
    decisions = {
        "mech": _decide(_policy(), "low", "low", preset="mechanical", category=category),
        "legacy": _decide(_policy(), "ordinary", "ordinary", category=category),
        "changed": _decide(_policy("medium"), "low", "low", preset="mechanical", category=category),
        "overridden": _decide(_policy(), "low", "low", "opus", preset="mechanical", category=category),
        "blocked": _decide(_policy(), "complex", "low", preset="standard", category=category),
    }
    ids = {}
    base = datetime.now(timezone.utc) - timedelta(minutes=5)
    with psycopg.connect(runtime_dsn) as conn:
        for index, (name, payload) in enumerate(decisions.items()):
            wid = f"{category}-{name}"
            ids[name] = wid
            conn.execute("INSERT INTO cc_decisions(decision_id,work_item_id,task_id,prompt_sha256,payload,recorded_at) "
                         "VALUES(%s,%s,%s,%s,%s,%s)", (str(uuid.uuid4()), wid, "t-" + wid, "0" * 64,
                         Jsonb(payload), base + timedelta(seconds=index)))
        call = "call-" + ids["mech"]
        conn.execute("INSERT INTO cc_reservations(call_id,work_item_id,workflow_id,profile,amount,state) "
                     "VALUES(%s,%s,%s,'haiku',0.02,'settled')", (call, ids["mech"], "t-" + ids["mech"]))
        conn.execute("INSERT INTO cc_receipts(receipt_id,call_id,amount,receipt_sha256,rate_card_version,counters,observed_at) "
                     "VALUES(%s,%s,0.05,%s,'proposal-2026-10-25',%s,%s)",
                     ("receipt-" + call, call, "a" * 64, Jsonb({}), base))
    yield runtime_dsn, category, ids
    with psycopg.connect(runtime_dsn) as conn:
        conn.execute("TRUNCATE cc_receipts,cc_reservations,cc_decisions,cc_records")


def test_report_keeps_old_contract_and_adds_preset_effort_coverage(seeded):
    dsn, category, ids = seeded
    report = query_codex_claude(dsn, CohortQuery(), category=category)
    assert OLD_TOP_KEYS <= set(report)
    assert report["total"] == 5
    rows = {row["work_item_id"]: row for row in report["rows"]}
    for row in rows.values():
        assert OLD_ROW_KEYS <= set(row)
        assert row["parent_outcome"] == "unknown"
        assert row["human_accepted"] is None and row["checks_passed"] is None
    mech, legacy, changed = rows[ids["mech"]], rows[ids["legacy"]], rows[ids["changed"]]
    assert (mech["applied_preset"], mech["preset_evidence"], mech["configured_effort"]) == ("mechanical", "applied", "low")
    assert mech["task_preset"]["version"] == "task-presets-v1"
    assert mech["reported_model"] is None and mech["requested_model"] == "claude-haiku-5-5"
    assert mech["cost_usd_equivalent"] == "0.05"
    assert (legacy["applied_preset"], legacy["preset_evidence"], legacy["configured_effort"]) == (None, "legacy_no_preset", "medium")
    assert "task_preset" in legacy and legacy["task_preset"] is None
    assert changed["configured_effort"] == "medium" and changed["effort_source"] == "decision_snapshot"
    over = rows[ids["overridden"]]
    assert (over["applied_preset"], over["preset_evidence"], over["profile"], over["override"]) == (None, "overridden", "opus", "opus")
    assert over["task_preset"]["requested"] == "mechanical"
    blocked = rows[ids["blocked"]]
    assert (blocked["preset_evidence"], blocked["configured_effort"], blocked["route_reason"]) == ("blocked", None, "task_preset_incompatible")
    assert report["cost"] == {"basis": "USD API-equivalent", "known_subtotal": "0.05", "known_workflows": 1, "unknown_workflows": 4}

    groups = {(g["applied_preset"], g["reported_model"], g["configured_effort"]): g for g in report["efficiency"]["groups"]}
    assert groups[("mechanical", "unknown", "low")]["workflows"] == 1
    assert groups[("mechanical", "unknown", "low")]["api_equivalent_usd"]["known_subtotal"] == "0.05"
    assert groups[("mechanical", "unknown", "medium")]["api_equivalent_usd"]["known_subtotal"] is None
    assert groups[("legacy_no_preset", "unknown", "medium")]["workflows"] == 1
    assert groups[("overridden", "unknown", "medium")]["workflows"] == 1
    assert groups[("blocked", "unknown", "unknown")]["workflows"] == 1
    assert sum(g["workflows"] for g in groups.values()) == 5
    for group in groups.values():
        assert group["outcomes"]["unknown"] == group["workflows"] == group["outcome_denominator"]
        assert all(c["known_sum"] is None and c["covered_workflows"] == 0 for c in group["counters"].values())


def test_as_of_and_filters_apply_to_efficiency_and_exports(seeded):
    dsn, category, ids = seeded
    early = query_codex_claude(dsn, CohortQuery(as_of=datetime.now(timezone.utc) - timedelta(minutes=10)), category=category)
    assert early["total"] == 0 and early["efficiency"]["groups"] == []
    filtered = query_codex_claude(dsn, CohortQuery(), category=category, profile="haiku")
    assert {r["work_item_id"] for r in filtered["rows"]} == {ids["mech"], ids["changed"]}
    assert sum(g["workflows"] for g in filtered["efficiency"]["groups"]) == 2
    jsonl = export_codex_claude(filtered, "jsonl")
    assert '"applied_preset": "mechanical"' in jsonl
    csv_text = export_codex_claude(filtered, "csv")
    header = csv_text.splitlines()[0].split(",")
    assert header[:19] == ["record_type", "definition_version", "as_of", "filters", "total", "work_item_id",
        "category", "executor", "requested_model", "reported_model", "project", "profile", "complexity",
        "route_reason", "override", "status", "evidence_status", "duration_seconds", "cost_usd_equivalent"]
    assert header[19:] == ["applied_preset", "preset_evidence", "configured_effort", "parent_outcome"]
