"""Conservative usage accounting over an obs_reader observation.

Raw schema-1 counters (CLI 0.162.1) are session cumulative. A per-call figure is
produced only as an *interval estimate* between two adjacent, trustworthy
observations on the same thread, and is never a billed cost.
"""
from __future__ import annotations

from datetime import datetime, timezone

from obs_reader import parse_timestamp

SUPPORTED_SCHEMA = 1
SUPPORTED_CLI = frozenset({"0.162.1"})
USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")
ORDER_TOLERANCE_S = 2
DURATION_TOLERANCE_MS = 1500  # timestamps have one-second resolution
STALE_MARKER_S = 3600

OUTCOME_CLASS = {
    "success": "success",
    "success_partial_persistence": "partial",
    "success_session_in_doubt": "in_doubt",
    "error": "error",
    "timeout": "timeout",
    "cancelled": "cancelled",
}

REASON_TEXT = {
    "fresh_baseline": "Fresh thread (resumed=false): zero baseline, so the estimate equals the cumulative counters.",
    "no_baseline_first_resumed": "First observed call on a resumed thread: no earlier baseline, so no per-call estimate.",
    "after_inference_started_unmeasured": "An earlier call on this session may have run inference without measured usage.",
    "after_recovery_uncertain": "An earlier call on this session ended with recovery uncertainty.",
    "after_usage_unavailable": "An earlier call on this thread had missing or invalid usage.",
    "after_unsupported_cli": "An earlier call on this thread used an unsupported CLI version.",
    "after_unsupported_schema": "An earlier record had an unsupported schema.",
    "after_file_head_truncated": "Telemetry before this point was truncated (file too large).",
    "after_rotation_gap": "A rotated telemetry segment is missing before this point.",
    "after_malformed_line": "A malformed telemetry line may have hidden an earlier call.",
    "after_partial_trailing_line": "A partial telemetry line may have hidden an earlier call.",
    "after_oversized_line": "An oversized telemetry line was skipped and may have hidden an earlier call.",
    "after_non_object_line": "An unreadable telemetry line may have hidden an earlier call.",
    "after_record_missing_call_id": "A record without a usable call ID may have hidden an earlier call.",
    "after_conflicting_call_id": "Conflicting records share a call ID before this point.",
    "after_source_changed_during_read": "Telemetry changed while it was being read; continuity is not established.",
    "after_scan_budget_exceeded": "Older telemetry was not read because the scan budget was exhausted.",
    "after_order_conflict": "Telemetry ordering contradicted timestamps before this point.",
    "after_other_ambiguity": "Earlier telemetry for this session was ambiguous.",
    "unsupported_schema": "Unsupported telemetry schema: usage basis unknown.",
    "unsupported_cli": "Unsupported CLI version: accounting basis unknown.",
    "recovery_uncertain": "Outcome carries recovery uncertainty; not a completed, independently reviewable turn.",
    "inference_started_unmeasured": "Inference may have started but usage was not measured.",
    "no_inference_started": "Failed before inference started; no model usage expected.",
    "unrecognized_outcome": "Outcome is not recognised; treated as ambiguous.",
    "success_inconsistent": "Success record contradicts its own flags; treated as ambiguous.",
    "usage_unavailable": "Usage is missing (unknown, not zero).",
    "usage_invalid": "Usage values are invalid or inconsistent.",
    "missing_thread_identity": "No thread identity on a successful call; cannot chain counters.",
    "identity_unknown": "Repo, session name or model is unknown; unknown identities are never matched, so no estimate.",
    "delta_inconsistent": "Both snapshots are valid cumulative counters but their difference is not a coherent "
                          "per-call usage (cached exceeds input, or reasoning exceeds output); no estimate.",
    "ordering_unverifiable": "Timestamps do not allow verifying call order.",
    "order_conflict": "Record order contradicts timestamps for this session.",
    "fresh_flag_conflict": "Marked fresh but the thread was already observed.",
    "resume_flag_unknown": "Resume flag missing; baseline cannot be established.",
    "identity_mismatch": "Same thread appeared under a different repo or session name.",
    "model_changed": "Model changed since the previous call on this thread; not comparable.",
    "counter_reset": "Cumulative counters decreased (reset); no per-call estimate.",
    "conflicting_call_id": "Conflicting records share this call ID; excluded from accounting.",
}

