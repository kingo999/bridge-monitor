import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bridge.code_fix_shadow_runner import _digest
from bridge.shadow_cohort import check_new_case, summarize_receipts


def receipt(case_id, origin, *, local_route="local", parent_usage=True):
    case = {"id": case_id, "provenance": {"origin_ref": origin,
        "captured_at": datetime.now(timezone.utc).isoformat(), "baseline_ref": "git:a",
        "test_ref": "git:b"}, "task": "Fix " + case_id,
        "baseline_source": "def solve(): return 0\n" + case_id}
    result = {"direct": {"paid": {"state": "completed", "usage": {"output_tokens": 10},
        "list_cost_usd": 0.01, "cost_basis": "list", "requested_model": "sonnet",
        "reported_model": "claude-sonnet-5-5"}}, "direct_verification": {"state": "pass"},
        "local_first": {"route": local_route, "reason": "verified" if local_route == "local"
            else "verification_failed", "local": {"state": "completed",
                "usage": {"output_tokens": 5}, "requested_model": "qwen",
                "reported_model": "qwen"},
            "paid": None if local_route == "local" else {"state": "completed",
                "usage": {"output_tokens": 10}, "list_cost_usd": 0.012,
                "cost_basis": "list", "requested_model": "sonnet",
                "reported_model": "claude-sonnet-5-5"}},
        "local_final_verification": {"state": "pass"},
        "parent": {"direct": {"state": "completed",
            "usage": {"input_tokens": 100} if parent_usage else None},
            "local_first": {"state": "completed",
            "usage": {"input_tokens": 110} if parent_usage else None}},
        "arm_elapsed_seconds": {"direct": 4.0, "local_first": 6.0},
        "arm_order": "direct_first"}
    item = {"cohort": "natural_shadow", "cases": [case],
        "settings": {"mode": "shadow", "cohort": "natural_shadow",
            "cohort_start_at": "2026-10-18T13:46:18+00:00",
            "excluded_case_ids": ["historical-answer-normalization", "historical-paid-fallback-format"],
            "excluded_baseline_sha256": ["e3b6d0d022590318e5103c0e1f767cb916daab7c430cea794fe4e69b88a77e4b",
                "639679fff40fd3737039a190f4931217d60a60bf1095587dce55f4be5ea74483"],
            "excluded_task_sha256": ["7c433a588a3aed602ce0633f1e9a47a3b3b9ff3a44d3f242459e7b05c74b5d54",
                "17dd55b6336069aa74a89f911c6ea04d9d166a10ddad1f373d64d1f26e58b596"]},
        "run_metadata": {"qwen_model": "qwen", "paid_model": "sonnet",
            "cases_sha256": case_id},
        "prompt_prefix": "Complete replacement source.\n",
        "validations": {case_id: {"state": "ready"}},
        "results": {case_id: result}, "in_progress": None}
    item["result_sha256"] = {case_id: _digest(result)}
    item["contract_sha256"] = _digest({key: item[key]
        for key in ("cases", "settings", "run_metadata", "prompt_prefix")} |
        {"parent_prompt_version": 3})
    return item


def write_receipt(path, item):
    case_id = item["cases"][0]["id"]
    item["result_sha256"] = {case_id: _digest(item["results"][case_id])}
    item["contract_sha256"] = _digest({key: item[key]
        for key in ("cases", "settings", "run_metadata", "prompt_prefix")} |
        {"parent_prompt_version": 3})
    path.write_text(json.dumps(item), encoding="utf-8")


def test_cohort_aggregates_distinct_verified_natural_receipts_without_canary_decision(tmp_path):
    paths = []
    for case_id, origin, route in (("one", "task-1", "local"),
                                   ("two", "task-2", "paid")):
        path = tmp_path / f"{case_id}.json"
        write_receipt(path, receipt(case_id, origin, local_route=route))
        paths.append(path)
    summary = summarize_receipts(paths, target_cases=30)
    assert summary["eligible_cases"] == 2
    assert summary["remaining_cases"] == 28
    assert summary["verified_local_deliveries"] == 1
    assert summary["paid_worker_calls_local_first"] == 1
    assert summary["observed_sonnet_list_cost_difference_usd"] == pytest.approx(0.008)
    assert summary["net_billed_savings_usd"] is None
    assert summary["ready_for_review"] is False
    assert summary["live_canary_authorized"] is False


