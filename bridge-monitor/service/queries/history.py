import psycopg

from contracts.queries import CohortQuery
from service.queries.cohorts import cutoff
from service.queries.cohorts import leaf_work_items
from service.queries.guidance import work_guidance, matches_guidance_filters
from service.ingest.store import read_content


def list_work_items(dsn: str, query: CohortQuery, *, limit: int = 50, offset: int = 0) -> list[dict]:
    if not (1 <= limit <= 100) or offset < 0:
        raise ValueError("history page out of bounds")
    as_of = cutoff(query)
    with psycopg.connect(dsn) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            filtering = any((query.guidance_group, query.failure_category,
                             query.repair_effort, query.example_reuse_status))
            eligible_ids = []
            if filtering:
                cohort_ids = set(leaf_work_items(conn, query, as_of))
                facts = work_guidance(conn, cohort_ids, as_of)
                eligible_ids = [work_id for work_id in cohort_ids
                                if matches_guidance_filters(facts[work_id], query)]
            rows = conn.execute("""SELECT w.work_item_id,w.task_id,w.category,w.origin,w.created_at,
            (SELECT r.outcome FROM work_resolutions r WHERE r.work_item_id=w.work_item_id
                AND r.recorded_at<=%s ORDER BY r.recorded_at DESC,r.resolution_id DESC LIMIT 1) AS resolution,
            w.resolved_at,
            EXISTS (SELECT 1 FROM events e WHERE e.work_item_id=w.work_item_id
                AND e.event_type='call.dispatched' AND e.payload->>'destination'='skynet'
                AND e.occurred_at<=%s) AS routed,
            EXISTS (SELECT 1 FROM delegation_decisions d WHERE d.work_item_id=w.work_item_id
                AND d.route='paid' AND d.decided_at<=%s) AS paid_route,
            (SELECT me.requested_model FROM model_executions me JOIN attempts a ON a.attempt_id=me.attempt_id
                JOIN calls c ON c.call_id=a.call_id WHERE c.work_item_id=w.work_item_id
                ORDER BY c.created_at DESC LIMIT 1) AS requested_model,
            (SELECT me.reported_model FROM model_executions me JOIN attempts a ON a.attempt_id=me.attempt_id
                JOIN calls c ON c.call_id=a.call_id WHERE c.work_item_id=w.work_item_id
                ORDER BY c.created_at DESC LIMIT 1) AS reported_model,
            (SELECT d.judgment FROM dispositions d JOIN results r ON r.result_id=d.result_id
                JOIN calls c ON c.call_id=r.call_id WHERE c.work_item_id=w.work_item_id
                AND d.stage=%s AND d.recorded_at<=%s
                AND NOT EXISTS (SELECT 1 FROM dispositions newer WHERE newer.supersedes_id=d.disposition_id
                    AND newer.recorded_at<=%s)
                ORDER BY d.recorded_at DESC,d.disposition_id DESC LIMIT 1) AS disposition_judgment
            FROM work_items w WHERE w.created_at<=%s AND (%s='all' OR w.origin=%s)
            AND (%s::timestamptz IS NULL OR w.created_at >= %s)
            AND (%s::timestamptz IS NULL OR w.created_at < %s)
            AND (%s=false OR EXISTS (SELECT 1 FROM work_resolutions r WHERE r.work_item_id=w.work_item_id AND r.recorded_at<=%s))
            AND (%s=false OR w.work_item_id=ANY(%s))
            ORDER BY w.created_at DESC, w.work_item_id DESC LIMIT %s OFFSET %s""",
            (as_of, as_of, as_of, query.consumer_stage, as_of, as_of, as_of,
             query.origin, query.origin, query.created_from, query.created_from,
             query.created_until, query.created_until, query.completed_only, as_of,
             filtering, eligible_ids, limit, offset)).fetchall()
            page_facts = work_guidance(conn, {row[0] for row in rows}, as_of)
    return [dict(zip(("work_item_id", "task_id", "category", "origin", "created_at",
                      "resolution", "resolved_at", "routed", "paid_route",
                      "requested_model", "reported_model", "disposition_judgment"), row),
                 guidance_group=page_facts[row[0]]["guidance_group"],
                 failure_categories=page_facts[row[0]]["failure_categories"],
                 repair_efforts=page_facts[row[0]]["repair_efforts"],
                 example_reuse_statuses=page_facts[row[0]]["example_reuse_statuses"])
            for row in rows]


