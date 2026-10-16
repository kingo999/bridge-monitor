"""Source checkout arbitration, independent of model capacity."""

from typing import Literal

import psycopg

from bridge.runtime.admission import ARBITRATION_LOCK


def acquire_source(dsn: str, call_id: str, source_id: str,
                   mode: Literal["read", "write"], snapshot_ref: str) -> bool:
    if not source_id or not snapshot_ref:
        raise ValueError("source identity and fingerprint are required")
    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (ARBITRATION_LOCK,))
        job = conn.execute("SELECT source_snapshot,source_mode,state FROM runtime_jobs WHERE call_id=%s",
                           (call_id,)).fetchone()
        if not job or job[2] in ("cancelled", "terminated", "completed", "fallback"):
            raise ValueError("source lock requires a live admitted job")
        if job[0] != snapshot_ref or (job[1] and job[1] != mode):
            raise ValueError("source access conflicts with immutable admission pin")
        prior = conn.execute("SELECT mode,snapshot_ref FROM runtime_source_locks "
                             "WHERE source_id=%s AND holder_call_id=%s", (source_id, call_id)).fetchone()
        if prior:
            return prior == (mode, snapshot_ref)
        others = conn.execute("SELECT mode,snapshot_ref FROM runtime_source_locks "
                              "WHERE source_id=%s", (source_id,)).fetchall()
        if others and (mode == "write" or any(other[0] == "write" or other[1] != snapshot_ref for other in others)):
            return False
        conn.execute("INSERT INTO runtime_source_locks(source_id,holder_call_id,mode,snapshot_ref) "
                     "VALUES(%s,%s,%s,%s)", (source_id, call_id, mode, snapshot_ref))
        return True


def release_source(dsn: str, call_id: str, source_id: str) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute("DELETE FROM runtime_source_locks WHERE source_id=%s AND holder_call_id=%s",
                     (source_id, call_id))
