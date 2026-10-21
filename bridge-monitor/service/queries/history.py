import psycopg

from contracts.queries import CohortQuery
from service.queries.cohorts import cutoff
from service.queries.cohorts import page_leaf_work_items
from service.queries.guidance import work_guidance, matches_guidance_filters
from service.ingest.store import read_content


def _gpt_work_judgments(conn, work_ids: list[str], as_of) -> dict[str, str]:
    """Show one judgment only when the work has one delivered Claude result."""
    if not work_ids:
        return {}
    deliveries = conn.execute("""SELECT c.work_item_id,r.result_id FROM results r
        JOIN calls c ON c.call_id=r.call_id
        JOIN work_items w ON w.work_item_id=c.work_item_id
        WHERE c.work_item_id=ANY(%s) AND c.parent_call_id='gpt:' || w.task_id
          AND c.execution_mode='production'
          AND r.delivered_at IS NOT NULL AND r.delivered_at<=%s
          AND r.delivered_content_id IS NOT NULL""", (work_ids, as_of)).fetchall()
    by_work = {}
    for work_id, result_id in deliveries:
        by_work.setdefault(work_id, []).append(result_id)
    result_ids = [result_id for _, result_id in deliveries]
    if not result_ids:
        return {}
    candidates = conn.execute("""SELECT d.disposition_id,d.result_id,d.judgment,
        d.supersedes_id FROM dispositions d
        JOIN results r ON r.result_id=d.result_id
        JOIN calls c ON c.call_id=r.call_id
        JOIN work_items w ON w.work_item_id=c.work_item_id
        JOIN content_objects content ON content.content_id=r.delivered_content_id
        LEFT JOIN runtime_disposition_obligations o ON o.result_id=r.result_id
          AND o.consumer_call_id=c.parent_call_id AND o.stage='gpt' AND o.part_id IS NULL
        WHERE d.result_id=ANY(%s) AND d.stage='gpt' AND d.recorded_at<=%s
          AND d.actor_id='gpt-parent:' || c.parent_call_id
          AND d.input_sha256=content.sha256
          AND (w.origin<>'observed' OR
               (o.content_sha256=content.sha256 AND
                d.details->>'provenance'='parent_mcp_report'))""",
        (result_ids, as_of)).fetchall()
    by_result = {}
    for disposition_id, result_id, judgment, supersedes_id in candidates:
        by_result.setdefault(result_id, []).append((disposition_id, judgment, supersedes_id))
    selected = {}
    for work_id, delivered in by_work.items():
        if len(delivered) != 1:
            continue
        rows = by_result.get(delivered[0], [])
        superseded = {target for _, _, target in rows if target}
        effective = [judgment for identifier, judgment, _ in rows if identifier not in superseded]
        if len(effective) == 1:
            selected[work_id] = effective[0]
    return selected


def derive_agent_statuses(events: list[dict]) -> list[dict]:
    agent_calls = {row["call_id"] for row in events
                   if row["event_type"] == "attempt.status" and row["call_id"]}
    statuses = {call_id: {"call_id": call_id, "agent_state": None,
                          "agent_reachable": None, "last_model_output_at": None,
                          "timeout_reason": None} for call_id in agent_calls}
    for row in events:
        state = statuses.get(row["call_id"])
        if state is None:
            continue
        if row["event_type"] == "attempt.status":
            payload = row["payload"]
            state["agent_state"] = payload.get("agent_state", state["agent_state"])
            state["agent_reachable"] = payload.get("agent_reachable", state["agent_reachable"])
        elif row["event_type"] == "response.delta":
            state["last_model_output_at"] = row["occurred_at"]
        elif row["event_type"] == "attempt.uncertain":
            state["timeout_reason"] = row["payload"].get("reason")
    return [statuses[call_id] for call_id in sorted(statuses)]


