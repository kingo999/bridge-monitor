"""Durable call admission. The database is the authority for budgets and queues."""

from datetime import timedelta
from typing import Literal

import psycopg
from psycopg.types.json import Jsonb

from contracts.policy import PolicyInput, PolicySnapshot
from contracts.work import AdmissionDecision, CallRequest


ARBITRATION_LOCK = 842116207


def _pin_matches(row, request: CallRequest) -> bool:
    scope, call_budget, token_budget = row
    return (scope == request.task.scope and call_budget == request.task.call_budget
            and token_budget == request.task.token_budget)


def admit_call(dsn: str, request: CallRequest, policy: PolicyInput | PolicySnapshot,
               *, policy_version: str, pool: Literal["claude", "qwen"],
               reserve_tokens: int, local_cutoff_at=None, source_snapshot: str | None = None,
               source_mode: Literal["read", "write"] | None = None,
               prompt_content_id: str | None = None) -> AdmissionDecision:
    if reserve_tokens < 0:
        raise ValueError("reserve_tokens must be nonnegative")
    if source_mode and not source_snapshot:
        raise ValueError("source access requires an immutable snapshot reference")
    task = request.task
    route = "paid" if pool == "claude" else "local"

    def answer(state: str, reason: str | None = None) -> AdmissionDecision:
        return AdmissionDecision(work_item_id=task.work_item_id, route=route, state=state,
                                 reason=reason, policy_version=policy_version)

    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (ARBITRATION_LOCK,))
        pin = conn.execute("SELECT scope, call_budget, token_budget FROM runtime_task_budgets WHERE task_id=%s",
                           (task.task_id,)).fetchone()
        if pin and not _pin_matches(pin, request):
            return answer("rejected", "task_pin_conflict")
        existing = conn.execute("SELECT request, pool, reserved_tokens, state, policy_version,prompt_content_id "
                                "FROM runtime_jobs WHERE call_id=%s",
                                (request.call_id,)).fetchone()
        if existing:
            body, existing_pool, tokens, state, version, stored_prompt = existing
            if (body != request.model_dump() or existing_pool != pool or tokens != reserve_tokens
                    or stored_prompt != prompt_content_id):
                return answer("rejected", "call_conflict")
            return AdmissionDecision(work_item_id=task.work_item_id, route=route,
                                     state=state, policy_version=version)
        if not pin:
            conn.execute("INSERT INTO runtime_task_budgets(task_id,scope,call_budget,token_budget) VALUES(%s,%s,%s,%s)",
                         (task.task_id, Jsonb(task.scope), task.call_budget, task.token_budget))
        waiting_limit = policy.limits.claude_waiting if pool == "claude" else policy.limits.qwen_waiting
        waiting = conn.execute("SELECT count(*) FROM runtime_jobs WHERE pool=%s AND state='queued'",
                               (pool,)).fetchone()[0]
        if waiting >= waiting_limit:
            return answer("rejected", "queue_full")
        remaining = conn.execute("SELECT call_budget-calls_reserved, token_budget-tokens_reserved "
                                 "FROM runtime_task_budgets WHERE task_id=%s", (task.task_id,)).fetchone()
        if remaining[0] < 1 or remaining[1] < reserve_tokens:
            return answer("rejected", "root_budget_exhausted")
        if pool == "qwen" and local_cutoff_at is None:
            # The scheduler never invents a local cutoff: the owner must provide one.
            return answer("rejected", "local_cutoff_required")
        conn.execute("UPDATE runtime_task_budgets SET calls_reserved=calls_reserved+1, "
                     "tokens_reserved=tokens_reserved+%s WHERE task_id=%s", (reserve_tokens, task.task_id))
        conn.execute("INSERT INTO runtime_jobs(call_id,task_id,work_item_id,pool,request,requested_model,"
                     "policy_version,state,queue_deadline_at,local_cutoff_at,reserved_tokens,source_snapshot,source_mode,"
                     "prompt_content_id) "
                     "VALUES(%s,%s,%s,%s,%s,%s,%s,'queued',now()+(%s * interval '1 second'),%s,%s,%s,%s,%s)",
                     (request.call_id, task.task_id, task.work_item_id, pool, Jsonb(request.model_dump()),
                      request.requested_model, policy_version, policy.deployment.queue_deadline_seconds,
                      local_cutoff_at, reserve_tokens, source_snapshot, source_mode, prompt_content_id))
        return answer("queued")