LIMITATIONS = [
    "Raw token counters are session cumulative for the observed CLI (0.162.1); they are not per-call and must not be summed.",
    "Per-call figures are telemetry-derived interval estimates between adjacent observations. Missing writes or turns outside this bridge cannot be ruled out from these logs alone.",
    "Nothing here is billed cost, subscription balance or context-window size.",
    "Reasoning output tokens are a subset of output tokens and are not additive.",
    "Unknown stays unknown: missing usage is never shown as zero.",
    "Active in-flight markers are observed states, not proof of a live process or of what the agent is doing.",
]


def _reason(code):
    return {"code": code, "text": REASON_TEXT.get(code, code)}


def _ratio(num, den):
    if num is None or den is None or den <= 0:
        return None
    return round(num / den, 4)


def _usage_valid(rec):
    """Return (ok, reason_code). Needs input and output; cached/reasoning optional."""
    u = rec["usage"]
    if not rec["usage_present"] or u["input_tokens"] is None or u["output_tokens"] is None:
        return False, "usage_invalid" if rec["usage_invalid"] else "usage_unavailable"
    if rec["usage_invalid"]:
        return False, "usage_invalid"
    if u["cached_input_tokens"] is not None and u["cached_input_tokens"] > u["input_tokens"]:
        return False, "usage_invalid"
    if u["reasoning_output_tokens"] is not None and u["reasoning_output_tokens"] > u["output_tokens"]:
        return False, "usage_invalid"
    return True, None


def _delta_incoherent(delta):
    """Subset invariants must also hold for the difference, not only for each snapshot."""
    cached, inp = delta["cached_input_tokens"], delta["input_tokens"]
    reasoning, out = delta["reasoning_output_tokens"], delta["output_tokens"]
    return ((cached is not None and inp is not None and cached > inp)
            or (reasoning is not None and out is not None and reasoning > out))


def _duration_view(rec):
    started = parse_timestamp(rec.get("started_at"))
    ended = parse_timestamp(rec.get("ended_at"))
    e2e = None
    if started and ended:
        ms = int((ended - started).total_seconds() * 1000)
        e2e = ms if ms >= 0 else None
    run = rec.get("run_ms")
    notes = []
    if e2e is not None and run is not None and run > e2e + DURATION_TOLERANCE_MS:
        notes.append("execution_exceeds_end_to_end")
    return {"run_ms": run, "queue_ms": rec.get("queue_ms"), "end_to_end_ms": e2e,
            "notes": notes}, started, ended


class _Chains:
    def __init__(self):
        self.by_thread = {}

    def invalidate(self, reason, repo_key=None, name=None, thread_id=None, global_scope=False):
        for tid, ch in self.by_thread.items():
            hit = (global_scope or tid == thread_id
                   or (repo_key is not None and ch["repo_key"] == repo_key
                       and (name is None or ch["name"] == name)))
            if hit:
                ch["baseline"] = None
                ch["invalid_reason"] = reason


def _barrier_reason(code):
    key = "after_" + code
    if key in REASON_TEXT:
        return key
    return {"file_refused_link": "after_other_ambiguity"}.get(code, "after_other_ambiguity")


def _empty_row(ev, rec, outcome_class):
    dur, _s, _e = _duration_view(rec)
    return {
        "call_id": rec.get("call_id"),
        "tool": rec.get("tool"),
        "started_at": rec.get("started_at"),
        "ended_at": rec.get("ended_at"),
        "route": rec["route"],
        "session": rec["session"],
        "outcome": rec.get("outcome"),
        "outcome_class": outcome_class,
        "error_category": rec.get("error_category"),
        "error_code": rec.get("error_code"),
        "exit_code": rec.get("exit_code"),
        "inference_may_have_started": rec.get("inference_may_have_started"),
        "durations": dur,
        "usage_raw": {f: rec["usage"][f] for f in USAGE_FIELDS + ("total_tokens",)},
        "usage_invalid_fields": list(rec.get("usage_invalid", [])),
        "usage_basis": "unknown",
        "usage_delta": None,
        "ratios": {"cache_raw": None, "cache_delta": None,
                   "reasoning_share_raw": None, "reasoning_share_delta": None},
        "estimate": {"status": "not_estimated", "reasons": []},
        "pointer_state": rec.get("pointer_state"),
        "history": rec.get("history"),
        "session_state": rec.get("session_state"),
        "repo": rec.get("repo"),
        "cli_version": rec.get("cli_version"),
        "schema_version": rec.get("schema_version"),
        "prompt_chars": rec.get("prompt_chars"),
        "answer_chars": rec.get("answer_chars"),
        "warnings": rec.get("warnings", []),
        "warnings_total": rec.get("warnings_total", 0),
        "usage_present": rec.get("usage_present", False),
        "source_file": ev.get("file"),
    }


