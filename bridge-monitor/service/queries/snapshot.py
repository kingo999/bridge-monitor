"""Commit-ordered dashboard reads. Publication rows and projections share an MVCC view."""

from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from contracts.events import DashboardStatus
from contracts.queries import CohortQuery, DashboardSnapshot, PublicationCursor


class CursorExpired(ValueError):
    def __init__(self, earliest_position: int):
        super().__init__("publication cursor is outside retained history")
        self.earliest_position = earliest_position


def read_snapshot(dsn: str, query: CohortQuery, *, limit: int = 50) -> DashboardSnapshot:
    if not 1 <= limit <= 100:
        raise ValueError("snapshot page out of bounds")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cursor = conn.execute("SELECT next_position-1 AS position FROM publication_counter WHERE singleton=true").fetchone()
            status = conn.execute("""SELECT active,queued,held_uncertain,blocked,capture_state,
                known_gaps,spool_pressure FROM dashboard_status WHERE singleton=true""").fetchone()
            capacity = None
            runtime_table = conn.execute("SELECT to_regclass('runtime_jobs') AS table_name").fetchone()["table_name"]
            if (runtime_table and query.origin != "synthetic" and conn.execute("""SELECT 1 FROM runtime_jobs j
                    JOIN work_items w ON w.work_item_id=j.work_item_id
                    WHERE w.origin='observed' AND j.execution_mode='production' LIMIT 1""").fetchone()):
                capacity = {name: {"active": 0, "queued": 0, "held_uncertain": 0} for name in ("claude", "qwen")}
                for row in conn.execute("""SELECT j.pool,j.state,j.slot_held,count(*) AS total
                    FROM runtime_jobs j JOIN work_items w ON w.work_item_id=j.work_item_id
                    WHERE w.origin='observed' AND j.execution_mode='production'
                    GROUP BY j.pool,j.state,j.slot_held"""):
                    values = capacity[row["pool"]]
                    if row["state"] == "queued":
                        values["queued"] += row["total"]
                    elif row["slot_held"] and row["state"] in ("uncertain", "fallback"):
                        values["held_uncertain"] += row["total"]
                    elif row["slot_held"] and row["state"] == "active":
                        values["active"] += row["total"]
                status = dict(status)
                for key in ("active", "queued", "held_uncertain"):
                    status[key] = sum(values[key] for values in capacity.values())
            items = conn.execute("""SELECT w.work_item_id,w.task_id,w.category,w.origin,w.created_at,
                (SELECT r.outcome FROM work_resolutions r WHERE r.work_item_id=w.work_item_id
                    ORDER BY r.recorded_at DESC,r.resolution_id DESC LIMIT 1) AS resolution,
                w.resolved_at,
                EXISTS (SELECT 1 FROM events e LEFT JOIN calls routed_call ON routed_call.call_id=e.call_id
                    WHERE e.work_item_id=w.work_item_id AND e.event_type='call.dispatched'
                    AND e.payload->>'destination'='skynet'
                    AND COALESCE(routed_call.execution_mode,e.payload->>'execution_mode','production')='production') AS routed,
                EXISTS (SELECT 1 FROM delegation_decisions d WHERE d.work_item_id=w.work_item_id
                    AND d.route='paid' AND d.execution_mode='production') AS paid_route,
                (SELECT me.requested_model FROM model_executions me JOIN attempts a ON a.attempt_id=me.attempt_id
                    JOIN calls c ON c.call_id=a.call_id WHERE c.work_item_id=w.work_item_id
                    AND c.execution_mode='production'
                    ORDER BY c.created_at DESC LIMIT 1) AS requested_model,
                (SELECT me.reported_model FROM model_executions me JOIN attempts a ON a.attempt_id=me.attempt_id
                    JOIN calls c ON c.call_id=a.call_id WHERE c.work_item_id=w.work_item_id
                    AND c.execution_mode='production'
                    ORDER BY c.created_at DESC LIMIT 1) AS reported_model
                FROM work_items w WHERE (%s='all' OR w.origin=%s)
                ORDER BY w.created_at DESC,w.work_item_id DESC LIMIT %s""",
                (query.origin, query.origin, limit)).fetchall()
            if capacity is not None:
                executions = conn.execute("""SELECT j.call_id,j.work_item_id,j.worker_instance_id,
                    j.requested_model,j.admitted_at AS occurred_at,
                    (SELECT e.reported_model FROM events e WHERE e.call_id=j.call_id
                        AND e.event_type='model.reported' ORDER BY e.occurred_at DESC LIMIT 1) AS reported_model
                    FROM runtime_jobs j JOIN work_items w ON w.work_item_id=j.work_item_id
                    WHERE w.origin='observed' AND j.execution_mode='production'
                    AND j.pool='claude' AND j.state IN ('active','uncertain')
                    ORDER BY j.admitted_at DESC LIMIT 8""").fetchall()
            else:
                executions = conn.execute("""SELECT e.call_id,e.work_item_id,e.worker_instance_id,
                    e.requested_model,e.occurred_at,
                    (SELECT r.reported_model FROM events r WHERE r.call_id=e.call_id
                        AND r.event_type='model.reported' ORDER BY r.occurred_at DESC LIMIT 1) AS reported_model
                    FROM events e WHERE e.event_type='call.dispatched'
                    AND e.payload->>'destination'='claude'
                    AND NOT EXISTS (SELECT 1 FROM work_resolutions resolved
                        WHERE resolved.work_item_id=e.work_item_id)
                    ORDER BY e.occurred_at DESC LIMIT 8""").fetchall()
            as_of = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
    return DashboardSnapshot(cursor=PublicationCursor(**cursor), as_of=as_of,
                             status=DashboardStatus(**status), work_items=[dict(row) for row in items],
                             worker_executions=[dict(row) for row in executions], capacity=capacity)


def read_publications(dsn: str, after_position: int, *, limit: int = 100) -> list[dict]:
    if after_position < 0 or not 1 <= limit <= 500:
        raise ValueError("publication page out of bounds")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            bounds = conn.execute("""SELECT (SELECT min(position) FROM publication_stream) AS first,
                next_position-1 AS last FROM publication_counter WHERE singleton=true""").fetchone()
            if after_position > bounds["last"]:
                raise CursorExpired(bounds["first"] or bounds["last"] + 1)
            if bounds["first"] is None and after_position < bounds["last"]:
                raise CursorExpired(bounds["last"] + 1)
            if bounds["first"] is not None and after_position < bounds["first"] - 1:
                raise CursorExpired(bounds["first"])
            rows = conn.execute("""SELECT p.position,p.published_at,e.event_id,e.event_type,e.task_id,
                e.work_item_id,e.call_id,e.attempt_id,e.model_execution_id,e.worker_instance_id,
                e.requested_model,e.reported_model,e.occurred_at,e.origin,e.capture_completeness,
                e.payload FROM publication_stream p JOIN events e ON e.event_id=p.event_id
                WHERE p.position>%s ORDER BY p.position LIMIT %s""",
                (after_position, limit)).fetchall()
    return [dict(row) for row in rows]
