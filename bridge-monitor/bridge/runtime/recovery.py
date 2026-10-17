"""Coordinator-owned paid child recovery."""

import uuid

import psycopg
from psycopg.types.json import Jsonb

from bridge.runtime.admission import ARBITRATION_LOCK
from bridge.runtime.reconciliation import _authenticate, _reconcile_execution_in_tx
from contracts.policy import PolicySnapshot
from contracts.work import AdmissionDecision, CallRequest


def schedule_paid_recovery(dsn, child_call_id, recovery_call_id, *, actor_id, auth_token, reason,
                           actor_kind="system"):
    """Queue one paid replacement without replaying the terminated owner.

    A correlated termination reconciliation is mandatory. The replacement
    keeps the original child's task/work-item scope and addresses the original
    owner's caller. An uncertain owner never qualifies.
    """
    _authenticate(actor_kind, actor_id, auth_token)
    if not child_call_id or not recovery_call_id or not reason:
        raise ValueError("recovery identity and reason are required")
    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (ARBITRATION_LOCK,))
        return _schedule_paid_recovery_in_tx(conn, child_call_id, recovery_call_id,
                                             reason=reason)


def _schedule_paid_recovery_in_tx(conn, child_call_id, recovery_call_id, *, reason):
    child = conn.execute("SELECT j.task_id,j.work_item_id,j.request,j.source_snapshot,"
                         "j.source_mode,c.parent_call_id,j.prompt_content_id,j.state,"
                         "j.reserved_tokens FROM runtime_jobs j "
                         "JOIN calls c ON c.call_id=j.call_id "
                         "WHERE j.call_id=%s AND j.pool='qwen'", (child_call_id,)).fetchone()
    if not child or not child[5]:
        raise ValueError("managed child and owner call required")
    (task_id, work_id, child_request, source_snapshot, source_mode, owner_call,
     prompt_content_id, child_state, child_reserved) = child
    owner = conn.execute("SELECT state,slot_held,source_snapshot,source_mode FROM runtime_jobs WHERE call_id=%s "
                         "AND pool='claude'", (owner_call,)).fetchone()
    termination = conn.execute("SELECT 1 FROM runtime_reconciliations "
                               "WHERE call_id=%s AND observed_state='terminated' LIMIT 1",
                               (owner_call,)).fetchone()
    if not owner or owner[:2] != ("terminated", False) or not termination:
        raise ValueError("confirmed owner termination required")
    source_snapshot = source_snapshot or owner[2]
    source_mode = source_mode or owner[3]
    original_prompt = conn.execute("SELECT m.original_content_id FROM messages m "
        "JOIN content_objects c ON c.content_id=m.original_content_id "
        "WHERE m.call_id=%s AND m.role='user' "
        "ORDER BY c.created_at DESC,m.message_id DESC LIMIT 1", (child_call_id,)).fetchone()
    if original_prompt:
        prompt_content_id = original_prompt[0]
    recipient = conn.execute("SELECT parent_call_id FROM calls WHERE call_id=%s",
                             (owner_call,)).fetchone()
    if not recipient or not recipient[0]:
        raise ValueError("original owner's caller is unavailable")
    existing = conn.execute("SELECT recovery_call_id,recipient_call_id,reason "
                            "FROM runtime_recovery_links WHERE child_call_id=%s",
                            (child_call_id,)).fetchone()
    if existing:
        if existing != (recovery_call_id, recipient[0], reason):
            raise ValueError("recovery already scheduled for child")
        job = conn.execute("SELECT state,policy_version FROM runtime_jobs WHERE call_id=%s",
                           (recovery_call_id,)).fetchone()
        return AdmissionDecision(work_item_id=work_id, route="paid", state=job[0],
                                 policy_version=job[1])
    if conn.execute("SELECT 1 FROM runtime_jobs WHERE call_id=%s", (recovery_call_id,)).fetchone():
        raise ValueError("recovery call identity already used")
    policy_row = conn.execute("SELECT c.policy,a.version FROM runtime_active_policy a "
                              "JOIN config_versions c ON c.version=a.version "
                              "WHERE a.singleton=true").fetchone()
    if not policy_row:
        raise ValueError("reviewed policy has not been activated")
    policy = PolicySnapshot.model_validate(policy_row[0])
    version = policy_row[1]
    def reject(why):
        return AdmissionDecision(work_item_id=work_id, route="paid", state="rejected",
                                 reason=why, policy_version=version)
    if conn.execute("SELECT 1 FROM runtime_reconciliations WHERE call_id=%s "
                    "AND evidence->>'kind'='cancellation_requested' LIMIT 1",
                    (owner_call,)).fetchone():
        return reject("parent_cancelled")
    # The owner is gone: its local child must not dispatch or deliver to it,
    # even if paid recovery cannot be admitted yet.
    if child_state == "queued":
        conn.execute("UPDATE runtime_jobs SET state='cancelled' WHERE call_id=%s",
                     (child_call_id,))
        conn.execute("UPDATE runtime_task_budgets SET calls_reserved=calls_reserved-1, "
                     "tokens_reserved=tokens_reserved-%s WHERE task_id=%s",
                     (child_reserved, task_id))
    elif child_state in ("active", "uncertain"):
        conn.execute("UPDATE runtime_jobs SET state='fallback' WHERE call_id=%s",
                     (child_call_id,))
    waiting = conn.execute("SELECT count(*) FROM runtime_jobs WHERE pool='claude' "
                           "AND state='queued'").fetchone()[0]
    if waiting >= policy.limits.claude_waiting:
        return reject("queue_full")
    budget = conn.execute("SELECT call_budget-calls_reserved,token_budget-tokens_reserved "
                          "FROM runtime_task_budgets WHERE task_id=%s", (task_id,)).fetchone()
    reserved = policy.deployment.fallback_allowance_tokens
    if not budget or budget[0] < 1 or budget[1] < reserved:
        return reject("root_budget_exhausted")
    if not prompt_content_id:
        return reject("recovery_prompt_unavailable")
    prompt_ready = conn.execute("SELECT raw_bytes IS NOT NULL AND completeness<>'removed' "
        "FROM content_objects WHERE content_id=%s", (prompt_content_id,)).fetchone()
    if not prompt_ready or not prompt_ready[0]:
        return reject("recovery_prompt_unavailable")
    original = CallRequest.model_validate(child_request)
    replacement = CallRequest(call_id=recovery_call_id, task=original.task,
                              requested_model=policy.fallback.paid_model_profile,
                              parent_call_id=recipient[0])
    conn.execute("UPDATE runtime_task_budgets SET calls_reserved=calls_reserved+1, "
                 "tokens_reserved=tokens_reserved+%s WHERE task_id=%s", (reserved, task_id))
    conn.execute("INSERT INTO calls(call_id,work_item_id,parent_call_id,requested_model,created_at) "
                 "VALUES(%s,%s,%s,%s,now())", (recovery_call_id, work_id, recipient[0],
                                                replacement.requested_model))
    conn.execute("INSERT INTO runtime_jobs(call_id,task_id,work_item_id,pool,request,"
                 "requested_model,policy_version,state,queue_deadline_at,reserved_tokens,"
                 "source_snapshot,source_mode,prompt_content_id) VALUES(%s,%s,%s,'claude',%s,%s,%s,'queued',"
                 "now()+(%s * interval '1 second'),%s,%s,%s,%s)",
                 (recovery_call_id, task_id, work_id, Jsonb(replacement.model_dump()),
                  replacement.requested_model, version, policy.deployment.queue_deadline_seconds,
                  reserved, source_snapshot, source_mode, prompt_content_id))
    observed = conn.execute("SELECT m.model_execution_id FROM model_executions m "
                            "JOIN attempts a ON a.attempt_id=m.attempt_id "
                            "WHERE a.call_id=%s ORDER BY a.attempt_number DESC LIMIT 1",
                            (owner_call,)).fetchone()
    conn.execute("INSERT INTO runtime_recovery_links(recovery_call_id,child_call_id,"
                 "recipient_call_id,owner_execution_id,reason) VALUES(%s,%s,%s,%s,%s)",
                 (recovery_call_id, child_call_id, recipient[0], observed[0] if observed else None,
                  reason))
    return AdmissionDecision(work_item_id=work_id, route="paid", state="queued",
                             policy_version=version)


