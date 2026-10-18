"""Process incarnation fencing and database-owned dispatch leases."""

import uuid
from typing import Literal

import psycopg
from psycopg.types.json import Jsonb

from contracts.policy import PolicySnapshot
from contracts.work import DispatchLease
from bridge.runtime.admission import ARBITRATION_LOCK
from bridge.guidance.selection import check_current_guidance


def register_incarnation(dsn: str, incarnation_id: str, worker_instance_id: str) -> None:
    if not incarnation_id or not worker_instance_id:
        raise ValueError("incarnation and worker identity are required")
    with psycopg.connect(dsn) as conn, conn.transaction():
        row = conn.execute("SELECT worker_instance_id,state FROM runtime_incarnations WHERE incarnation_id=%s",
                           (incarnation_id,)).fetchone()
        if row:
            if row != (worker_instance_id, "live"):
                raise ValueError("incarnation cannot be reused or resurrected")
            return
        conn.execute("INSERT INTO runtime_incarnations(incarnation_id,worker_instance_id,state) "
                     "VALUES(%s,%s,'live')", (incarnation_id, worker_instance_id))


def heartbeat_incarnation(dsn: str, incarnation_id: str) -> None:
    with psycopg.connect(dsn) as conn:
        row = conn.execute("UPDATE runtime_incarnations SET last_heartbeat_at=now() "
                           "WHERE incarnation_id=%s AND state='live' RETURNING incarnation_id",
                           (incarnation_id,)).fetchone()
        if not row:
            raise ValueError("incarnation is not live")


def _existing_lease(row) -> DispatchLease:
    call_id, lease_id, owner_incarnation, fencing_token, policy_version = row
    return DispatchLease(lease_id=lease_id, call_id=call_id, owner_incarnation=owner_incarnation,
                         fencing_token=fencing_token, policy_version=policy_version)


