"""Aggregate separately registered natural code-fix shadow attempts for review."""

import hashlib
import json
import math
from pathlib import Path
from statistics import median

from bridge.code_fix_shadow_runner import _digest, validate_natural_case
from bridge.shadow_accounting import summarize_shadow


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[(95 * len(ordered) + 99) // 100 - 1], 3)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _protocol(receipt: dict) -> str:
    metadata = dict(receipt.get("run_metadata") or {})
    metadata.pop("cases_sha256", None)  # Each task has its own fixture.
    return _digest({"settings": receipt.get("settings"),
        "prompt_prefix": receipt.get("prompt_prefix"), "run_metadata": metadata})


def _identity_ok(result: dict, metadata: dict) -> bool:
    local = result.get("local_first") or {}
    replies = (
        ((result.get("direct") or {}).get("paid"), metadata.get("paid_model"), False),
        (local.get("local"), metadata.get("qwen_model"), True),
        (local.get("paid"), metadata.get("paid_model"), False),
    )
    for reply, pinned, exact_reported in replies:
        if reply is None:
            continue
        if not pinned or reply.get("requested_model") != pinned:
            return False
        reported = reply.get("reported_model")
        if not isinstance(reported, str) or not reported.strip():
            return False
        if exact_reported and reported != pinned:
            return False
    return True


def check_new_case(case: dict, settings: dict, prompt_prefix: str,
                   run_metadata: dict, existing_paths: list[str | Path]) -> None:
    """Fail before dispatch if this case repeats a task or changes the cohort protocol."""
    if not existing_paths:
        return
    summary = summarize_receipts(existing_paths, target_cases=1)
    candidate = _protocol({"settings": settings, "prompt_prefix": prompt_prefix,
                           "run_metadata": run_metadata})
    if summary["protocol_sha256"] != candidate:
        raise ValueError("mixed protocol in natural cohort")
    for source in existing_paths:
        prior = json.loads(Path(source).read_text(encoding="utf-8"))["cases"][0]
        if prior["id"] == case["id"]:
            raise ValueError("duplicate case ID")
        if prior["provenance"]["origin_ref"] == case["provenance"]["origin_ref"]:
            raise ValueError("duplicate origin")
        if _sha(prior["task"]) == _sha(case["task"]):
            raise ValueError("duplicate task")
        if _sha(prior["baseline_source"]) == _sha(case["baseline_source"]):
            raise ValueError("duplicate baseline")


def summarize_receipts(paths: list[str | Path], *, target_cases: int = 30) -> dict:
    if type(target_cases) is not int or target_cases < 1:
        raise ValueError("target cases must be positive")
    merged = {}
    origins, ids, tasks, baselines = set(), set(), set(), set()
    protocol = None
    direct_times, local_times = [], []
    arm_orders = {"direct_first": 0, "local_first": 0}
    direct_cost = local_cost = 0.0
    cost_known = model_identity = True
    direct_verified = final_verified = incomplete = matched_verified = 0
    for source in paths:
        path = Path(source)
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if (receipt.get("cohort") != "natural_shadow" or
                (receipt.get("settings") or {}).get("cohort") != "natural_shadow"):
            raise ValueError(f"natural cohort requires natural receipt: {path}")
        contract = {key: receipt.get(key) for key in
            ("cases", "settings", "run_metadata", "prompt_prefix")}
        contract["parent_prompt_version"] = 3
        if receipt.get("contract_sha256") != _digest(contract):
            raise ValueError(f"contract digest mismatch: {path}")
        candidate_protocol = _protocol(receipt)
        if protocol is not None and candidate_protocol != protocol:
            raise ValueError(f"mixed protocol in natural cohort: {path}")
        protocol = candidate_protocol
        cases = receipt.get("cases")
        if not isinstance(cases, list) or len(cases) != 1:
            raise ValueError(f"natural receipt needs one case: {path}")
        case = cases[0]
        validate_natural_case(case, receipt["settings"])
        case_id = case.get("id")
        provenance = case.get("provenance") or {}
        origin = provenance.get("origin_ref")
        if not isinstance(origin, str) or not origin.strip():
            raise ValueError(f"natural provenance missing: {path}")
        if origin in origins:
            raise ValueError(f"duplicate origin: {origin}")
        if case_id in ids:
            raise ValueError(f"duplicate case ID: {case_id}")
        task_hash = _sha(case["task"])
        baseline_hash = _sha(case["baseline_source"])
        if task_hash in tasks:
            raise ValueError(f"duplicate task: {path}")
        if baseline_hash in baselines:
            raise ValueError(f"duplicate baseline: {path}")
        if (receipt.get("validations", {}).get(case_id, {}).get("state") != "ready"):
            raise ValueError(f"unvalidated case: {path}")
        origins.add(origin)
        ids.add(case_id)
        tasks.add(task_hash)
        baselines.add(baseline_hash)
        results = receipt.get("results") or {}
        progress = receipt.get("in_progress")
        if progress is not None:
            if progress.get("case_id") != case_id or results:
                raise ValueError(f"invalid in-progress receipt: {path}")
            order = progress.get("arm_order")
            incomplete += 1
        else:
            if set(results) != {case_id}:
                raise ValueError(f"incomplete natural result: {path}")
            result = results[case_id]
            if receipt.get("result_sha256", {}).get(case_id) != _digest(result):
                raise ValueError(f"result digest mismatch: {path}")
            merged[case_id] = result
            order = result.get("arm_order")
            direct_ok = (result.get("direct_verification") or {}).get("state") == "pass"
            final_ok = (result.get("local_final_verification") or {}).get("state") == "pass"
            direct_verified += direct_ok
            final_verified += final_ok
            model_identity &= _identity_ok(result, receipt.get("run_metadata") or {})
            direct = (result.get("direct") or {}).get("paid") or {}
            fallback = (result.get("local_first") or {}).get("paid")
            for reply, side in ((direct, "direct"), (fallback, "local")):
                if reply is None:
                    continue
                value = reply.get("list_cost_usd")
                if (type(value) not in (int, float) or not math.isfinite(value) or
                        value < 0 or reply.get("cost_basis") != "list"):
                    cost_known = False
                elif side == "direct":
                    direct_cost += float(value)
                else:
                    local_cost += float(value)
            elapsed = result.get("arm_elapsed_seconds") or {}
            measured = all(type(elapsed.get(name)) in (int, float) and
                math.isfinite(elapsed[name]) and elapsed[name] >= 0
                for name in ("direct", "local_first"))
            if direct_ok and final_ok and measured:
                matched_verified += 1
                direct_times.append(float(elapsed["direct"]))
                local_times.append(float(elapsed["local_first"]))
        if order not in arm_orders:
            raise ValueError(f"unknown arm order: {path}")
        arm_orders[order] += 1
    summary = summarize_shadow(merged, cohort="natural_shadow")
    attempted = len(ids)
    completed = len(merged)
    comparable = completed > 0 and matched_verified == completed and cost_known
    summary.update({"target_cases": target_cases, "eligible_cases": attempted,
        "attempted_cases": attempted, "completed_cases": completed,
        "in_progress_cases": incomplete,
        "remaining_cases": max(target_cases - attempted, 0),
        "direct_verified": direct_verified, "local_final_verified": final_verified,
        "matched_verified_latency_cases": matched_verified,
        "arm_order_counts": arm_orders,
        "protocol_sha256": protocol,
        "model_identity_complete": model_identity and completed > 0 and incomplete == 0,
        "usage_coverage_complete": summary["comparison_complete"] and incomplete == 0,
        "latency_coverage_complete": matched_verified == completed and completed > 0
            and incomplete == 0,
        "direct_median_seconds": round(median(direct_times), 3) if direct_times else None,
        "local_first_median_seconds": round(median(local_times), 3) if local_times else None,
        "direct_p95_seconds": _p95(direct_times),
        "local_first_p95_seconds": _p95(local_times),
        "observed_sonnet_list_cost_difference_usd": round(direct_cost - local_cost, 9)
            if comparable and incomplete == 0 else None,
        "sonnet_list_cost_coverage_complete": cost_known and completed > 0
            and incomplete == 0,
        "ready_for_review": attempted >= target_cases and incomplete == 0
            and completed == attempted,
        "live_canary_authorized": False})
    return summary