def read_work_item_detail(dsn: str, work_item_id: str) -> dict | None:
    """Metadata and content references only; the browser fetches bodies on selection."""
    from psycopg.rows import dict_row
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            work = conn.execute("SELECT * FROM work_items WHERE work_item_id=%s", (work_item_id,)).fetchone()
            if work is None:
                return None
            calls = conn.execute("""SELECT c.call_id,c.parent_call_id,c.requested_model,c.created_at,
                EXISTS (SELECT 1 FROM events e WHERE e.call_id=c.call_id AND e.event_type='call.dispatched'
                    AND e.payload->>'destination'='skynet') AS local_dispatch
                FROM calls c WHERE c.work_item_id=%s ORDER BY c.created_at,c.call_id""",
                (work_item_id,)).fetchall()
            messages = conn.execute("""SELECT m.message_id,m.call_id,m.role,m.direction,
                m.original_content_id,m.effective_content_id FROM messages m JOIN calls c ON c.call_id=m.call_id
                WHERE c.work_item_id=%s ORDER BY c.created_at,m.message_id""", (work_item_id,)).fetchall()
            results = conn.execute("""SELECT r.result_id,r.call_id,r.attempt_id,r.raw_content_id,
                r.delivered_content_id,r.delivered_at FROM results r JOIN calls c ON c.call_id=r.call_id
                WHERE c.work_item_id=%s ORDER BY r.delivered_at,r.result_id""", (work_item_id,)).fetchall()
            models = conn.execute("""SELECT me.model_execution_id,me.attempt_id,a.call_id,me.requested_model,
                me.reported_model,me.model_evidence,a.status FROM model_executions me
                JOIN attempts a ON a.attempt_id=me.attempt_id JOIN calls c ON c.call_id=a.call_id
                WHERE c.work_item_id=%s ORDER BY me.model_execution_id""", (work_item_id,)).fetchall()
            decisions = conn.execute("""SELECT decision_id,route,eligibility,reason,policy_version,
                child_call_id,guidance_manifest,gate_facts,decided_at FROM delegation_decisions
                WHERE work_item_id=%s ORDER BY decided_at,decision_id""", (work_item_id,)).fetchall()
            supplied_decisions = conn.execute("""SELECT DISTINCT chosen.decision_id
                FROM events e JOIN LATERAL (
                    SELECT d.decision_id FROM delegation_decisions d
                    WHERE d.child_call_id=e.call_id AND d.route='local'
                      AND d.decided_at<=e.occurred_at
                    ORDER BY d.decided_at DESC,d.decision_id DESC LIMIT 1
                ) chosen ON true
                WHERE e.work_item_id=%s AND e.event_type='call.dispatched'
                  AND e.payload->>'destination'='skynet'""", (work_item_id,)).fetchall()
            dispositions = conn.execute("""SELECT d.disposition_id,d.result_id,d.actor_id,d.stage,d.judgment,
                d.reason,d.supersedes_id,d.recorded_at,d.details,d.part_id,d.input_sha256,
                d.output_result_id FROM dispositions d JOIN results r ON r.result_id=d.result_id
                JOIN calls c ON c.call_id=r.call_id WHERE c.work_item_id=%s
                ORDER BY d.recorded_at,d.disposition_id""", (work_item_id,)).fetchall()
            resolutions = conn.execute("""SELECT resolution_id,outcome,final_result_id,recorded_at
                FROM work_resolutions WHERE work_item_id=%s ORDER BY recorded_at,resolution_id""",
                (work_item_id,)).fetchall()
            events = conn.execute("""SELECT event_id,event_type,occurred_at,received_at,call_id,attempt_id,
                worker_instance_id,requested_model,reported_model,capture_completeness,payload
                FROM events WHERE work_item_id=%s ORDER BY occurred_at,event_id LIMIT 500""",
                (work_item_id,)).fetchall()
            pack_ids = sorted({d["guidance_manifest"].get("pack_version_id") for d in decisions
                               if d["guidance_manifest"].get("pack_version_id")})
            packs = conn.execute("SELECT pack_version_id,pack_id,version_label,sha256,items "
                                 "FROM guidance_pack_versions WHERE pack_version_id=ANY(%s)",
                                 (pack_ids,)).fetchall()
            reviews = conn.execute("""SELECT er.review_id,er.pack_version_id,er.example_id,
                er.content_id,er.source_work_item_id,er.source_result_id,er.reviewer_id,
                er.status,er.permitted_use,er.exposure,er.scope,er.evidence,
                er.supersedes_id,er.reviewed_at,source.origin AS source_origin
                FROM example_reviews er LEFT JOIN work_items source
                  ON source.work_item_id=er.source_work_item_id
                WHERE er.pack_version_id=ANY(%s) ORDER BY er.reviewed_at,er.review_id""",
                (pack_ids,)).fetchall()
            evaluations = conn.execute("""SELECT ev.evaluation_id,ev.result_id,ev.evidence,ev.recorded_at
                FROM evaluations ev JOIN results r ON r.result_id=ev.result_id
                JOIN calls c ON c.call_id=r.call_id WHERE c.work_item_id=%s
                ORDER BY ev.recorded_at,ev.evaluation_id""", (work_item_id,)).fetchall()
            links = conn.execute("""SELECT l.from_result_id,l.to_result_id,l.relation
                FROM result_links l WHERE l.from_result_id IN (
                  SELECT r.result_id FROM results r JOIN calls c ON c.call_id=r.call_id
                  WHERE c.work_item_id=%s) OR l.to_result_id IN (
                  SELECT r.result_id FROM results r JOIN calls c ON c.call_id=r.call_id
                  WHERE c.work_item_id=%s)""", (work_item_id, work_item_id)).fetchall()
            ids = {identifier for row in messages for identifier in
                   (row["original_content_id"], row["effective_content_id"]) if identifier}
            ids.update(identifier for row in results for identifier in
                       (row["raw_content_id"], row["delivered_content_id"]) if identifier)
            content = conn.execute("""SELECT content_id,completeness,byte_length,content_type,
                encoding FROM content_objects WHERE content_id=ANY(%s)""", (list(ids),)).fetchall()
    work_data = dict(work)
    if resolutions:
        work_data["resolution"] = resolutions[-1]["outcome"]
        work_data["resolved_at"] = resolutions[-1]["recorded_at"]
    return {"work_item": work_data, "calls": list(map(dict, calls)),
            "messages": list(map(dict, messages)), "results": list(map(dict, results)),
            "models": list(map(dict, models)), "decisions": list(map(dict, decisions)),
            "supplied_decision_ids": [row["decision_id"] for row in supplied_decisions],
            "dispositions": list(map(dict, dispositions)),
            "resolutions": list(map(dict, resolutions)), "events": list(map(dict, events)),
            "guidance_packs": list(map(dict, packs)), "example_reviews": list(map(dict, reviews)),
            "evaluations": list(map(dict, evaluations)), "result_links": list(map(dict, links)),
            "content_states": {row["content_id"]: dict(row) for row in content}}


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
            AND m.role='user' AND m.direction='out'
            AND EXISTS (SELECT 1 FROM events dispatch WHERE dispatch.call_id=c.call_id
                AND dispatch.event_type='call.dispatched'
                AND dispatch.payload->>'destination'='skynet')
            ORDER BY c.created_at,m.message_id LIMIT 1""", (work_item_id,)).fetchone()
        local = conn.execute("""SELECT r.delivered_content_id FROM results r
            JOIN calls c ON c.call_id=r.call_id
            WHERE c.work_item_id=%s AND r.delivered_at IS NOT NULL
            AND EXISTS (SELECT 1 FROM events dispatch WHERE dispatch.call_id=c.call_id
                AND dispatch.event_type='call.dispatched'
                AND dispatch.payload->>'destination'='skynet')
            ORDER BY r.delivered_at,r.result_id LIMIT 1""", (work_item_id,)).fetchone()
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
