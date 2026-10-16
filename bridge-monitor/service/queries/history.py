import psycopg

from contracts.queries import CohortQuery
from service.queries.cohorts import cutoff
from service.ingest.store import read_content


def list_work_items(dsn: str, query: CohortQuery, *, limit: int = 50, offset: int = 0) -> list[dict]:
    if not (1 <= limit <= 100) or offset < 0:
        raise ValueError("history page out of bounds")
    as_of = cutoff(query)
    with psycopg.connect(dsn) as conn:
        rows = conn.execute("""SELECT work_item_id,task_id,category,origin,created_at
            FROM work_items WHERE created_at<=%s AND (%s='all' OR origin=%s)
            AND (%s::timestamptz IS NULL OR created_at >= %s)
            AND (%s::timestamptz IS NULL OR created_at < %s)
            ORDER BY created_at DESC, work_item_id DESC LIMIT %s OFFSET %s""",
            (as_of, query.origin, query.origin, query.created_from, query.created_from,
             query.created_until, query.created_until, limit, offset)).fetchall()
    return [dict(zip(("work_item_id", "task_id", "category", "origin", "created_at"), row))
            for row in rows]


def inspect_exchange(dsn: str, work_item_id: str, *, role: str) -> dict | None:
    if role != "operator":
        raise PermissionError("operator access required")
    with psycopg.connect(dsn) as conn:
        work = conn.execute("SELECT work_item_id,task_id,category,origin FROM work_items WHERE work_item_id=%s",
                            (work_item_id,)).fetchone()
        if not work:
            return None
        prompt = conn.execute("""SELECT m.original_content_id FROM messages m
            JOIN calls c ON c.call_id=m.call_id
            WHERE c.work_item_id=%s AND m.original_content_id IS NOT NULL
            ORDER BY c.created_at LIMIT 1""", (work_item_id,)).fetchone()
        local = conn.execute("""SELECT r.delivered_content_id FROM results r
            JOIN calls c ON c.call_id=r.call_id
            WHERE c.work_item_id=%s AND r.delivered_at IS NOT NULL
            ORDER BY r.delivered_at LIMIT 1""", (work_item_id,)).fetchone()
        event_rows = conn.execute("""SELECT event_id,event_type,occurred_at,payload
            FROM events WHERE work_item_id=%s ORDER BY occurred_at,event_id LIMIT 200""",
            (work_item_id,)).fetchall()
    original = read_content(dsn, prompt[0], role="operator") if prompt else None
    response = read_content(dsn, local[0], role="operator") if local and local[0] else None
    return {"work_item_id": work[0], "task_id": work[1], "category": work[2],
            "origin": work[3], "original_prompt_content_id": prompt[0] if prompt else None,
            "local_response_content_id": local[0] if local else None,
            "original_prompt": original.decode("utf-8") if original else None,
            "local_response": response.decode("utf-8") if response else None,
            "events": [dict(zip(("event_id", "event_type", "occurred_at", "payload"), row))
                       for row in event_rows]}