def claim_dispatch(dsn: str, *, pool: Literal["claude", "qwen"], incarnation_id: str,
                   call_id: str | None = None,
                   execution_mode: Literal["production", "shadow"] = "production") -> DispatchLease | None:
    """One serializable arbitration path across all bridge processes.

    A repeated claim for the same active lease is an acknowledgement retry only;
    it never launches another inference. The caller must deduplicate its launch by lease_id.
    """
    if execution_mode == "shadow" and pool != "qwen":
        raise ValueError("shadow dispatch is Qwen-only")
    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (ARBITRATION_LOCK,))
        incarnation = conn.execute("SELECT worker_instance_id,state FROM runtime_incarnations "
                                   "WHERE incarnation_id=%s", (incarnation_id,)).fetchone()
        if not incarnation or incarnation[1] != "live":
            raise ValueError("live registered incarnation required")
        if call_id:
            prior = conn.execute("SELECT call_id,lease_id,owner_incarnation,fencing_token,policy_version "
                                 "FROM runtime_jobs WHERE call_id=%s AND pool=%s "
                                 "AND execution_mode=%s AND state='active'",
                                 (call_id, pool, execution_mode)).fetchone()
            if prior and prior[2] == incarnation_id:
                return _existing_lease(prior)
            if prior:
                return None
        policy_row = conn.execute("SELECT c.policy FROM runtime_active_policy a "
                                  "JOIN config_versions c ON c.version=a.version "
                                  "WHERE a.singleton=true").fetchone()
        if not policy_row:
            raise ValueError("reviewed policy has not been activated")
        policy = PolicySnapshot.model_validate(policy_row[0])
        if execution_mode == "shadow" and policy.router.mode != "shadow":
            return None
        active_limit = policy.limits.claude_global_active if pool == "claude" else policy.limits.qwen_active
        held = conn.execute("SELECT count(*) FROM runtime_jobs WHERE pool=%s AND slot_held", (pool,)).fetchone()[0]
        if held >= active_limit:
            return None
        last_row = conn.execute("SELECT last_task_id FROM runtime_pool_cursor WHERE pool=%s", (pool,)).fetchone()
        last_task_id = last_row[0] if last_row else None
        # All candidates are read while the global transaction lock is held.
        candidates = conn.execute("SELECT call_id,task_id,policy_version,source_mode,"
                                  "source_snapshot,request FROM runtime_jobs "
                                  "WHERE pool=%s AND execution_mode=%s AND state='queued' "
                                  "AND queue_deadline_at>now() "
                                  "AND (local_cutoff_at IS NULL OR local_cutoff_at>now()) "
                                  "ORDER BY admitted_at,call_id", (pool, execution_mode)).fetchall()
        by_task = {}
        for candidate in candidates:
            by_task.setdefault(candidate[1], candidate)
        task_ids = sorted(by_task)
        if last_task_id:
            task_ids = [task for task in task_ids if task > last_task_id] + [task for task in task_ids if task <= last_task_id]
        winner = None
        winning_guidance = None
        for task_id in task_ids:
            candidate = by_task[task_id]
            if candidate[3]:
                source_id = candidate[5].get("task", {}).get("context", {}).get("source_id")
                if (pool != "claude" or candidate[3] != "read" or not source_id):
                    continue
                locks = conn.execute("SELECT mode,snapshot_ref FROM runtime_source_locks "
                                     "WHERE source_id=%s", (source_id,)).fetchall()
                if any(mode == "write" or snapshot != candidate[4]
                       for mode, snapshot in locks):
                    continue
            if pool == "claude":
                root_held = conn.execute("SELECT count(*) FROM runtime_jobs WHERE task_id=%s "
                                         "AND pool='claude' AND slot_held", (task_id,)).fetchone()[0]
                if root_held >= policy.limits.claude_per_root_active:
                    continue
            elif execution_mode == "production":
                selected = conn.execute("SELECT guidance_manifest FROM delegation_decisions "
                    "WHERE child_call_id=%s AND route='local' "
                    "ORDER BY decided_at DESC,decision_id DESC LIMIT 1", (candidate[0],)).fetchone()
                if selected:
                    request_row = conn.execute("SELECT request FROM runtime_jobs WHERE call_id=%s",
                                               (candidate[0],)).fetchone()
                    allowed, reason, review_ids = check_current_guidance(
                        conn, policy, selected[0], request_row[0]["task"])
                    if not allowed:
                        work = conn.execute("SELECT work_item_id FROM runtime_jobs WHERE call_id=%s",
                                            (candidate[0],)).fetchone()[0]
                        decision_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                            f"bridge-monitor:dispatch-gate:{candidate[0]}:{policy.version}:{reason}:{review_ids}"))
                        conn.execute("INSERT INTO delegation_decisions(decision_id,work_item_id,"
                            "child_call_id,route,eligibility,reason,policy_version,guidance_manifest,"
                            "gate_facts,decided_at) VALUES(%s,%s,%s,'pending','blocked',%s,%s,%s,%s,now()) "
                            "ON CONFLICT(decision_id) DO NOTHING", (decision_id, work, candidate[0],
                            reason, policy.version, Jsonb(selected[0]), Jsonb({
                                "pinned_policy_version": candidate[2],
                                "current_policy_version": policy.version,
                                "example_review_ids": review_ids,
                                "dispatch_gate": "fail"})))
                        continue
                    winning_guidance = (selected[0], candidate[2], review_ids)
            winner = candidate
            break
        if not winner:
            return None
        if call_id and winner[0] != call_id:
            return None
        chosen_call, root_task, governing_version = winner[:3]
        lease_id = str(uuid.uuid4())
        deadline_seconds = (policy.limits.claude_execution_seconds if pool == "claude"
                            else policy.router.execution_seconds if execution_mode == "shadow"
                            else policy.limits.qwen_execution_seconds)
        row = conn.execute("UPDATE runtime_jobs SET state='active',owner_incarnation=%s,"
                           "worker_instance_id=%s,lease_id=%s,fencing_token=fencing_token+1,"
                           "slot_held=true,execution_deadline_at=now()+(%s * interval '1 second') "
                           "WHERE call_id=%s AND state='queued' RETURNING fencing_token",
                           (incarnation_id, incarnation[0], lease_id, deadline_seconds, chosen_call)).fetchone()
        if not row:
            raise RuntimeError("dispatch arbitration lost under transaction lock")
        if winner[3]:
            source_id = winner[5]["task"]["context"]["source_id"]
            conn.execute("INSERT INTO runtime_source_locks(source_id,holder_call_id,mode,snapshot_ref) "
                         "VALUES(%s,%s,'read',%s)", (source_id, chosen_call, winner[4]))
        if pool == "qwen" and winning_guidance:
            manifest, pinned_version, review_ids = winning_guidance
            work = conn.execute("SELECT work_item_id FROM runtime_jobs WHERE call_id=%s",
                                (chosen_call,)).fetchone()[0]
            conn.execute("INSERT INTO delegation_decisions(decision_id,work_item_id,"
                "child_call_id,route,eligibility,reason,policy_version,guidance_manifest,"
                "gate_facts,decided_at) VALUES(%s,%s,%s,'pending','eligible',"
                "'dispatch_recheck_passed',%s,%s,%s,now())",
                (str(uuid.uuid5(uuid.NAMESPACE_URL,
                    f"bridge-monitor:dispatch-recheck:{chosen_call}:{lease_id}")),
                 work, chosen_call, policy.version, Jsonb(manifest), Jsonb({
                     "pinned_policy_version": pinned_version,
                     "current_policy_version": policy.version,
                     "example_review_ids": review_ids,
                     "dispatch_gate": "pass"})))
        conn.execute("INSERT INTO runtime_pool_cursor(pool,last_task_id) VALUES(%s,%s) "
                     "ON CONFLICT(pool) DO UPDATE SET last_task_id=excluded.last_task_id",
                     (pool, root_task))
        return DispatchLease(lease_id=lease_id, call_id=chosen_call,
                             owner_incarnation=incarnation_id, fencing_token=row[0],
                             policy_version=governing_version)


