"""Parent-router identity, bounded facts and decision publication."""

import hashlib
import uuid
from datetime import datetime, timezone

import psycopg

from contracts.events import EventType
from contracts.router import CaptureFacts, RouterCase, RouterDecision
from contracts.work import TaskPacket


def router_call_ids(task: TaskPacket) -> tuple[str, str, str]:
    paid = str(uuid.uuid5(uuid.NAMESPACE_URL,
        f"bridge-monitor:parent-router:paid:{task.task_id}:{task.work_item_id}"))
    shadow = str(uuid.uuid5(uuid.NAMESPACE_URL,
        f"bridge-monitor:parent-router:shadow:{paid}"))
    decision = str(uuid.uuid5(uuid.NAMESPACE_URL,
        f"bridge-monitor:parent-router:decision:{paid}"))
    return paid, shadow, decision


def collect_capture_facts(dsn: str, task: TaskPacket, *, reserve_tokens: int,
                          case: RouterCase | None, case_digest_valid: bool,
                          policy, local_capability: dict) -> CaptureFacts:
    with psycopg.connect(dsn) as conn:
        budget = conn.execute("SELECT calls_reserved,tokens_reserved FROM runtime_task_budgets "
                              "WHERE task_id=%s", (task.task_id,)).fetchone()
        waiting = conn.execute("SELECT count(*) FROM runtime_jobs WHERE pool='qwen' "
                               "AND state='queued'").fetchone()[0]
    reserved_calls, reserved_tokens = budget if budget else (0, 0)
    router = policy.router
    return CaptureFacts(case=case, case_digest_valid=case_digest_valid,
        baseline_failed=True if case_digest_valid else None,
        capture_healthy=True,
        # Live agent/profile attestation is checked immediately before shadow launch.
        agent_ready=bool(policy.deployment.qwen_agent_endpoint and
                         policy.deployment.qwen_model_profile and
                         policy.deployment.qwen_model_digest),
        model_profile=policy.deployment.qwen_model_profile or None,
        model_digest=policy.deployment.qwen_model_digest or None,
        remaining_calls=task.call_budget - reserved_calls,
        remaining_tokens=task.token_budget - reserved_tokens - reserve_tokens,
        context_tokens=local_capability.get("context_tokens"),
        queue_slots=policy.limits.qwen_waiting - waiting,
        verifier_active=bool(router.verifier))


def publish_router_decision(emitter, task: TaskPacket, *, call_id: str,
                            decision_id: str, decision: RouterDecision,
                            policy_version: str, mode: str, route: str,
                            reason: str | None = None) -> bool:
    return emitter.emit(EventType.DELEGATION_DECIDED,
        call_id=call_id, task_id=task.task_id, work_item_id=task.work_item_id,
        config_version=policy_version,
        payload={"decision_id": decision_id, "decision_stage": "parent_router",
                 "execution_mode": mode, "route": route,
                 "eligibility": ("eligible" if decision.eligible else "ineligible")
                    if route != "pending" else "blocked",
                 "reason": reason or decision.reason,
                 "guidance_manifest": {"state": "not_selected"},
                 "gate_facts": {**decision.manifest,
                                "agent_preflight": "deferred_to_shadow_worker",
                                "decided_at_utc": datetime.now(timezone.utc).isoformat()}})


def existing_parent_decision(dsn: str, work_item_id: str):
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT decision_id,child_call_id,gate_facts->>'task_sha256' "
            "FROM delegation_decisions WHERE work_item_id=%s "
            "AND decision_stage='parent_router' AND execution_mode='production' "
            "ORDER BY decided_at LIMIT 1", (work_item_id,)).fetchone()


def prompt_sha256(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def existing_local_delegation(dsn: str, paid_call_id: str, qwen_model: str) -> bool:
    with psycopg.connect(dsn) as conn:
        return bool(conn.execute("SELECT "
            "EXISTS (SELECT 1 FROM calls WHERE parent_call_id=%s "
            "AND execution_mode='production' AND requested_model=%s) OR "
            "EXISTS (SELECT 1 FROM events WHERE call_id=%s AND event_type='tool.started' "
            "AND payload->>'tool_name'='mcp__bridge_monitor__delegate_local_analysis')",
            (paid_call_id, qwen_model, paid_call_id)).fetchone()[0])