def _unsupported_row(ev):
    rec = ev["rec"]
    return {
        "call_id": rec.get("call_id"), "tool": None, "started_at": rec.get("started_at"),
        "ended_at": None, "route": {"model": None, "effort": None}, "session": {
            "name": None, "repo_key": None, "thread_id": None, "resumed": None},
        "outcome": None, "outcome_class": "unknown", "error_category": None, "error_code": None,
        "exit_code": None, "inference_may_have_started": None,
        "durations": {"run_ms": None, "queue_ms": None, "end_to_end_ms": None, "notes": []},
        "usage_raw": {f: None for f in USAGE_FIELDS + ("total_tokens",)},
        "usage_invalid_fields": [], "usage_basis": "unknown",
        "usage_delta": None,
        "ratios": {"cache_raw": None, "cache_delta": None,
                   "reasoning_share_raw": None, "reasoning_share_delta": None},
        "estimate": {"status": "not_estimated", "reasons": [_reason("unsupported_schema")]},
        "pointer_state": None, "history": None, "session_state": None, "repo": None,
        "cli_version": None, "schema_version": rec.get("schema_version"),
        "prompt_chars": None, "answer_chars": None, "warnings": [], "warnings_total": 0,
        "usage_present": False, "source_file": ev.get("file"),
    }


def _conflict_row(ev):
    row = _unsupported_row({"rec": {"call_id": ev["call_id"]}, "file": ev.get("file")})
    row["estimate"]["reasons"] = [_reason("conflicting_call_id")]
    row["outcome_class"] = "conflict"
    return row


def _raw_usage_problem(raw, invalid_fields):
    """True when the logged counters are malformed or violate parent/subset invariants.

    Raw values are still displayed; nothing may be derived from them.
    """
    if invalid_fields:
        return True
    cached, inp = raw["cached_input_tokens"], raw["input_tokens"]
    reasoning, out = raw["reasoning_output_tokens"], raw["output_tokens"]
    return ((cached is not None and inp is not None and cached > inp)
            or (reasoning is not None and out is not None and reasoning > out))


def _set_ratios(row):
    raw, delta = row["usage_raw"], row["usage_delta"]
    if row["usage_basis"] != "session_cumulative":
        return  # accounting basis unknown: no derived figures
    if _raw_usage_problem(raw, row["usage_invalid_fields"]):
        return  # invalid counters: every ratio, raw and delta, stays unknown
    row["ratios"]["cache_raw"] = _ratio(raw["cached_input_tokens"], raw["input_tokens"])
    row["ratios"]["reasoning_share_raw"] = _ratio(raw["reasoning_output_tokens"], raw["output_tokens"])
    if delta:
        row["ratios"]["cache_delta"] = _ratio(delta["cached_input_tokens"], delta["input_tokens"])
        row["ratios"]["reasoning_share_delta"] = _ratio(
            delta["reasoning_output_tokens"], delta["output_tokens"])


