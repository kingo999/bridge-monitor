"""Queue operations around the shared database arbiter."""

import psycopg

from bridge.runtime.admission import ARBITRATION_LOCK
from bridge.runtime.ownership import claim_dispatch


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
