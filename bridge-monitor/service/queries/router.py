"""Separate, operator-authenticated parent-router audit and shadow cohort."""

from collections import Counter
from datetime import datetime
from typing import Literal

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict


class RouterCohortFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    origin: Literal["observed", "imported", "synthetic", "all"] = "observed"
    created_from: datetime | None = None
    created_until: datetime | None = None
    model_profile: str | None = None
    prompt_version: str | None = None
    verifier_ref: str | None = None
    work_item_id: str | None = None


def _latest_evaluation(conn, result_id: str | None) -> dict | None:
    if not result_id:
        return None
    row = conn.execute("SELECT evaluation_id,evidence,recorded_at FROM evaluations "
        "WHERE result_id=%s ORDER BY recorded_at DESC,evaluation_id DESC LIMIT 1",
        (result_id,)).fetchone()
    return dict(row["evidence"], evaluation_id=row["evaluation_id"],
                recorded_at=row["recorded_at"]) if row else None


def read_router_audit(dsn: str, work_item_id: str) -> dict | None:
    """Return references and evidence, never prompt/response bodies in this metadata view."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        work = conn.execute("SELECT work_item_id,task_id,category,origin,created_at "
            "FROM work_items WHERE work_item_id=%s", (work_item_id,)).fetchone()
        if not work:
            return None
        decisions = conn.execute("SELECT decision_id,decision_stage,execution_mode,"
            "child_call_id,route,eligibility,reason,policy_version,gate_facts,"
            "decided_at FROM delegation_decisions WHERE work_item_id=%s "
            "AND decision_stage='parent_router' ORDER BY decided_at,decision_id",
            (work_item_id,)).fetchall()
        parent = next((dict(row) for row in decisions
                       if row["execution_mode"] == "production"), None)
        shadow = next((dict(row) for row in decisions
                       if row["execution_mode"] == "shadow" and row["route"] == "local"), None)
        paid_call = parent["child_call_id"] if parent else None
        shadow_call = shadow["child_call_id"] if shadow else None
        jobs = conn.execute("SELECT call_id,pool,execution_mode,state,requested_model,"
            "policy_version,admitted_at,launch_claimed_at,execution_deadline_at,slot_held "
            "FROM runtime_jobs WHERE work_item_id=%s ORDER BY admitted_at,call_id",
            (work_item_id,)).fetchall()
        paid_result = conn.execute("SELECT result_id,raw_content_id,delivered_content_id,"
            "delivered_at FROM results WHERE call_id=%s ORDER BY result_id LIMIT 1",
            (paid_call,)).fetchone() if paid_call else None
        shadow_result = conn.execute("SELECT result_id,raw_content_id,delivered_content_id,"
            "delivered_at FROM results WHERE call_id=%s ORDER BY result_id LIMIT 1",
            (shadow_call,)).fetchone() if shadow_call else None
        parts = conn.execute("SELECT part_id,content_id FROM result_parts WHERE result_id=%s "
            "ORDER BY part_id", (shadow_result["result_id"],)).fetchall() if shadow_result else []
        prompts = conn.execute("SELECT call_id,original_content_id,effective_content_id "
            "FROM messages WHERE call_id=ANY(%s) AND role='user' "
            "ORDER BY message_id", ([call for call in (paid_call, shadow_call) if call],)).fetchall()
        paid_prompt = next((row for row in prompts if row["call_id"] == paid_call), None)
        shadow_prompt = next((row for row in prompts if row["call_id"] == shadow_call), None)
        dispositions = conn.execute("SELECT d.disposition_id,d.stage,d.judgment,d.reason,"
            "d.recorded_at,d.supersedes_id FROM dispositions d JOIN results r "
            "ON r.result_id=d.result_id JOIN calls c ON c.call_id=r.call_id "
            "WHERE c.work_item_id=%s ORDER BY d.recorded_at,d.disposition_id",
            (work_item_id,)).fetchall()
        evaluation = _latest_evaluation(conn, shadow_result["result_id"] if shadow_result else None)
        evaluation_history = conn.execute("SELECT evaluation_id,evidence,recorded_at "
            "FROM evaluations WHERE result_id=%s ORDER BY recorded_at,evaluation_id",
            (shadow_result["result_id"],)).fetchall() if shadow_result else []
        usage = conn.execute("SELECT a.call_id,u.scope,u.input_tokens,u.output_tokens,"
            "u.provenance,u.price_basis FROM usage_observations u "
            "JOIN model_executions m ON m.model_execution_id=u.model_execution_id "
            "JOIN attempts a ON a.attempt_id=m.attempt_id "
            "WHERE a.call_id=ANY(%s) ORDER BY a.call_id,u.observation_id",
            ([call for call in (paid_call, shadow_call) if call],)).fetchall()
        events = conn.execute("SELECT event_type,call_id,occurred_at,payload "
            "FROM events WHERE work_item_id=%s AND event_type IN "
            "('call.queued','call.dispatched','call.completed','attempt.uncertain',"
            "'admission.blocked','delegation.decided') "
            "ORDER BY occurred_at,event_id", (work_item_id,)).fetchall()
    return {"work_item": dict(work), "parent_decision": parent,
            "shadow_decision": shadow, "decisions": list(map(dict, decisions)),
            "paid_call_id": paid_call, "shadow_call_id": shadow_call,
            "jobs": list(map(dict, jobs)),
            "paid_result": dict(paid_result) if paid_result else None,
            "shadow_result": dict(shadow_result) if shadow_result else None,
            "shadow_parts": list(map(dict, parts)), "evaluation": evaluation,
            "evaluation_history": list(map(dict, evaluation_history)),
            "prompt_refs": {"original": paid_prompt["original_content_id"] if paid_prompt else None,
                            "paid_effective": paid_prompt["effective_content_id"] if paid_prompt else None,
                            "shadow_effective": shadow_prompt["effective_content_id"] if shadow_prompt else None},
            "result_refs": {"paid": paid_result["delivered_content_id"] if paid_result else None,
                            "shadow_raw": shadow_result["raw_content_id"] if shadow_result else None,
                            "shadow_parsed": parts[0]["content_id"] if parts else None},
            "dispositions": list(map(dict, dispositions)),
            "usage": list(map(dict, usage)), "timeline": list(map(dict, events))}


def query_shadow_cohort(dsn: str, filters: RouterCohortFilter) -> dict:
    """Attempt-based denominator; missing verifier/usage/charges stay unknown."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        parents = conn.execute("SELECT d.work_item_id,d.child_call_id,d.reason,d.gate_facts,"
            "d.decided_at,w.origin,w.created_at FROM delegation_decisions d "
            "JOIN work_items w ON w.work_item_id=d.work_item_id "
            "WHERE d.decision_stage='parent_router' AND d.execution_mode='production' "
            "AND (%s='all' OR w.origin=%s) "
            "AND (%s::timestamptz IS NULL OR w.created_at>=%s) "
            "AND (%s::timestamptz IS NULL OR w.created_at<%s) "
            "AND (%s::text IS NULL OR d.gate_facts->>'model_profile'=%s) "
            "AND (%s::text IS NULL OR d.gate_facts->>'prompt_version'=%s) "
            "AND (%s::text IS NULL OR d.gate_facts->>'verifier_ref'=%s) "
            "AND (%s::text IS NULL OR d.work_item_id=%s) "
            "ORDER BY d.decided_at,d.decision_id",
            (filters.origin, filters.origin, filters.created_from, filters.created_from,
             filters.created_until, filters.created_until, filters.model_profile,
             filters.model_profile, filters.prompt_version, filters.prompt_version,
             filters.verifier_ref, filters.verifier_ref, filters.work_item_id,
             filters.work_item_id)).fetchall()
        # One parent decision per work item is enforced by router ingress identity.
        parents = list({row["work_item_id"]: row for row in parents}.values())
        work_ids = [row["work_item_id"] for row in parents]
        shadows = conn.execute("SELECT d.work_item_id,d.child_call_id,d.gate_facts "
            "FROM delegation_decisions d WHERE d.work_item_id=ANY(%s) "
            "AND d.decision_stage='parent_router' AND d.execution_mode='shadow' "
            "AND d.route='local'", (work_ids,)).fetchall()
        shadow_by_work = {row["work_item_id"]: row for row in shadows}
        call_ids = [row["child_call_id"] for row in shadows if row["child_call_id"]]
        jobs = conn.execute("SELECT call_id,state,admitted_at,launch_claimed_at "
            "FROM runtime_jobs WHERE call_id=ANY(%s)", (call_ids,)).fetchall()
        job_by_call = {row["call_id"]: row for row in jobs}
        results = conn.execute("SELECT call_id,result_id,delivered_at "
            "FROM results WHERE call_id=ANY(%s)", (call_ids,)).fetchall()
        result_by_call = {row["call_id"]: row for row in results}
        result_ids = [row["result_id"] for row in results]
        evaluations = conn.execute("SELECT result_id,evaluation_id,evidence,recorded_at "
            "FROM evaluations WHERE result_id=ANY(%s) "
            "ORDER BY recorded_at,evaluation_id", (result_ids,)).fetchall()
        eval_by_result = {row["result_id"]: row for row in evaluations}
        usage = conn.execute("SELECT a.call_id,u.input_tokens,u.output_tokens,u.provenance,"
            "u.price_basis FROM usage_observations u "
            "JOIN model_executions m ON m.model_execution_id=u.model_execution_id "
            "JOIN attempts a ON a.attempt_id=m.attempt_id "
            "WHERE a.call_id=ANY(%s)", (call_ids + [r["child_call_id"] for r in parents],)).fetchall()
        uncertain_events = conn.execute("SELECT DISTINCT ON (call_id) call_id,payload "
            "FROM events WHERE call_id=ANY(%s) AND event_type IN "
            "('attempt.uncertain','admission.blocked') "
            "ORDER BY call_id,occurred_at DESC,event_id DESC", (call_ids,)).fetchall()
    uncertain_by_call = {row["call_id"]: row["payload"].get("reason") for row in uncertain_events}
    reasons = Counter()
    outcomes = Counter()
    models = Counter()
    prompt_versions = Counter()
    verifier_refs = Counter()
    state_counts = Counter()
    evaluated = 0
    result_count = 0
    latencies = []
    for parent in parents:
        facts = parent["gate_facts"]
        models[facts.get("model_profile") or "unknown"] += 1
        prompt_versions[facts.get("prompt_version") or "unknown"] += 1
        verifier_refs[facts.get("verifier_ref") or "missing"] += 1
        shadow = shadow_by_work.get(parent["work_item_id"])
        if not shadow:
            reasons[parent["reason"] or "unclassified"] += 1
            state_counts["not_queued"] += 1
            continue
        call_id = shadow["child_call_id"]
        job = job_by_call.get(call_id)
        state = job["state"] if job else "missing_job"
        state_counts[state] += 1
        result = result_by_call.get(call_id)
        if result:
            result_count += 1
            evaluation = eval_by_result.get(result["result_id"])
            if evaluation:
                evaluated += 1
                evidence = evaluation["evidence"]
                outcome = evidence.get("state") or "unknown"
                outcomes[outcome] += 1
                reasons[evidence.get("reason") or "unclassified"] += 1
            else:
                outcomes["unknown"] += 1
                reasons["evaluation_missing"] += 1
        else:
            outcomes["unknown"] += 1
            reasons[uncertain_by_call.get(call_id) or state] += 1
        if job and job["launch_claimed_at"]:
            latencies.append(max(0, int((job["launch_claimed_at"] -
                                         job["admitted_at"]).total_seconds() * 1000)))
    usage_by_call = {row["call_id"] for row in usage if row["input_tokens"] is not None
                     and row["output_tokens"] is not None}
    paid_ids = {row["child_call_id"] for row in parents}
    shadow_ids = set(call_ids)
    return {"definition_version": "parent-router-shadow-v1",
            "attempted_tasks": len(parents), "queued_shadow": len(shadow_by_work),
            "result_captured": result_count, "independent_evaluated": evaluated,
            "states": dict(state_counts), "outcomes": dict(outcomes),
            "reasons": dict(reasons), "model_profiles": dict(models),
            "prompt_versions": dict(prompt_versions), "verifier_refs": dict(verifier_refs),
            "queue_latency_ms": {"samples": len(latencies),
                                 "median": sorted(latencies)[len(latencies)//2] if latencies else None},
            "usage_coverage": {"qwen": len(shadow_ids & usage_by_call),
                               "paid": len(paid_ids & usage_by_call)},
            "unknown_usage": {"qwen": len(shadow_ids - usage_by_call),
                              "paid": len(paid_ids - usage_by_call)},
            "provider_usage": list(map(dict, usage)),
            "conditional_avoided_paid_calls": None,
            "conditional_savings_reason": "no_comparable_paid_same_contract_reference",
            "billed_savings": None, "billed_savings_reason": "billing_not_observed"}