def _account(ev, chains, scope_last):
    rec = ev["rec"]
    outcome_class = OUTCOME_CLASS.get(rec.get("outcome"), "unknown")
    row = _empty_row(ev, rec, outcome_class)
    reasons = row["estimate"]["reasons"]
    repo_key, name = rec["session"]["repo_key"], rec["session"]["name"]
    thread = rec["session"]["thread_id"]
    scope_known = repo_key is not None
    model = rec["route"]["model"]

    def scoped_barrier(code):
        if scope_known:
            chains.invalidate(code, repo_key=repo_key, name=name, thread_id=thread)
        else:
            chains.invalidate(code, thread_id=thread, global_scope=True)

    cli_ok = rec.get("cli_version") in SUPPORTED_CLI
    row["usage_basis"] = "session_cumulative" if cli_ok else "unknown"
    if not cli_ok:
        reasons.append(_reason("unsupported_cli"))

    # Ordering: stream order is authoritative; timestamps must not contradict it.
    _dur, started, ended = _duration_view(rec)
    order_dt = ended or started
    order_ok = True
    if repo_key is not None and name is not None:  # unknown identities never share a scope
        key = (repo_key, name)
        last = scope_last.get(key)
        if order_dt is not None and last is not None and \
                (last - order_dt).total_seconds() > ORDER_TOLERANCE_S:
            order_ok = False
            reasons.append(_reason("order_conflict"))
            scoped_barrier("after_order_conflict")
        if order_dt is not None and (last is None or order_dt > last):
            scope_last[key] = order_dt

    # Non-success outcomes never yield an estimate; some break continuity.
    if outcome_class == "unknown":
        reasons.append(_reason("unrecognized_outcome"))
        scoped_barrier("after_other_ambiguity")
        return _finish(row)
    if outcome_class in ("error", "timeout", "cancelled"):
        possible = (rec["inference_may_have_started"] is not False
                    or rec.get("history") == "turn_may_exist")
        if possible:
            reasons.append(_reason("inference_started_unmeasured"))
            scoped_barrier("after_inference_started_unmeasured")
        else:
            reasons.append(_reason("no_inference_started"))
            row["estimate"]["status"] = "not_applicable"
        return _finish(row)
    if outcome_class in ("partial", "in_doubt"):
        reasons.append(_reason("recovery_uncertain"))
        scoped_barrier("after_recovery_uncertain")
        return _finish(row)

    # Plain success.
    problems = []
    if rec["inference_may_have_started"] is not True or (rec["exit_code"] not in (0, None)) \
            or rec.get("session_state") != "ready" \
            or rec.get("pointer_state") not in ("bound", "unchanged"):
        problems.append("success_inconsistent")
    ok, why = _usage_valid(rec)
    if not ok:
        problems.append(why)
    if thread is None:
        problems.append("missing_thread_identity")
    if repo_key is None or name is None or model is None:
        problems.append("identity_unknown")
    if not cli_ok:
        problems.append("unsupported_cli_break")
    if order_dt is None:
        problems.append("ordering_unverifiable")
    if not order_ok:
        problems.append("order_conflict_break")

    if not problems:
        usage = rec["usage"]
        ident = (repo_key, name, rec["route"]["model"], rec["cli_version"])
        ch = chains.by_thread.get(thread)
        resumed = rec["session"]["resumed"]
        status, delta, extra = "not_estimated", None, None

        def adopt():
            chains.by_thread[thread] = {
                "repo_key": repo_key, "name": name, "ident": ident, "baseline": dict(usage),
                "invalid_reason": None, "seen": True, "last_raw": dict(usage),
                "last_call_id": rec["call_id"], "calls": (ch["calls"] if ch else 0) + 1,
                "model": rec["route"]["model"]}

        def abandon(code):
            chains.by_thread[thread] = {
                "repo_key": repo_key, "name": name, "ident": ident, "baseline": None,
                "invalid_reason": code, "seen": True, "last_raw": dict(usage),
                "last_call_id": rec["call_id"], "calls": (ch["calls"] if ch else 0) + 1,
                "model": rec["route"]["model"]}

        if resumed is False:
            if ch is not None and ch["seen"]:
                reasons.append(_reason("fresh_flag_conflict"))
                abandon("after_other_ambiguity")
            else:
                delta = {f: usage[f] for f in USAGE_FIELDS}
                status = "fresh_baseline_estimate"
                reasons.append(_reason("fresh_baseline"))
                adopt()
        elif resumed is True:
            if ch is None or ch["baseline"] is None:
                reasons.append(_reason("no_baseline_first_resumed" if ch is None
                                       else ch["invalid_reason"] or "after_other_ambiguity"))
                adopt()
            elif (ch["repo_key"], ch["name"]) != (repo_key, name):
                reasons.append(_reason("identity_mismatch"))
                abandon("after_other_ambiguity")
            elif ch["ident"] != ident:
                reasons.append(_reason("model_changed"))
                adopt()
            else:
                base = ch["baseline"]
                known = [f for f in USAGE_FIELDS if base[f] is not None and usage[f] is not None]
                if any(usage[f] < base[f] for f in known):
                    reasons.append(_reason("counter_reset"))
                    adopt()
                else:
                    delta = {f: (usage[f] - base[f] if f in known else None) for f in USAGE_FIELDS}
                    if _delta_incoherent(delta):
                        # Two valid snapshots can still differ into an impossible interval;
                        # show uncertainty and only reseed continuity from this snapshot.
                        reasons.append(_reason("delta_inconsistent"))
                        delta = None
                    else:
                        status = "eligible_estimate"
                    adopt()
        else:
            reasons.append(_reason("resume_flag_unknown"))
            abandon("after_other_ambiguity")

        row["usage_basis"] = "session_cumulative"
        if delta is not None:
            delta["total_tokens"] = None
            row["usage_delta"] = delta
        row["estimate"]["status"] = status
        return _finish(row)

    # Broken success: record why, and break continuity for the thread and scope.
    for code in problems:
        if code == "unsupported_cli_break" or code == "order_conflict_break":
            continue
        if code in ("usage_unavailable", "usage_invalid", "success_inconsistent",
                    "missing_thread_identity", "identity_unknown", "ordering_unverifiable"):
            reasons.append(_reason(code))
    first = problems[0]
    after = {"usage_unavailable": "after_usage_unavailable", "usage_invalid": "after_usage_unavailable",
             "unsupported_cli_break": "after_unsupported_cli"}.get(first, "after_other_ambiguity")
    scoped_barrier(after)
    if thread is not None:
        chains.by_thread[thread] = {
            "repo_key": repo_key, "name": name,
            "ident": (repo_key, name, rec["route"]["model"], rec["cli_version"]),
            "baseline": None, "invalid_reason": after, "seen": True, "last_raw": None,
            "last_call_id": rec["call_id"], "model": rec["route"]["model"],
            "calls": (chains.by_thread.get(thread) or {"calls": 0})["calls"] + 1}
    return _finish(row)


