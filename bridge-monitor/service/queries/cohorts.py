from datetime import datetime, timezone

import psycopg

from contracts.queries import CohortQuery


def cutoff(query: CohortQuery) -> datetime:
    return query.as_of or datetime.now(timezone.utc)


def leaf_work_items(conn: psycopg.Connection, query: CohortQuery, as_of: datetime) -> list[str]:
    rows = conn.execute("""SELECT w.work_item_id FROM work_items w
        WHERE w.created_at <= %s
          AND (%s = 'all' OR w.origin = %s)
          AND (%s::timestamptz IS NULL OR w.created_at >= %s)
          AND (%s::timestamptz IS NULL OR w.created_at < %s)
          AND (%s = false OR EXISTS (
               SELECT 1 FROM work_resolutions r WHERE r.work_item_id=w.work_item_id
               AND r.recorded_at <= %s))
          AND NOT EXISTS (SELECT 1 FROM work_items c
               WHERE c.parent_work_item_id=w.work_item_id AND c.created_at <= %s)
        ORDER BY w.work_item_id""",
        (as_of, query.origin, query.origin, query.created_from, query.created_from,
         query.created_until, query.created_until, query.completed_only, as_of, as_of)).fetchall()
    return [row[0] for row in rows]


def page_leaf_work_items(conn: psycopg.Connection, query: CohortQuery,
                         as_of: datetime, before: tuple | None,
                         *, limit: int = 100) -> list[tuple]:
    """Keyset page for history filters; never materialize the whole cohort."""
    if not 1 <= limit <= 100:
        raise ValueError("cohort scan page out of bounds")
    return conn.execute("""SELECT w.work_item_id,w.created_at FROM work_items w
        WHERE w.created_at<=%s
          AND (%s='all' OR w.origin=%s)
          AND (%s::timestamptz IS NULL OR w.created_at>=%s)
          AND (%s::timestamptz IS NULL OR w.created_at<%s)
          AND (%s=false OR EXISTS (SELECT 1 FROM work_resolutions r
              WHERE r.work_item_id=w.work_item_id AND r.recorded_at<=%s))
          AND NOT EXISTS (SELECT 1 FROM work_items c
              WHERE c.parent_work_item_id=w.work_item_id AND c.created_at<=%s)
          AND (%s::timestamptz IS NULL OR
              (w.created_at,w.work_item_id)<(%s,%s))
        ORDER BY w.created_at DESC,w.work_item_id DESC LIMIT %s""",
        (as_of, query.origin, query.origin, query.created_from, query.created_from,
         query.created_until, query.created_until, query.completed_only, as_of,
         as_of, before[0] if before else None, before[0] if before else None,
         before[1] if before else None, limit)).fetchall()
