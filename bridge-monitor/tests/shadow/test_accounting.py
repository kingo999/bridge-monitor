import json

import pytest

from bridge.shadow_accounting import parse_codex_jsonl, summarize_shadow


def test_codex_parent_usage_keeps_cache_categories_and_raw_response():
    stream = "\n".join(json.dumps(event) for event in [
        {"type": "thread.started", "thread_id": "example"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}},
        {"type": "turn.completed", "usage": {"input_tokens": 100,
            "cached_input_tokens": 70, "cache_write_input_tokens": 2,
            "output_tokens": 8, "reasoning_output_tokens": 1}},
    ])
    parsed = parse_codex_jsonl(stream, requested_model="gpt-test", elapsed_seconds=1.2)
    assert parsed["state"] == "completed" and parsed["answer"] == "done"
    assert parsed["usage"]["cached_input_tokens"] == 70
    assert parsed["requested_model"] == "gpt-test"
    assert parsed["reported_model"] is None
    assert parsed["thread_id"] == "example"


def test_missing_or_bad_parent_usage_remains_unknown():
    parsed = parse_codex_jsonl('{"type":"turn.completed"}',
        requested_model="gpt-test", elapsed_seconds=1)
    assert parsed["state"] == "usage_unavailable" and parsed["usage"] is None
    with pytest.raises(ValueError):
        parse_codex_jsonl('{"type":"turn.completed","usage":{"input_tokens":-1}}',
            requested_model="gpt-test", elapsed_seconds=1)


def test_multiple_parent_turns_sum_only_with_complete_usage():
    events = [{"type": "turn.completed", "usage": {"input_tokens": 10}},
              {"type": "turn.completed", "usage": {"input_tokens": 20}}]
    complete = parse_codex_jsonl("\n".join(map(json.dumps, events)),
        requested_model="gpt-test", elapsed_seconds=1)
    assert complete["usage"]["input_tokens"] == 30
    assert complete["turn_count"] == complete["usage_turns"] == 2
    events[1].pop("usage")
    partial = parse_codex_jsonl("\n".join(map(json.dumps, events)),
        requested_model="gpt-test", elapsed_seconds=1)
    assert partial["state"] == "usage_unavailable" and partial["usage"] is None


def test_summary_reports_paid_worker_avoidance_without_claiming_billed_savings():
    direct = {"route": "paid", "paid": {"state": "completed", "usage": {"input_tokens": 5,
        "cache_read_input_tokens": 100, "output_tokens": 20}, "elapsed_seconds": 2.0}}
    local = {"route": "local", "reason": "verified", "local": {"state": "completed", "usage": {"input_tokens": 40,
        "output_tokens": 15}, "elapsed_seconds": 3.0}, "paid": None}
    parents = {"direct": {"state": "completed", "usage": {"input_tokens": 100}},
               "local_first": {"state": "completed", "usage": {"input_tokens": 110}}}
    summary = summarize_shadow({"one": {"direct": direct, "local_first": local,
        "parent": parents}})
    assert summary["paid_worker_calls_direct"] == 1
    assert summary["paid_worker_calls_local_first"] == 0
    assert summary["verified_local_deliveries"] == 1
    assert summary["token_totals"]["sonnet_direct"]["cache_read_input_tokens"] == 100
    assert summary["token_totals"]["qwen"]["output_tokens"] == 15
    assert summary["token_totals"]["gpt_local_first"]["input_tokens"] == 110
    assert summary["net_billed_savings_usd"] is None
    assert summary["usage_coverage"]["gpt_direct"]["known"] == 1
    assert summary["comparison_complete"] is True


def test_missing_token_usage_marks_comparison_incomplete():
    result = {"direct": {"paid": {"usage": {}}},
              "local_first": {"local": {"usage": {"input_tokens": 1}}, "paid": None},
              "parent": {"direct": {"usage": None},
                         "local_first": {"usage": {"input_tokens": 1}}}}
    summary = summarize_shadow({"one": result})
    assert summary["comparison_complete"] is False
    assert summary["usage_coverage"]["sonnet_direct"]["missing"] == 1