def list_work_items(dsn: str, query: CohortQuery, *, limit: int = 50, offset: int = 0,
                    max_limit: int = 100) -> list[dict]:
    if not (1 <= limit <= max_limit <= 2000) or offset < 0:
        raise ValueError("history page out of bounds")
    as_of = cutoff(query)
    with psycopg.connect(dsn) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            filtering = any((query.guidance_group, query.failure_category,
                             query.repair_effort, query.example_reuse_status))
            eligible_ids = []
            if filtering:
                before = None
                remaining_offset = offset
                while len(eligible_ids) < limit:
                    batch = page_leaf_work_items(conn, query, as_of, before)
                    if not batch:
                        break
                    facts = work_guidance(conn, {row[0] for row in batch}, as_of)
                    for work_id, _ in batch:
                        if not matches_guidance_filters(facts[work_id], query):
                            continue
                        if remaining_offset:
                            remaining_offset -= 1
                        else:
                            eligible_ids.append(work_id)
                            if len(eligible_ids) == limit:
                                break
                    before = (batch[-1][1], batch[-1][0])
                    if len(batch) < 100:
                        break
            rows = conn.execute("""SELECT w.work_item_id,w.task_id,w.category,w.origin,w.created_at,
            (SELECT r.outcome FROM work_resolutions r WHERE r.work_item_id=w.work_item_id
                AND r.recorded_at<=%s ORDER BY r.recorded_at DESC,r.resolution_id DESC LIMIT 1) AS resolution,
            (SELECT r.recorded_at FROM work_resolutions r WHERE r.work_item_id=w.work_item_id
                AND r.recorded_at<=%s ORDER BY r.recorded_at DESC,r.resolution_id DESC LIMIT 1) AS resolved_at,
            EXISTS (SELECT 1 FROM events e LEFT JOIN calls routed_call ON routed_call.call_id=e.call_id
                WHERE e.work_item_id=w.work_item_id
                AND e.event_type='call.dispatched' AND e.payload->>'destination'='skynet'
                AND COALESCE(routed_call.execution_mode,e.payload->>'execution_mode','production')='production'
                AND e.occurred_at<=%s) AS routed,
            EXISTS (SELECT 1 FROM delegation_decisions d WHERE d.work_item_id=w.work_item_id
                AND d.route='paid' AND d.execution_mode='production' AND d.decided_at<=%s) AS paid_route,
            (SELECT me.requested_model FROM model_executions me JOIN attempts a ON a.attempt_id=me.attempt_id
                JOIN calls c ON c.call_id=a.call_id WHERE c.work_item_id=w.work_item_id
                AND c.execution_mode='production'
                ORDER BY c.created_at DESC,me.model_execution_id DESC LIMIT 1) AS requested_model,
            (SELECT me.reported_model FROM model_executions me JOIN attempts a ON a.attempt_id=me.attempt_id
                JOIN calls c ON c.call_id=a.call_id WHERE c.work_item_id=w.work_item_id
                AND c.execution_mode='production'
                ORDER BY c.created_at DESC,me.model_execution_id DESC LIMIT 1) AS reported_model,
            (SELECT effective.judgment FROM (
                SELECT d.result_id,d.part_id,min(d.judgment) AS judgment,
                    max(d.recorded_at) AS latest FROM dispositions d
                JOIN results r ON r.result_id=d.result_id
                JOIN calls c ON c.call_id=r.call_id WHERE c.work_item_id=w.work_item_id
                AND c.execution_mode='production'
                AND d.stage=%s AND d.recorded_at<=%s
                AND NOT EXISTS (SELECT 1 FROM dispositions newer WHERE newer.supersedes_id=d.disposition_id
                    AND newer.recorded_at<=%s)
                GROUP BY d.result_id,d.part_id HAVING count(*)=1
            ) effective ORDER BY effective.latest DESC,effective.result_id DESC LIMIT 1) AS disposition_judgment
            FROM work_items w WHERE w.created_at<=%s AND (%s='all' OR w.origin=%s)
            AND (%s::timestamptz IS NULL OR w.created_at >= %s)
            AND (%s::timestamptz IS NULL OR w.created_at < %s)
            AND (%s=false OR EXISTS (SELECT 1 FROM work_resolutions r WHERE r.work_item_id=w.work_item_id AND r.recorded_at<=%s))
            AND (%s=false OR w.work_item_id=ANY(%s))
            ORDER BY w.created_at DESC, w.work_item_id DESC LIMIT %s OFFSET %s""",
            (as_of, as_of, as_of, as_of, query.consumer_stage, as_of, as_of, as_of,
             query.origin, query.origin, query.created_from, query.created_from,
             query.created_until, query.created_until, query.completed_only, as_of,
             filtering, eligible_ids, limit, 0 if filtering else offset)).fetchall()
            page_facts = work_guidance(conn, {row[0] for row in rows}, as_of)
            gpt_judgments = (_gpt_work_judgments(conn, [row[0] for row in rows], as_of)
                             if query.consumer_stage == "gpt" else {})
            from service.queries.direct_execution import execution_metadata
            execution_facts = execution_metadata(conn, [row[0] for row in rows], as_of)
    return [dict(zip(("work_item_id", "task_id", "category", "origin", "created_at",
                      "resolution", "resolved_at", "routed", "paid_route",
                      "requested_model", "reported_model", "disposition_judgment"), row),
                 **({"disposition_judgment": gpt_judgments.get(row[0])}
                    if query.consumer_stage == "gpt" else {}),
                 guidance_group=page_facts[row[0]]["guidance_group"],
                 failure_categories=page_facts[row[0]]["failure_categories"],
                 repair_efforts=page_facts[row[0]]["repair_efforts"],
                 example_reuse_statuses=page_facts[row[0]]["example_reuse_statuses"],
                 **execution_facts[row[0]])
            for row in rows]


