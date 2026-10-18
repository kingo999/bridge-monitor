"""Provider-separated token ledger for an offline matched shadow trial."""

import json
from collections import defaultdict


PARENT_KEYS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
               "output_tokens", "reasoning_output_tokens")


def parse_codex_jsonl(stream: str, *, requested_model: str,
                      elapsed_seconds: float) -> dict:
    answer = None
    usage = defaultdict(int)
    turn_count = 0
    usage_turns = 0
    thread_id = None
    failed = False
    for line in stream.splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id")
        elif event.get("type") == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message":
                answer = item.get("text")
        elif event.get("type") == "turn.completed":
            turn_count += 1
            raw = event.get("usage")
            if raw is not None:
                if not isinstance(raw, dict) or any(
                        type(value) is not int or value < 0 for value in raw.values()):
                    raise ValueError("invalid parent token usage")
                if any(key in raw for key in PARENT_KEYS):
                    usage_turns += 1
                    for key in PARENT_KEYS:
                        usage[key] += raw.get(key, 0)
        elif event.get("type") == "turn.failed":
            failed = True
    complete = turn_count > 0 and usage_turns == turn_count
    return {"state": "failed" if failed else "completed" if complete
            else "usage_unavailable", "requested_model": requested_model,
            "reported_model": None, "thread_id": thread_id, "answer": answer,
            "usage": dict(usage) if complete else None, "elapsed_seconds": elapsed_seconds,
            "turn_count": turn_count, "usage_turns": usage_turns}


def summarize_shadow(results: dict[str, dict], *, cohort: str = "retrospective_shadow") -> dict:
    totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    coverage: dict[str, dict[str, int]] = defaultdict(lambda: {"known": 0, "missing": 0})
    direct_calls = 0
    local_path_calls = 0
    verified = 0
    for item in results.values():
        direct = item.get("direct") or {}
        local = item.get("local_first") or {}
        parent = item.get("parent") or {}
        for name, reply in (("sonnet_direct", direct.get("paid")),
                            ("qwen", local.get("local")),
                            ("sonnet_fallback", local.get("paid")),
                            ("gpt_direct", parent.get("direct")),
                            ("gpt_local_first", parent.get("local_first"))):
            if reply is not None:
                covered = isinstance(reply.get("usage"), dict) and bool(reply["usage"])
                coverage[name]["known" if covered else "missing"] += 1
            if reply and isinstance(reply.get("usage"), dict):
                for key, value in reply["usage"].items():
                    if type(value) is int and value >= 0:
                        totals[name][key] += value
        direct_calls += direct.get("paid") is not None
        local_path_calls += local.get("paid") is not None
        verified += local.get("route") == "local" and local.get("reason") == "verified"
    required = ("sonnet_direct", "qwen", "gpt_direct", "gpt_local_first")
    complete = bool(results) and all(coverage[name]["known"] == len(results)
        for name in required) and coverage["sonnet_fallback"]["missing"] == 0
    return {"cases": len(results), "cohort": cohort,
            "usage_coverage": dict(coverage), "comparison_complete": complete,
            "paid_worker_calls_direct": direct_calls,
            "paid_worker_calls_local_first": local_path_calls,
            "verified_local_deliveries": verified,
            "token_totals": {key: dict(value) for key, value in totals.items()},
            "net_billed_savings_usd": None,
            "savings_limit": "Provider-specific tokens are not interchangeable; billing and "
                             "live desktop parent usage are not observed."}