def _finish(row):
    reasons = row["estimate"]["reasons"]
    if _raw_usage_problem(row["usage_raw"], row["usage_invalid_fields"]) \
            and all(r["code"] != "usage_invalid" for r in reasons):
        reasons.append(_reason("usage_invalid"))  # also for failed/uncertain rows
    _set_ratios(row)
    return row


def _inflight_views(markers, recorded_ids, now):
    out = []
    for m in markers:
        anchor = parse_timestamp(m.get("since") or m.get("started_at"))
        elapsed = None
        if anchor is not None:
            seconds = (now - anchor).total_seconds()
            elapsed = int(seconds) if seconds >= 0 else None
        if m.get("state") == "running":
            kind = "running"
        elif m.get("state") == "uncertain":
            kind = "uncertain"
        else:
            kind = "unknown"
        recorded = m.get("call_id") in recorded_ids if m.get("call_id") else False
        view = dict(m)
        view.update({
            "kind": kind,
            "elapsed_s": elapsed,
            "recorded_in_telemetry": recorded,
            "possibly_stale": recorded or (elapsed is not None and elapsed > STALE_MARKER_S)
            or m.get("phase") == "turn_completed",
        })
        out.append(view)
    return out


def compute(state, now=None):
    """Account an obs_reader.read_state() result. Returns calls (stream order), sessions, inflight."""
    now = now or datetime.now(timezone.utc)
    chains = _Chains()
    scope_last = {}
    calls = []
    for ev in state["telemetry"]["events"]:
        kind = ev["kind"]
        if kind == "barrier":
            chains.invalidate(_barrier_reason(ev["reason"]), global_scope=True)
        elif kind == "conflict":
            calls.append(_conflict_row(ev))
        elif not ev["supported_schema"]:
            chains.invalidate("after_unsupported_schema", global_scope=True)
            calls.append(_unsupported_row(ev))
        else:
            calls.append(_account(ev, chains, scope_last))

    sessions = []
    for tid, ch in chains.by_thread.items():
        sessions.append({
            "thread_id": tid, "name": ch["name"], "repo_key": ch["repo_key"], "model": ch["model"],
            "calls_observed": ch["calls"], "last_call_id": ch["last_call_id"],
            "latest_cumulative": ch["last_raw"],
            "continuity_valid": ch["baseline"] is not None,
            "continuity_note": ch["invalid_reason"] and REASON_TEXT.get(ch["invalid_reason"]),
        })
    recorded = {c["call_id"] for c in calls if c["call_id"]}
    inflight = _inflight_views(state["inflight"]["markers"], recorded, now)
    return {"calls": calls, "sessions": sessions, "inflight": inflight}
