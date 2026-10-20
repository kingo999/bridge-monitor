"""Separate, operator-authenticated Gemma classifier shadow cohort.

Reads only ``classifier_*`` tables. It is never combined with production routing totals
or the Qwen coding-quality cohort, and recommendations are never presented as completed
work or saved paid requests.
"""

import math
from collections import Counter
from datetime import datetime
from typing import Literal

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict

DEFINITION_VERSION = "classifier-shadow-cohort-v1"
ROW_LIMIT = 5000
TERMINAL_STATES = ("completed", "failed", "skipped", "uncertain", "cancelled")


class ClassifierCohortFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    origin_kind: Literal["natural", "controlled", "synthetic", "replay", "all"] = "all"
    capture_method: Literal['all', 'manual', 'route_work', 'bridge_ingress'] = 'all'
    model: str | None = None
    profile_id: str | None = None
    rubric_version: str | None = None
    created_from: datetime | None = None
    created_until: datetime | None = None


def _distribution(values: list[int | float]) -> dict:
    ordered = sorted(v for v in values if v is not None)
    if not ordered:
        return {"samples": 0, "median": None, "p95": None, "max": None}
    position = (len(ordered) - 1) * 0.95
    low, high = math.floor(position), math.ceil(position)
    p95 = ordered[low] + (ordered[high] - ordered[low]) * (position - low)
    middle = ordered[len(ordered) // 2] if len(ordered) % 2 else (
        ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2
    return {"samples": len(ordered), "median": middle, "p95": round(p95, 1), "max": ordered[-1]}


def _available(conn) -> bool:
    return conn.execute("SELECT to_regclass('classifier_observations') IS NOT NULL "
                        "AND to_regclass('classifier_evaluations') IS NOT NULL AS ok").fetchone()["ok"]


def query_classifier_cohort(dsn: str, filters: ClassifierCohortFilter) -> dict:
    with psycopg.connect(dsn, row_factory=dict_row) as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        if not _available(conn):
            return {"definition_version": DEFINITION_VERSION, "available": False,
                    "reason": "schema_not_applied"}
        rows = conn.execute("""SELECT observation_id,work_item_id,origin_kind,requested_model,profile_id,
            rubric_version,state,skip_reason,error_type,validation_error,attempts,rec_route,
            rec_category,policy_allowed,policy_blocks,actual_route,queue_ms,request_ms,
            first_content_ms,inference_ms,load_ms,end_to_end_ms,input_tokens,output_tokens,
            usage_provenance,backend,captured_at,facts,
            CASE WHEN facts_provenance->>'capture_method' IN ('route_work','bridge_ingress') THEN facts_provenance->>'capture_method'
                 ELSE 'manual' END AS capture_method
            FROM classifier_observations
            WHERE (%s='all' OR origin_kind=%s)
              AND (%s='all' OR CASE WHEN facts_provenance->>'capture_method' IN ('route_work','bridge_ingress')
                   THEN facts_provenance->>'capture_method' ELSE 'manual' END=%s)
              AND (%s::text IS NULL OR requested_model=%s)
              AND (%s::text IS NULL OR profile_id=%s)
              AND (%s::text IS NULL OR rubric_version=%s)
              AND (%s::timestamptz IS NULL OR captured_at>=%s)
              AND (%s::timestamptz IS NULL OR captured_at<%s)
            ORDER BY captured_at DESC LIMIT %s""",
            (filters.origin_kind, filters.origin_kind, filters.capture_method, filters.capture_method,
             filters.model, filters.model,
             filters.profile_id, filters.profile_id, filters.rubric_version,
             filters.rubric_version, filters.created_from, filters.created_from,
             filters.created_until, filters.created_until, ROW_LIMIT)).fetchall()
        ids = [r["observation_id"] for r in rows]
        evaluations = conn.execute("""SELECT observation_id,kind,reference_route,
            acceptable_routes,judgment,recorded_at,evaluation_id FROM classifier_evaluations
            WHERE observation_id=ANY(%s) ORDER BY recorded_at,evaluation_id""", (ids,)).fetchall()
        work_ids = sorted({r["work_item_id"] for r in rows if r.get("work_item_id")})
        shadow_eval = conn.execute("""SELECT DISTINCT ON (d.work_item_id) d.work_item_id,
            e.evidence->>'state' AS state FROM delegation_decisions d
            JOIN results r ON r.call_id=d.child_call_id JOIN evaluations e ON e.result_id=r.result_id
            WHERE d.work_item_id=ANY(%s) AND d.decision_stage='parent_router'
            AND d.execution_mode='shadow' AND d.route='local'
            ORDER BY d.work_item_id,e.recorded_at DESC,e.evaluation_id DESC""", (work_ids,)).fetchall()
        paid_dispositions = conn.execute("""SELECT DISTINCT ON (c.work_item_id) c.work_item_id,
            x.judgment FROM dispositions x JOIN results r ON r.result_id=x.result_id
            JOIN calls c ON c.call_id=r.call_id WHERE c.work_item_id=ANY(%s)
            AND c.execution_mode='production' ORDER BY c.work_item_id,x.recorded_at DESC,
            x.disposition_id DESC""", (work_ids,)).fetchall()
        facets = conn.execute("""SELECT array_agg(DISTINCT requested_model) AS models,
            array_agg(DISTINCT profile_id) AS profiles, array_agg(DISTINCT rubric_version) AS rubrics,
            array_agg(DISTINCT origin_kind) AS origins FROM classifier_observations""").fetchone()
    states = Counter(r["state"] for r in rows)
    attempted = [r for r in rows if r["attempts"] >= 1]
    completed = [r for r in rows if r["state"] == "completed"]
    latest_reference: dict[str, dict] = {}
    follow_up: dict[str, set] = {}
    for ev in evaluations:
        if ev["kind"] == "reference_label":
            latest_reference[ev["observation_id"]] = ev
        follow_up.setdefault(ev["observation_id"], set()).add(ev["kind"])
    agree = preferred = allowed_agree = labelled = 0
    for r in completed:
        ref = latest_reference.get(r["observation_id"])
        if not ref:
            continue
        labelled += 1
        acceptable = ref["acceptable_routes"] or ([ref["reference_route"]] if ref["reference_route"] else [])
        if r["rec_route"] in acceptable:
            agree += 1
            allowed_agree += bool(r["policy_allowed"])
        preferred += r["rec_route"] == ref["reference_route"]
    completed_work = {r["work_item_id"] for r in completed if r.get("work_item_id")}
    shadow_states = Counter(e["state"] or "unknown" for e in shadow_eval
                            if e["work_item_id"] in completed_work)
    disposition_judgments = Counter(d["judgment"] for d in paid_dispositions
                                    if d["work_item_id"] in completed_work)
    known_actual = [r for r in completed if r["actual_route"] not in (None, "unknown")]
    block_reasons = Counter(b for r in completed for b in (r["policy_blocks"] or []))
    covered = Counter(r["usage_provenance"] or "missing" for r in attempted)
    resident = Counter((r["backend"] or {}).get("state", "unrecorded") for r in attempted)
    verification_kinds = {"verification", "acceptance", "correction"}
    method_work = {method: {r['work_item_id'] for r in rows
                           if r['capture_method'] == method and r['work_item_id']}
                   for method in ('manual', 'route_work', 'bridge_ingress')}
    fact_coverage = {key: {label: sum((r['facts'].get(key) is value) for r in rows)
                           for label, value in (('true', True), ('false', False), ('unknown', None))}
                     for key in ('general_available', 'code_available', 'paid_allowed',
                                 'tools_available', 'context_complete', 'tests_present')}
    return {
        "definition_version": DEFINITION_VERSION, "available": True,
        "scope_note": ("Advisory recommendations only. Not production routing, not Qwen "
                       "dispatches, not completed coding tasks and not paid requests saved."),
        "rows_considered": len(rows), "row_limit": ROW_LIMIT,
        'capture': {'observations': {m: sum(r['capture_method'] == m for r in rows) for m in method_work},
                    'unique_work_items': len(set.union(*method_work.values())),
                    'unique_work_items_by_method': {m: len(ids) for m, ids in method_work.items()},
                    'overlap_work_items': sum(n > 1 for n in Counter(w for ids in method_work.values() for w in ids).values()),
                    'note': 'Counts cover this bounded query window; dropped in-memory offers are not recorded.'},
        'fact_coverage': fact_coverage,
        "counts": {"submitted": len(rows), "attempted": len(attempted),
                   "completed": states["completed"], "failed": states["failed"],
                   "skipped": states["skipped"], "uncertain": states["uncertain"],
                   "cancelled": states["cancelled"],
                   "pending": states["captured"] + states["dispatching"],
                   "unknown_outcome": states["uncertain"] + states["dispatching"]},
        "skip_reasons": dict(Counter(r["skip_reason"] for r in rows if r["state"] == "skipped")),
        "error_types": dict(Counter(r["error_type"] for r in rows if r["error_type"])),
        "contract_errors": dict(Counter(r["validation_error"] for r in rows if r["validation_error"])),
        "recommendations": {"routes": dict(Counter(r["rec_route"] for r in completed)),
                            "categories": dict(Counter(r["rec_category"] for r in completed))},
        "policy": {"allowed": sum(r["policy_allowed"] is True for r in completed),
                   "blocked": sum(r["policy_allowed"] is False for r in completed),
                   "block_reasons": dict(block_reasons),
                   'unknown_code_capacity_blocks': sum('code_worker_unknown' in (r['policy_blocks'] or []) for r in completed),
                   'other_blocked': sum(any(b != 'code_worker_unknown' for b in (r['policy_blocks'] or [])) for r in completed)},
        "reference": {"labelled": labelled, "unlabelled": len(completed) - labelled,
                      "raw_route_acceptable": agree, "raw_route_preferred": preferred,
                      "allowed_and_acceptable": allowed_agree},
        "actual_route": {"known": len(known_actual),
                         "unknown": len(completed) - len(known_actual),
                         "differs_from_recommendation": sum(r["rec_route"] != r["actual_route"]
                                                            for r in known_actual),
                         "note": "A difference is not an error: the actual route is unchanged by design."},
        "follow_up": {"with_verification_or_correction": sum(
                          bool(follow_up.get(r["observation_id"], set()) & verification_kinds)
                          for r in completed),
                      "with_operator_judgment": sum("operator_judgment" in
                          follow_up.get(r["observation_id"], set()) for r in completed),
                      "completed": len(completed)},
        "router_evidence": {
            "note": ("Context from the existing router for the same work item. It is not a judgment "
                     "of whether the recommendation was right."),
            "completed_with_work_item": len(completed_work),
            "qwen_shadow_independent_evaluation": dict(shadow_states),
            "paid_result_dispositions": dict(disposition_judgments)},
        "latency_ms": {"queue": _distribution([r["queue_ms"] for r in attempted]),
                       "request": _distribution([r["request_ms"] for r in completed]),
                       "first_content": _distribution([r["first_content_ms"] for r in completed]),
                       "inference_reported": _distribution([r["inference_ms"] for r in completed]),
                       "load_reported": _distribution([r["load_ms"] for r in attempted]),
                       "end_to_end": _distribution([r["end_to_end_ms"] for r in completed])},
        "tokens": {"input_total": sum(r["input_tokens"] or 0 for r in attempted),
                   "output_total": sum(r["output_tokens"] or 0 for r in attempted),
                   "provenance": dict(covered), "attempted": len(attempted)},
        "backend_residency": dict(resident),
        "cost": {"local_provider_charge_usd": 0 if attempted else None,
                 "local_provider_charge_basis": "local inference; no provider invoice",
                 "electricity_usd": None, "electricity_reason": "not_measured",
                 "hypothetical_avoided_paid_calls": None,
                 "billed_savings": None, "billed_savings_reason": "recommendations_do_not_avoid_calls"},
        "filters": {"models": sorted(facets["models"] or []), "profiles": sorted(facets["profiles"] or []),
                    "rubrics": sorted(facets["rubrics"] or []),
                    "origins": sorted(facets["origins"] or [])}}


def read_classifier_observation(dsn: str, observation_id: str) -> dict | None:
    """Metadata and content references only; bodies stay behind the content endpoint."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        if not _available(conn):
            return None
        row = conn.execute("SELECT * FROM classifier_observations WHERE observation_id=%s",
                           (observation_id,)).fetchone()
        if not row:
            return None
        evaluations = conn.execute("SELECT * FROM classifier_evaluations WHERE observation_id=%s "
                                   "ORDER BY recorded_at,evaluation_id", (observation_id,)).fetchall()
    row.pop("source_key", None)
    return {"observation": row, "evaluations": evaluations,
            "content_refs": {"request": row["request_content_id"],
                             "source": row["source_content_id"],
                             "response": row["response_content_id"]}}
