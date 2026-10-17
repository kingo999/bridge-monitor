"""Configured content retention with immutable identity and replay-boundary facts."""

import uuid
from datetime import datetime, timedelta, timezone

import psycopg

from bridge.runtime.admission import ARBITRATION_LOCK


def apply_retention(dsn: str, *, retention_days: int,
                    now: datetime | None = None) -> dict:
    if retention_days <= 0:
        raise ValueError("positive configured retention period required")
    observed_at = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        raise ValueError("retention clock must be timezone aware")
    cutoff = observed_at - timedelta(days=retention_days)
    run_id = str(uuid.uuid4())
    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (ARBITRATION_LOCK,))
        tombstones = conn.execute("WITH removed AS (UPDATE content_objects c "
            "SET raw_bytes=NULL,completeness='removed' "
            "WHERE c.created_at<%s AND c.raw_bytes IS NOT NULL "
            "AND c.capture_layer<>'registry' "
            "AND NOT EXISTS (SELECT 1 FROM runtime_jobs j WHERE j.prompt_content_id=c.content_id "
            "AND j.state IN ('queued','active','uncertain','fallback')) "
            "AND NOT EXISTS (SELECT 1 FROM runtime_disposition_obligations o "
            "JOIN results r ON r.result_id=o.result_id "
            "WHERE c.content_id IN (r.raw_content_id,r.delivered_content_id) "
            "AND NOT EXISTS (SELECT 1 FROM dispositions d WHERE d.result_id=o.result_id "
            "AND d.stage=o.stage)) "
            "AND NOT EXISTS (SELECT 1 FROM example_reviews er WHERE er.content_id=c.content_id "
            "AND er.status='approved' AND NOT EXISTS (SELECT 1 FROM example_reviews newer "
            "WHERE newer.supersedes_id=er.review_id)) "
            "RETURNING c.content_id) SELECT count(*) FROM removed", (cutoff,)).fetchone()[0]
        removed = conn.execute("WITH expired AS (DELETE FROM publication_stream "
            "WHERE published_at<%s RETURNING position) SELECT count(*) FROM expired",
            (cutoff,)).fetchone()[0]
        first = conn.execute("SELECT min(position) FROM publication_stream").fetchone()[0]
        last = conn.execute("SELECT next_position-1 FROM publication_counter "
                            "WHERE singleton=true").fetchone()[0]
        earliest = first if first is not None else last + 1
        conn.execute("INSERT INTO retention_runs(run_id,cutoff_at,content_tombstones,"
            "publications_removed,earliest_position) VALUES(%s,%s,%s,%s,%s)",
            (run_id, cutoff, tombstones, removed, earliest))
    return {"run_id": run_id, "cutoff_at": cutoff,
            "content_tombstones": tombstones, "publications_removed": removed,
            "earliest_position": earliest}