def read_work_item_detail(dsn: str, work_item_id: str, *, head_events: int = 100,
                          tail_events: int = 400) -> dict | None:
    """Metadata and content references only; the browser fetches bodies on selection."""
    from psycopg.rows import dict_row
    if head_events < 1 or tail_events < 1 or head_events + tail_events > 2000:
        raise ValueError("timeline event window out of bounds")
    event_window = head_events + tail_events
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            work = conn.execute("SELECT * FROM work_items WHERE work_item_id=%s", (work_item_id,)).fetchone()
            if work is None:
                return None
            from service.queries.direct_execution import execution_metadata, direct_usage
            execution_facts = execution_metadata(conn, [work_item_id], cutoff(CohortQuery()))[work_item_id]
            direct = execution_facts.get("direct_execution")
            direct_observations = direct_usage(conn, direct["execution_id"]) if direct else []
            calls = conn.execute("""SELECT c.call_id,c.parent_call_id,c.requested_model,c.created_at,
                c.execution_mode,
                EXISTS (SELECT 1 FROM events e WHERE e.call_id=c.call_id AND e.event_type='call.dispatched'
                    AND e.payload->>'destination'='skynet'
                    AND c.execution_mode='production') AS local_dispatch
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
            usage = conn.execute("""SELECT u.observation_id,u.model_execution_id,u.scope,
                u.is_cumulative,u.input_tokens,u.output_tokens,u.cache_read_input_tokens,
                u.cache_creation_input_tokens,u.provenance,u.price_basis
                FROM usage_observations u
                JOIN model_executions me ON me.model_execution_id=u.model_execution_id
                JOIN attempts a ON a.attempt_id=me.attempt_id
                JOIN calls c ON c.call_id=a.call_id
                WHERE c.work_item_id=%s ORDER BY c.created_at,u.observation_id""",
                (work_item_id,)).fetchall()
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
                d.output_result_id,
                CASE WHEN d.stage='gpt' THEN COALESCE(
                    d.actor_id='gpt-parent:' || c.parent_call_id
                    AND c.parent_call_id='gpt:' || w.task_id
                    AND r.delivered_at IS NOT NULL
                    AND d.input_sha256=content.sha256
                    AND (w.origin<>'observed' OR
                         (o.content_sha256=content.sha256 AND
                          d.details->>'provenance'='parent_mcp_report')),false)
                ELSE NULL END AS valid_parent_report
                FROM dispositions d JOIN results r ON r.result_id=d.result_id
                JOIN calls c ON c.call_id=r.call_id
                JOIN work_items w ON w.work_item_id=c.work_item_id
                LEFT JOIN content_objects content ON content.content_id=r.delivered_content_id
                LEFT JOIN runtime_disposition_obligations o ON o.result_id=r.result_id
                  AND o.consumer_call_id=c.parent_call_id AND o.stage='gpt'
                  AND o.part_id IS NULL
                WHERE c.work_item_id=%s
                ORDER BY d.recorded_at,d.disposition_id""", (work_item_id,)).fetchall()
            resolutions = conn.execute("""SELECT resolution_id,outcome,final_result_id,recorded_at
                FROM work_resolutions WHERE work_item_id=%s ORDER BY recorded_at,resolution_id""",
                (work_item_id,)).fetchall()
            events = conn.execute("""SELECT event_id,event_type,occurred_at,received_at,call_id,attempt_id,
                worker_instance_id,requested_model,reported_model,capture_completeness,payload
                FROM events WHERE work_item_id=%s ORDER BY occurred_at,event_id LIMIT %s""",
                (work_item_id, event_window + 1)).fetchall()
            events_truncated = len(events) > event_window
            if events_truncated:
                latest_events = conn.execute("""SELECT event_id,event_type,occurred_at,received_at,
                    call_id,attempt_id,worker_instance_id,requested_model,reported_model,
                    capture_completeness,payload FROM events WHERE work_item_id=%s
                    ORDER BY occurred_at DESC,event_id DESC LIMIT %s""",
                    (work_item_id, tail_events)).fetchall()
                events = events[:head_events] + list(reversed(latest_events))
            agent_events = conn.execute("""SELECT DISTINCT ON (call_id,event_type)
                call_id,event_type,occurred_at,payload
                FROM events WHERE work_item_id=%s AND event_type IN
                    ('attempt.status','attempt.uncertain','response.delta')
                ORDER BY call_id,event_type,occurred_at DESC,event_id DESC""",
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
            recoveries = conn.execute("""SELECT rr.call_id,rr.recorded_at,
                rr.evidence->'recovery_outcomes' AS outcomes
                FROM runtime_reconciliations rr
                JOIN runtime_jobs j ON j.call_id=rr.call_id
                WHERE j.work_item_id=%s AND rr.evidence ? 'recovery_outcomes'
                ORDER BY rr.recorded_at,rr.reconciliation_id""",
                (work_item_id,)).fetchall()
            ids = {identifier for row in messages for identifier in
                   (row["original_content_id"], row["effective_content_id"]) if identifier}
            ids.update(identifier for row in results for identifier in
                       (row["raw_content_id"], row["delivered_content_id"]) if identifier)
            content = conn.execute("""SELECT content_id,completeness,byte_length,content_type,
                encoding FROM content_objects WHERE content_id=ANY(%s)""", (list(ids),)).fetchall()
    work_data = dict(work)
    work_data.update(execution_facts)
    if resolutions:
        work_data["resolution"] = resolutions[-1]["outcome"]
        work_data["resolved_at"] = resolutions[-1]["recorded_at"]
    return {"work_item": work_data, "calls": list(map(dict, calls)),
            "direct_execution": direct, "direct_usage": direct_observations,
            "messages": list(map(dict, messages)), "results": list(map(dict, results)),
            "models": list(map(dict, models)), "usage": list(map(dict, usage)),
            "decisions": list(map(dict, decisions)),
            "supplied_decision_ids": [row["decision_id"] for row in supplied_decisions],
            "dispositions": list(map(dict, dispositions)),
            "resolutions": list(map(dict, resolutions)), "events": list(map(dict, events)),
            "agent_statuses": derive_agent_statuses(sorted(map(dict, agent_events),
                key=lambda row: row["occurred_at"])),
            "events_truncated": events_truncated,
            "events_gap_after": head_events if events_truncated else None,
            "guidance_packs": list(map(dict, packs)), "example_reviews": list(map(dict, reviews)),
            "evaluations": list(map(dict, evaluations)), "result_links": list(map(dict, links)),
            "recovery_decisions": list(map(dict, recoveries)),
            "content_states": {row["content_id"]: dict(row) for row in content}}


def read_output_preview(dsn: str, work_item_id: str, call_id: str, *,
                        role: str, max_chunks: int = 80,
                        max_bytes: int = 16384) -> dict | None:
    """Return a bounded tail of captured text for one selected call."""
    if role != "operator":
        raise PermissionError("operator access required for output preview")
    if not (1 <= max_chunks <= 500 and 1 <= max_bytes <= 65536):
        raise ValueError("output preview limits out of bounds")
    with psycopg.connect(dsn) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            call = conn.execute("SELECT 1 FROM calls WHERE work_item_id=%s AND call_id=%s",
                                (work_item_id, call_id)).fetchone()
            if not call:
                return None
            latest = conn.execute("""SELECT payload->>'message_id',occurred_at
                FROM events WHERE work_item_id=%s AND call_id=%s
                  AND event_type='response.delta'
                ORDER BY received_at DESC,producer_sequence DESC,event_id DESC LIMIT 1""",
                (work_item_id, call_id)).fetchone()
            rows = conn.execute("""SELECT mc.chunk_sequence,
                    substring(c.raw_bytes FROM greatest(1,octet_length(c.raw_bytes)-%s+1)),
                    c.content_type,c.byte_length
                FROM message_chunks mc LEFT JOIN content_objects c ON c.content_id=mc.content_id
                WHERE mc.message_id=%s
                ORDER BY mc.chunk_sequence DESC LIMIT %s""",
                (max_bytes, latest[0], max_chunks + 1)).fetchall() if latest else []
    selected = rows[:max_chunks]
    visible = [bytes(raw) for _, raw, content_type, _ in reversed(selected)
               if raw is not None and content_type and content_type.startswith("text/")]
    joined = b"".join(visible)
    original_bytes = sum(byte_length for _, raw, content_type, byte_length in selected
                         if raw is not None and content_type and content_type.startswith("text/"))
    truncated = len(rows) > max_chunks or original_bytes > max_bytes
    if len(joined) > max_bytes:
        joined = joined[-max_bytes:]
    return {"call_id": call_id, "text": joined.decode("utf-8", errors="replace"),
            "chunks_shown": len(visible), "missing_chunks": len(selected) - len(visible),
            "truncated": truncated,
            "last_event_at": latest[1] if latest else None}


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
            WHERE c.work_item_id=%s AND c.execution_mode='production'
            AND m.original_content_id IS NOT NULL
            AND m.role='user' AND m.direction='out'
            AND EXISTS (SELECT 1 FROM events dispatch WHERE dispatch.call_id=c.call_id
                AND dispatch.event_type='call.dispatched'
                AND dispatch.payload->>'destination'='skynet')
            ORDER BY c.created_at,m.message_id LIMIT 1""", (work_item_id,)).fetchone()
        local = conn.execute("""SELECT r.delivered_content_id FROM results r
            JOIN calls c ON c.call_id=r.call_id
            WHERE c.work_item_id=%s AND c.execution_mode='production'
            AND r.delivered_at IS NOT NULL
            AND EXISTS (SELECT 1 FROM events dispatch WHERE dispatch.call_id=c.call_id
                AND dispatch.event_type='call.dispatched'
                AND dispatch.payload->>'destination'='skynet')
            ORDER BY r.delivered_at,r.result_id LIMIT 1""", (work_item_id,)).fetchone()
        event_rows = conn.execute("""SELECT event_id,event_type,occurred_at,payload
            FROM events WHERE work_item_id=%s ORDER BY occurred_at,event_id LIMIT 201""",
            (work_item_id,)).fetchall()
    original = read_content(dsn, prompt[0], role="operator") if prompt else None
    response = read_content(dsn, local[0], role="operator") if local and local[0] else None
    return {"work_item_id": work[0], "task_id": work[1], "category": work[2],
            "origin": work[3], "original_prompt_content_id": prompt[0] if prompt else None,
            "local_response_content_id": local[0] if local else None,
            "original_prompt": original.decode("utf-8") if original else None,
            "local_response": response.decode("utf-8") if response else None,
            "events": [dict(zip(("event_id", "event_type", "occurred_at", "payload"), row))
                       for row in event_rows[:200]],
            "events_truncated": len(event_rows) > 200}