def test_cohort_retains_requested_and_reported_model_choices(tmp_path):
    item = receipt("one", "task-1")
    item["results"]["one"]["direct"]["paid"].update(
        requested_model="sonnet", reported_model="claude-sonnet-5-5")
    item["results"]["one"]["local_first"]["local"].update(
        requested_model="qwen2.5-coder", reported_model="qwen2.5-coder")
    path = tmp_path / "one.receipt.json"
    write_receipt(path, item)
    summary = summarize_receipts([path], target_cases=30)
    assert summary["model_choices"]["sonnet_direct"]["requested"]["sonnet"] == 1
    assert summary["model_choices"]["sonnet_direct"]["reported"]["claude-sonnet-5-5"] == 1
    assert summary["model_choices"]["qwen"]["reported"]["qwen2.5-coder"] == 1


def test_cohort_rejects_duplicate_origin_and_missing_parent_usage(tmp_path):
    one = receipt("one", "same")
    two = receipt("two", "same")
    paths = []
    for index, item in enumerate((one, two)):
        path = tmp_path / f"{index}.json"
        write_receipt(path, item)
        paths.append(path)
    with pytest.raises(ValueError, match="duplicate origin"):
        summarize_receipts(paths, target_cases=2)
    two["cases"][0]["provenance"]["origin_ref"] = "other"
    two["results"]["two"]["parent"]["direct"]["usage"] = None
    write_receipt(paths[1], two)
    summary = summarize_receipts(paths, target_cases=2)
    assert summary["ready_for_review"] is True
    assert summary["usage_coverage_complete"] is False


def test_duplicate_task_or_baseline_and_mixed_protocol_are_rejected(tmp_path):
    one = receipt("one", "task-1")
    two = receipt("two", "task-2")
    first = tmp_path / "one.receipt.json"
    second = tmp_path / "two.receipt.json"
    write_receipt(first, one)
    two["cases"][0]["task"] = one["cases"][0]["task"]
    write_receipt(second, two)
    with pytest.raises(ValueError, match="duplicate task"):
        summarize_receipts([first, second], target_cases=2)
    two["cases"][0]["task"] = "different task"
    two["cases"][0]["baseline_source"] = one["cases"][0]["baseline_source"]
    write_receipt(second, two)
    with pytest.raises(ValueError, match="duplicate baseline"):
        summarize_receipts([first, second], target_cases=2)
    two["cases"][0]["baseline_source"] = "new source"
    two["prompt_prefix"] = "different prompt"
    write_receipt(second, two)
    with pytest.raises(ValueError, match="mixed protocol"):
        summarize_receipts([first, second], target_cases=2)


def test_duplicate_or_protocol_change_is_rejected_before_new_dispatch(tmp_path):
    existing = receipt("one", "task-1")
    path = tmp_path / "one.receipt.json"
    write_receipt(path, existing)
    new = receipt("two", "task-2")
    new["cases"][0]["task"] = existing["cases"][0]["task"]
    with pytest.raises(ValueError, match="duplicate task"):
        check_new_case(new["cases"][0], new["settings"], new["prompt_prefix"],
            new["run_metadata"], [path])
    new["cases"][0]["task"] = "different"
    with pytest.raises(ValueError, match="mixed protocol"):
        check_new_case(new["cases"][0], new["settings"], "changed prompt",
            new["run_metadata"], [path])


def test_failed_arm_has_no_comparable_cost_or_latency_but_counts_as_attempt(tmp_path):
    item = receipt("one", "task-1")
    item["results"]["one"]["direct_verification"]["state"] = "fail"
    path = tmp_path / "one.receipt.json"
    write_receipt(path, item)
    summary = summarize_receipts([path], target_cases=1)
    assert summary["attempted_cases"] == 1
    assert summary["matched_verified_latency_cases"] == 0
    assert summary["observed_sonnet_list_cost_difference_usd"] is None
    assert summary["ready_for_review"] is True
    assert summary["live_canary_authorized"] is False


def test_in_progress_receipt_is_visible_not_dropped(tmp_path):
    item = receipt("one", "task-1")
    item["results"] = {}
    item["result_sha256"] = {}
    item["in_progress"] = {"case_id": "one", "arm_order": "direct_first"}
    path = tmp_path / "one.receipt.json"
    path.write_text(json.dumps(item), encoding="utf-8")
    summary = summarize_receipts([path], target_cases=1)
    assert summary["attempted_cases"] == 1
    assert summary["in_progress_cases"] == 1
    assert summary["completed_cases"] == 0
    assert summary["ready_for_review"] is False


