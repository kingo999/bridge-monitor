"""Queue operations around the shared database arbiter."""

import uuid

import psycopg
from psycopg.types.json import Jsonb

from bridge.runtime.admission import ARBITRATION_LOCK
from bridge.runtime.ownership import claim_dispatch


def cancellation_requested(dsn: str, call_id: str) -> bool:
    """Read the durable request; an uncertain state alone is not cancellation."""
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT 1 FROM runtime_reconciliations WHERE call_id=%s "
            "AND evidence->>'kind'='cancellation_requested' LIMIT 1",
            (call_id,)).fetchone() is not None


def request_cancel_tree(dsn: str, parent_call_id: str, *, reason: str) -> tuple[str, ...]:
    """Cancel queued descendants and durably request active-child termination.

    An active or uncertain inference keeps its capacity until reconciliation
    proves termination. A cancellation request is not termination evidence.
    """
    if not parent_call_id or not reason:
        raise ValueError("cancellation root and reason are required")
    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (ARBITRATION_LOCK,))
        descendants = conn.execute("WITH RECURSIVE family(call_id) AS ("
            "SELECT call_id FROM calls WHERE call_id=%s UNION "
            "SELECT edge.call_id FROM family f CROSS JOIN LATERAL ("
            "SELECT c.call_id FROM calls c WHERE c.parent_call_id=f.call_id UNION "
            "SELECT l.recovery_call_id AS call_id FROM runtime_recovery_links l "
            "WHERE l.child_call_id=f.call_id) edge) "
            "SELECT j.call_id,j.state,j.task_id,j.reserved_tokens FROM runtime_jobs j "
            "JOIN family f ON f.call_id=j.call_id ORDER BY j.admitted_at,j.call_id",
            (parent_call_id,)).fetchall()
        affected = []
        for call_id, state, task_id, reserved in descendants:
            if state == "queued":
                conn.execute("UPDATE runtime_jobs SET state='cancelled' WHERE call_id=%s",
                             (call_id,))
                conn.execute("UPDATE runtime_task_budgets SET calls_reserved=calls_reserved-1,"
                             "tokens_reserved=tokens_reserved-%s WHERE task_id=%s",
                             (reserved, task_id))
                affected.append(call_id)
            elif state in ("active", "uncertain", "fallback"):
                conn.execute("UPDATE runtime_jobs SET state='uncertain' WHERE call_id=%s",
                             (call_id,))
                conn.execute("INSERT INTO runtime_reconciliations(reconciliation_id,call_id,"
                             "observed_state,evidence,actor_kind,actor_id) "
                             "VALUES(%s,%s,'uncertain',%s,'system','coordinator')",
                             (str(uuid.uuid4()), call_id,
                              Jsonb({"kind": "cancellation_requested", "reason": reason})))
                affected.append(call_id)
            elif state in ("completed", "terminated"):
                conn.execute("INSERT INTO runtime_reconciliations(reconciliation_id,call_id,"
                             "observed_state,evidence,actor_kind,actor_id) "
                             "VALUES(%s,%s,'uncertain',%s,'system','coordinator')",
                             (str(uuid.uuid4()), call_id,
                              Jsonb({"kind": "cancellation_requested", "reason": reason})))
                affected.append(call_id)
        family_ids = [parent_call_id, *(row[0] for row in descendants)]
        conn.execute("UPDATE runtime_fallback_transitions SET state='cancelled',resolved_at=now() "
                     "WHERE recipient_call_id=ANY(%s) AND state='committed'", (family_ids,))
        return tuple(affected)


def next_dispatch(dsn: str, pool: str, incarnation_id: str):
    return claim_dispatch(dsn, pool=pool, incarnation_id=incarnation_id)


def cancel_queued(dsn: str, call_id: str) -> bool:
    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (ARBITRATION_LOCK,))
        row = conn.execute("UPDATE runtime_jobs SET state='cancelled' "
                           "WHERE call_id=%s AND state='queued' "
                           "RETURNING task_id,reserved_tokens", (call_id,)).fetchone()
        if not row:
            return False
        conn.execute("UPDATE runtime_task_budgets SET calls_reserved=calls_reserved-1, "
                     "tokens_reserved=tokens_reserved-%s WHERE task_id=%s", (row[1], row[0]))
        return True
