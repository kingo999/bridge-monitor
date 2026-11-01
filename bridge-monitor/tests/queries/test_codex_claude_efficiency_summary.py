"""Pure efficiency summary and parent outcome evidence (no database)."""
from service.queries.codex_claude import (EFFICIENCY_DEFINITION, _efficiency_attribution,
                                          _preset_fields, efficiency_summary)
from service.queries.history import _classify_judgment_evidence

COUNTERS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def _row(wid, *, preset="mechanical", model="claude-haiku-5-5", effort="low", outcome="accepted",
         duration=10.0, cost=None, usage=None):
    return {"work_item_id": wid, "applied_preset": preset, "preset_evidence": "applied" if preset else "legacy_no_preset",
            "efficiency_model": model, "efficiency_effort": effort, "parent_outcome": outcome,
            "duration_seconds": duration, "cost_usd_equivalent": cost,
            "usage": usage or {key: None for key in COUNTERS}}


def _group(summary, preset, model, effort):
    return next(g for g in summary["groups"] if (g["applied_preset"], g["reported_model"],
        g["configured_effort"]) == (preset, model, effort))


def test_groups_count_workflows_and_full_outcome_vocabulary():
    rows = [_row("a", outcome="accepted"), _row("b", outcome="revised"), _row("c", outcome="pending"),
            _row("d", outcome="ambiguous"), _row("e", outcome="unknown"), _row("f", outcome="unused"),
            _row("g", outcome="replaced")]
    summary = efficiency_summary(rows)
    assert summary["definition_version"] == EFFICIENCY_DEFINITION
    assert summary["unit"] == "workflows"
    group = _group(summary, "mechanical", "claude-haiku-5-5", "low")
    assert group["workflows"] == 7
    assert group["outcomes"] == {"accepted": 1, "revised": 1, "replaced": 1, "unused": 1,
        "pending": 1, "unknown": 1, "ambiguous": 1}
    assert group["outcome_denominator"] == 7
    assert group["human_accepted"] is None and group["checks_passed"] is None
    for forbidden in ("savings", "cost_per_accepted", "rank", "subscription_usd"):
        assert forbidden not in group and forbidden not in summary


def test_null_and_partial_coverage_never_become_zero():
    full = {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 80, "cache_creation_input_tokens": None}
    rows = [_row("a", cost="0.10", usage=full, duration=4.0), _row("b", duration=None), _row("c", cost="0.05", duration=8.0)]
    group = efficiency_summary(rows)["groups"][0]
    assert group["counters"]["input_tokens"] == {"known_sum": 100, "covered_workflows": 1, "unknown_workflows": 2}
    assert group["counters"]["cache_creation_input_tokens"] == {"known_sum": None, "covered_workflows": 0, "unknown_workflows": 3}
    assert "total_tokens" not in group["counters"]
    assert group["api_equivalent_usd"]["known_subtotal"] == "0.15"
    assert group["api_equivalent_usd"]["covered_workflows"] == 2
    assert group["api_equivalent_usd"]["unknown_workflows"] == 1
    assert group["api_equivalent_usd"]["is_total_job_cost"] is False
    assert group["duration_seconds"] == {"samples": 2, "median": 6.0, "p95": 8.0}
    empty = efficiency_summary([_row("z")])["groups"][0]
    assert empty["api_equivalent_usd"]["known_subtotal"] is None
    assert empty["api_equivalent_usd"]["covered_workflows"] == 0
    assert efficiency_summary([])["groups"] == []
    assert efficiency_summary([])["parent_coordinator_overhead"] == "unmeasured"


def test_mixed_and_unknown_attribution_stay_in_explicit_buckets():
    rows = [_row("a", model="mixed", effort="unattributed"), _row("b", preset=None, model="unknown", effort="unknown"),
            _row("c", model="claude-sonnet-5-5", effort="medium", preset="standard")]
    summary = efficiency_summary(rows)
    assert {(g["applied_preset"], g["reported_model"], g["configured_effort"]) for g in summary["groups"]} == {
        ("mechanical", "mixed", "unattributed"), ("legacy_no_preset", "unknown", "unknown"),
        ("standard", "claude-sonnet-5-5", "medium")}
    assert sum(g["workflows"] for g in summary["groups"]) == 3


def test_attribution_uses_every_contributing_execution_never_requested():
    assert _efficiency_attribution({"a", "b"}, set(), "low", "a", "a") == ("mixed", "unattributed")
    assert _efficiency_attribution({"a"}, {"child"}, "low", "a", "a") == ("unknown", "unattributed")
    assert _efficiency_attribution({"a"}, set(), "low", "a", "a") == ("a", "low")
    assert _efficiency_attribution(set(), set(), "low", "a", None) == ("unknown", "low")
    assert _efficiency_attribution({"a"}, set(), "low", "other", "a") == ("a", "unattributed")


def test_preset_fields_distinguish_legacy_override_and_missing_decision():
    assert _preset_fields({}, has_decision=False) == (None, "missing_decision")
    assert _preset_fields({"action": "claude"}, has_decision=True) == (None, "legacy_no_preset")
    applied = {"task_preset": {"requested": "standard", "applied": "standard", "status": "applied"}}
    assert _preset_fields(applied, has_decision=True) == ("standard", "applied")
    overridden = {"task_preset": {"requested": "mechanical", "applied": None, "status": "overridden"}}
    assert _preset_fields(overridden, has_decision=True) == (None, "overridden")


def test_judgment_evidence_handles_supersession_duplicates_pending_and_ambiguity():
    by_work = {"accepted": ["r1"], "superseded": ["r2"], "pending": ["r3"], "two_results": ["r4", "r5"],
               "duplicate": ["r6"]}
    by_result = {
        "r1": [("d1", "accepted", None)],
        "r2": [("d2", "accepted", None), ("d3", "revised", "d2")],
        "r4": [("d4", "accepted", None)],
        "r6": [("d6", "accepted", None), ("d7", "accepted", None)],
    }
    evidence = _classify_judgment_evidence(by_work, by_result)
    assert evidence == {"accepted": "accepted", "superseded": "revised", "pending": "pending",
                        "two_results": "ambiguous", "duplicate": "ambiguous"}