def claim_launch(dsn: str, lease: DispatchLease) -> bool:
    """One launch claim per lease; an uncertain lost acknowledgement cannot start twice."""
    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (ARBITRATION_LOCK,))
        row = conn.execute("UPDATE runtime_jobs SET launch_claimed_at=now() "
                           "WHERE call_id=%s AND lease_id=%s AND owner_incarnation=%s "
                           "AND fencing_token=%s AND state='active' AND launch_claimed_at IS NULL "
                           "RETURNING call_id", (lease.call_id, lease.lease_id,
                           lease.owner_incarnation, lease.fencing_token)).fetchone()
        return bool(row)


def abort_unlaunched(dsn: str, lease: DispatchLease) -> bool:
    """Cancel a verified never-launched call after source preparation fails."""
    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (ARBITRATION_LOCK,))
        row = conn.execute("UPDATE runtime_jobs SET state='cancelled',slot_held=false "
                           "WHERE call_id=%s AND lease_id=%s AND owner_incarnation=%s "
                           "AND state='active' AND launch_claimed_at IS NULL "
                           "RETURNING task_id,reserved_tokens", (lease.call_id, lease.lease_id,
                                                                  lease.owner_incarnation)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM runtime_source_locks WHERE holder_call_id=%s", (lease.call_id,))
        conn.execute("UPDATE runtime_task_budgets SET calls_reserved=calls_reserved-1,"
                     "tokens_reserved=tokens_reserved-%s WHERE task_id=%s", (row[1], row[0]))
        return True


def mark_execution_uncertain(dsn: str, call_id: str, *, reason: str) -> None:
    if not reason:
        raise ValueError("uncertainty reason is required")
    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (ARBITRATION_LOCK,))
        row = conn.execute("UPDATE runtime_jobs SET state='uncertain' WHERE call_id=%s "
                           "AND state='active' RETURNING owner_incarnation", (call_id,)).fetchone()
        if row and row[0]:
            conn.execute("UPDATE runtime_incarnations SET state='uncertain' "
                         "WHERE incarnation_id=%s AND state='live'", (row[0],))
