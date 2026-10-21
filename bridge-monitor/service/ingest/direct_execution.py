"""Projection of direct observations, isolated from the Bridge scheduler."""

import uuid

from psycopg.types.json import Jsonb

from contracts.direct_execution import DirectRecordedEvent


def project_direct_task(conn, event):
    packet = DirectRecordedEvent.model_validate(event.payload)
    report, usage = packet.report, packet.usage
    work_id = "direct:" + str(uuid.uuid5(uuid.NAMESPACE_URL, report.execution_id))
    if (event.work_item_id != work_id or event.task_id != work_id
            or event.session_id != report.session_id or event.occurred_at != report.observed_at
            or event.call_id or event.model_execution_id):
        raise ValueError("direct event identity mismatch")
    if conn.execute("SELECT 1 FROM calls WHERE work_item_id=%s UNION ALL "
                    "SELECT 1 FROM delegation_decisions WHERE work_item_id=%s LIMIT 1",
                    (work_id, work_id)).fetchone():
        raise ValueError("direct task cannot overwrite Bridge work")
    old = conn.execute("SELECT producer_id,session_id,project_root FROM direct_executions "
                       "WHERE execution_id=%s FOR UPDATE", (report.execution_id,)).fetchone()
    if old and old != (event.producer_id, report.session_id, report.project_root):
        raise ValueError("direct execution identity conflict")
    if old:
        terminal = conn.execute("SELECT outcome FROM work_resolutions WHERE work_item_id=%s "
            "ORDER BY recorded_at DESC LIMIT 1", (work_id,)).fetchone()
        if terminal and report.status != terminal[0]:
            raise ValueError("direct terminal status conflict")
    conn.execute("INSERT INTO tasks(task_id,origin,scope,created_at) "
        "VALUES(%s,%s,%s,%s) ON CONFLICT(task_id) DO NOTHING",
        (work_id, event.origin, Jsonb(["direct_codex"]), report.observed_at))
    conn.execute("INSERT INTO work_items(work_item_id,task_id,origin,category,created_at) "
        "VALUES(%s,%s,%s,'direct_codex',%s) ON CONFLICT(work_item_id) DO NOTHING",
        (work_id, work_id, event.origin, report.observed_at))
    conn.execute("INSERT INTO direct_executions(execution_id,work_item_id,producer_id,"
        "session_id,project_root,first_observed_at) VALUES(%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT(execution_id) DO NOTHING",
        (report.execution_id, work_id, event.producer_id, report.session_id,
         report.project_root, report.observed_at))
    stored = conn.execute("SELECT producer_id,session_id,project_root FROM direct_executions "
                          "WHERE execution_id=%s", (report.execution_id,)).fetchone()
    if stored != (event.producer_id, report.session_id, report.project_root):
        raise ValueError("direct execution identity conflict")
    if report.status in ("completed", "failed", "cancelled"):
        conn.execute("INSERT INTO work_resolutions(resolution_id,work_item_id,outcome,recorded_at) "
            "VALUES(%s,%s,%s,%s) ON CONFLICT(resolution_id) DO NOTHING",
            (event.event_id, work_id, report.status, report.observed_at))
        conn.execute("UPDATE work_items SET resolved_at=GREATEST(resolved_at,%s) "
                     "WHERE work_item_id=%s", (report.observed_at, work_id))
    if usage is None:
        if packet.source_turn_id is not None:
            raise ValueError("source turn requires usage")
        return
    if usage.session_id != report.session_id or not packet.source_turn_id:
        raise ValueError("usage session identity and stable turn ID required")
    if all(value is None for value in (usage.input_tokens, usage.output_tokens,
            usage.cache_read_input_tokens, usage.cache_creation_input_tokens,
            usage.reasoning_output_tokens)):
        return  # No measurement exists yet; don't reserve the future turn's usage identity.
    observation_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
        f"direct-usage:{usage.session_id}:{packet.source_turn_id}"))
    evidence = {"source_sha256": usage.source_sha256,
                "session_id": usage.session_id, "source_turn_id": packet.source_turn_id,
                "reasoning_output_tokens": usage.reasoning_output_tokens,
                "attribution": "caller_selected_cli_turn", "provider": None,
                "reported_model": None}
    expected = (report.execution_id, usage.input_tokens, usage.output_tokens,
                usage.cache_read_input_tokens, usage.cache_creation_input_tokens, evidence)
    previous = conn.execute("SELECT direct_execution_id,input_tokens,output_tokens,"
        "cache_read_input_tokens,cache_creation_input_tokens,evidence FROM usage_observations "
        "WHERE observation_id=%s", (observation_id,)).fetchone()
    if previous and previous != expected:
        raise ValueError("usage receipt identity conflict")
    conn.execute("INSERT INTO usage_observations(observation_id,direct_execution_id,"
        "source_turn_id,scope,is_cumulative,input_tokens,output_tokens,"
        "cache_read_input_tokens,cache_creation_input_tokens,provenance,evidence) "
        "VALUES(%s,%s,%s,'turn',true,%s,%s,%s,%s,'codex_cli_receipt',%s) "
        "ON CONFLICT(observation_id) DO NOTHING",
        (observation_id, report.execution_id, packet.source_turn_id,
         usage.input_tokens, usage.output_tokens, usage.cache_read_input_tokens,
         usage.cache_creation_input_tokens, Jsonb(evidence)))
    stored = conn.execute("SELECT direct_execution_id,input_tokens,output_tokens,"
        "cache_read_input_tokens,cache_creation_input_tokens,evidence FROM usage_observations "
        "WHERE observation_id=%s", (observation_id,)).fetchone()
    if stored != expected:
        raise ValueError("usage receipt identity conflict")