def test_model_mismatch_and_failed_parent_usage_are_visible(tmp_path):
    item = receipt("one", "task-1")
    item["results"]["one"]["local_first"]["local"]["reported_model"] = "other"
    item["results"]["one"]["parent"]["direct"]["state"] = "failed"
    path = tmp_path / "one.receipt.json"
    write_receipt(path, item)
    summary = summarize_receipts([path], target_cases=1)
    assert summary["model_identity_complete"] is False
    assert summary["usage_coverage_complete"] is False


def test_nan_or_non_list_cost_is_not_comparable(tmp_path):
    item = receipt("one", "task-1")
    item["results"]["one"]["direct"]["paid"]["list_cost_usd"] = float("nan")
    path = tmp_path / "one.receipt.json"
    write_receipt(path, item)
    summary = summarize_receipts([path], target_cases=1)
    assert summary["observed_sonnet_list_cost_difference_usd"] is None
    item["results"]["one"]["direct"]["paid"]["list_cost_usd"] = 0.01
    item["results"]["one"]["direct"]["paid"]["cost_basis"] = "unknown"
    write_receipt(path, item)
    summary = summarize_receipts([path], target_cases=1)
    assert summary["sonnet_list_cost_coverage_complete"] is False


def test_retro_or_unverified_receipt_cannot_enter_natural_cohort(tmp_path):
    item = receipt("one", "task-1")
    item["cohort"] = "retrospective_shadow"
    path = tmp_path / "one.json"
    write_receipt(path, item)
    with pytest.raises(ValueError, match="natural"):
        summarize_receipts([path], target_cases=30)
    item["cohort"] = "natural_shadow"
    item["settings"]["cohort"] = "retrospective_shadow"
    write_receipt(path, item)
    with pytest.raises(ValueError, match="natural"):
        summarize_receipts([path], target_cases=30)
    item["settings"]["cohort"] = "natural_shadow"
    item["results"]["one"]["local_final_verification"]["state"] = "fail"
    write_receipt(path, item)
    summary = summarize_receipts([path], target_cases=30)
    assert summary["ready_for_review"] is False


def test_relabelled_legacy_baseline_is_rejected_even_with_valid_digest(tmp_path):
    item = receipt("new-looking-id", "claimed-new-origin")
    legacy = json.loads((Path(__file__).resolve().parents[1] /
        "fixtures" / "code-fix-shadow-20261018.json").read_text(encoding="utf-8"))[0]
    item["cases"][0]["baseline_source"] = legacy["baseline_source"]
    path = tmp_path / "new-looking-id.receipt.json"
    write_receipt(path, item)
    with pytest.raises(ValueError, match="excluded retrospective"):
        summarize_receipts([path], target_cases=30)


def test_modified_result_digest_is_rejected(tmp_path):
    item = receipt("one", "task-1")
    path = tmp_path / "one.json"
    write_receipt(path, item)
    item["results"]["one"]["local_first"]["route"] = "paid"
    path.write_text(json.dumps(item), encoding="utf-8")
    with pytest.raises(ValueError, match="digest"):
        summarize_receipts([path], target_cases=30)


def test_missing_direct_verification_remains_a_quality_gap(tmp_path):
    item = receipt("one", "task-1")
    item["results"]["one"]["direct_verification"] = None
    path = tmp_path / "one.json"
    write_receipt(path, item)
    summary = summarize_receipts([path], target_cases=1)
    assert summary["direct_verified"] == 0
    assert summary["ready_for_review"] is True


def test_reaching_target_only_opens_review_and_never_activates_canary(tmp_path):
    paths = []
    for case_id in ("one", "two"):
        path = tmp_path / f"{case_id}.receipt.json"
        write_receipt(path, receipt(case_id, f"task-{case_id}"))
        paths.append(path)
    summary = summarize_receipts(paths, target_cases=2)
    assert summary["ready_for_review"] is True
    assert summary["live_canary_authorized"] is False


def test_missing_sonnet_list_cost_remains_visible_at_review(tmp_path):
    item = receipt("one", "task-1")
    item["results"]["one"]["direct"]["paid"]["list_cost_usd"] = None
    path = tmp_path / "one.receipt.json"
    write_receipt(path, item)
    summary = summarize_receipts([path], target_cases=1)
    assert summary["observed_sonnet_list_cost_difference_usd"] is None
    assert summary["ready_for_review"] is True
    assert summary["sonnet_list_cost_coverage_complete"] is False
