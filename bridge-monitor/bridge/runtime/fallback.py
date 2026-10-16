"""Child-call-keyed automatic paid continuation, selected atomically with routing."""

import uuid
from datetime import datetime
from typing import Literal

import psycopg
from psycopg.types.json import Jsonb

from contracts.work import FallbackTransition
from bridge.runtime.admission import ARBITRATION_LOCK


def commit_fallback(dsn: str, child_call_id: str, *,
                    timeout_stage: Literal["context", "queue", "predispatch", "execution", "cutoff"],
                    reason: str, threshold_at: datetime, elapsed_ms: int,
                    recipient_call_id: str, owner_execution_id: str | None,
                    selected_model: str | None,
                    confirmed_not_sent: bool = False) -> FallbackTransition:
    if threshold_at.tzinfo is None or elapsed_ms < 0 or not reason:
        raise ValueError("valid timed fallback evidence required")
    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (ARBITRATION_LOCK,))
        old = conn.execute("SELECT state,recipient_call_id,owner_execution_id,reason "
                           "FROM runtime_fallback_transitions WHERE child_call_id=%s",
                           (child_call_id,)).fetchone()
        if old:
            return FallbackTransition(child_call_id=child_call_id, state=old[0],
                recipient_call_id=old[1], owner_execution_id=old[2], reason=old[3])
        job = conn.execute("SELECT j.work_item_id,j.task_id,j.state,j.slot_held,j.policy_version,"
                           "j.reserved_tokens,j.request,c.parent_call_id,j.launch_claimed_at FROM runtime_jobs j "
                           "JOIN calls c ON c.call_id=j.call_id WHERE j.call_id=%s AND j.pool='qwen'",
                           (child_call_id,)).fetchone()
        if not job:
            raise ValueError("managed local child call required")
        (work_item_id, task_id, state, slot_held, policy_version, tokens,
         request, parent_call_id, launch_claimed_at) = job
        if parent_call_id != recipient_call_id:
            raise ValueError("fallback recipient must be the owning parent call")
        if state in ("completed", "terminated", "cancelled"):
            raise ValueError("local completion or cancellation already won")
        if timeout_stage == "queue" and state != "queued":
            raise ValueError("dispatch won queue-expiry race")
        if timeout_stage == "predispatch" and (state != "active" or
                                               (launch_claimed_at and not confirmed_not_sent)):
            raise ValueError("local launch already claimed")
        if timeout_stage in ("execution", "cutoff") and state not in ("active", "uncertain"):
            raise ValueError("local execution did not start")
        if timeout_stage == "context" and state != "queued":
            raise ValueError("context fallback must precede dispatch")
        parent = conn.execute("SELECT state,slot_held FROM runtime_jobs "
                              "WHERE call_id=%s AND pool='claude'", (recipient_call_id,)).fetchone()
        if not parent or parent[0] != "active" or not parent[1]:
            raise ValueError("healthy paid owner required for in-owner continuation")
        observed = conn.execute("SELECT m.reported_model FROM model_executions m "
                                "JOIN attempts a ON a.attempt_id=m.attempt_id "
                                "WHERE a.call_id=%s AND m.reported_model IS NOT NULL "
                                "ORDER BY a.attempt_number DESC LIMIT 1", (recipient_call_id,)).fetchone()
        if not selected_model or not observed or selected_model != observed[0]:
            raise ValueError("fallback model must match compatible observed owner")
        decision_id = str(uuid.uuid4())
        route_reason = (f"local_predispatch_fallback:{reason}" if timeout_stage == "predispatch"
                        else f"local_{timeout_stage}_timeout:{reason}")
        conn.execute("INSERT INTO delegation_decisions(decision_id,work_item_id,child_call_id,"
                     "route,eligibility,reason,policy_version,guidance_manifest,gate_facts,decided_at) "
                     "VALUES(%s,%s,%s,'paid','fallback',%s,%s,%s,%s,now())",
                     (decision_id, work_item_id, child_call_id, route_reason, policy_version,
                      Jsonb({"state": "no_guidance", "version": 1}),
                      Jsonb({"timeout_stage": timeout_stage, "threshold_at": threshold_at.isoformat(),
                             "elapsed_ms": elapsed_ms, "owner_model": selected_model,
                             "pre_dispatch": state == "queued" or timeout_stage == "predispatch",
                             "confirmed_not_sent": confirmed_not_sent})))
        conn.execute("INSERT INTO runtime_fallback_transitions(child_call_id,work_item_id,"
                     "state,timeout_stage,reason,threshold_at,elapsed_ms,policy_version,"
                     "owner_execution_id,recipient_call_id,selected_model,decision_id) "
                     "VALUES(%s,%s,'committed',%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                     (child_call_id, work_item_id, timeout_stage, reason, threshold_at,
                      elapsed_ms, policy_version, owner_execution_id,
                      recipient_call_id, selected_model, decision_id))
        conn.execute("UPDATE runtime_jobs SET state='fallback',slot_held=%s WHERE call_id=%s",
                     (slot_held if timeout_stage != "predispatch" else False, child_call_id))
        if state == "queued" or timeout_stage == "predispatch":
            conn.execute("UPDATE runtime_task_budgets SET calls_reserved=calls_reserved-1, "
                         "tokens_reserved=tokens_reserved-%s WHERE task_id=%s", (tokens, task_id))
        return FallbackTransition(child_call_id=child_call_id, state="committed",
            recipient_call_id=recipient_call_id, owner_execution_id=owner_execution_id,
            reason=reason)