def reconcile_owner_and_recover(dsn, owner_call_id, evidence, *, actor_kind, actor_id,
                                auth_token):
    """Reconcile one Claude owner, then queue each orphaned child at most once.

    A missing or ambiguous termination receipt only records uncertainty. An
    explicit parent cancellation suppresses recovery even if its process later
    terminates. Queue/budget rejection is returned as a visible decision.
    """
    _authenticate(actor_kind, actor_id, auth_token)
    if not isinstance(evidence, dict):
        raise TypeError("structured evidence required")
    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (ARBITRATION_LOCK,))
        state, reconciliation_id = _reconcile_execution_in_tx(conn, owner_call_id, evidence,
            actor_kind=actor_kind, actor_id=actor_id)
        if state != "terminated":
            return {"state": state, "recoveries": []}
        cancelled = conn.execute(
            "SELECT 1 FROM runtime_reconciliations WHERE call_id=%s "
            "AND evidence->>'kind'='cancellation_requested' LIMIT 1",
            (owner_call_id,)).fetchone()
        children = conn.execute(
            "SELECT j.call_id FROM runtime_jobs j JOIN calls c ON c.call_id=j.call_id "
            "WHERE c.parent_call_id=%s AND j.pool='qwen' "
            "ORDER BY j.admitted_at,j.call_id", (owner_call_id,)).fetchall()
        recoveries = []
        if not cancelled:
            for (child_call_id,) in children:
                recovery_call_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                    f"bridge-monitor:paid-recovery:{child_call_id}"))
                decision = _schedule_paid_recovery_in_tx(conn, child_call_id, recovery_call_id,
                    reason="owner_terminated")
                recoveries.append({"child_call_id": child_call_id,
                                   "recovery_call_id": recovery_call_id,
                                   "state": decision.state, "reason": decision.reason})
        conn.execute("UPDATE runtime_reconciliations SET evidence=evidence || %s "
                     "WHERE reconciliation_id=%s",
                     (Jsonb({"recovery_outcomes": recoveries}), reconciliation_id))
        return {"state": state, "recoveries": recoveries}
