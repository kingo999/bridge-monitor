import math

from contracts.events import EventEnvelope, EventType
from contracts.outcomes import DispositionRecord
from psycopg.types.json import Jsonb


CAPACITY_TYPES = {EventType.CAPACITY_RESERVED, EventType.CAPACITY_RELEASED,
                  EventType.CALL_QUEUED, EventType.CALL_DISPATCHED, EventType.ADMISSION_BLOCKED}
CAPTURE_TYPES = {EventType.CAPTURE_GAP, EventType.CAPTURE_DEGRADED,
                 EventType.CAPTURE_RECOVERED, EventType.SPOOL_PRESSURE}


def validate_projectable_event(event: EventEnvelope) -> None:
    """Reject malformed facts before they can be stored with deferred references."""
    p = event.payload

    def required(name: str, value: object) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{event.event_type.value} requires {name}")

    kind = event.event_type
    if kind == EventType.TASK_CREATED:
        required("task_id", event.task_id or p.get("task_id"))
    elif kind == EventType.WORK_ITEM_CREATED:
        required("work_item_id", event.work_item_id or p.get("work_item_id"))
        required("task_id", event.task_id or p.get("task_id"))
    elif kind == EventType.CALL_RECEIVED:
        required("call_id", event.call_id or p.get("call_id"))
        required("work_item_id", event.work_item_id)
    elif kind == EventType.CALL_DISPATCHED:
        required("call_id", event.call_id or p.get("call_id"))
        required("work_item_id", event.work_item_id)
        required("destination", p.get("destination"))
    elif kind == EventType.DELEGATION_DECIDED:
        required("decision_id", p.get("decision_id"))
        required("route", p.get("route"))
        required("work_item_id", event.work_item_id)
    elif kind == EventType.ATTEMPT_STARTED:
        required("attempt_id", event.attempt_id or p.get("attempt_id"))
        required("call_id", event.call_id or p.get("call_id"))
        number = p.get("attempt_number")
        if type(number) is not int or number < 1:
            raise ValueError("attempt.started requires positive attempt_number")
    elif kind == EventType.MODEL_SELECTED:
        required("model_execution_id", event.model_execution_id)
        required("attempt_id", event.attempt_id)
        required("requested_model", event.requested_model)
    elif kind == EventType.MODEL_REPORTED:
        required("model_execution_id", event.model_execution_id)
        required("reported_model", event.reported_model)
    elif kind == EventType.USAGE_REPORTED:
        required("model_execution_id", event.model_execution_id)
        required("observation_id", p.get("observation_id"))
        for field in ("input_tokens", "output_tokens"):
            value = p.get(field)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("usage tokens must be nonnegative integers")
    elif kind == EventType.RESPONSE_DELTA:
        required("message_id", p.get("message_id"))
        number = p.get("chunk_sequence")
        if type(number) is not int or number < 1:
            raise ValueError("response.delta requires positive chunk_sequence")
    elif kind == EventType.RESULT_DELIVERED:
        required("result_id", p.get("result_id"))
        required("call_id", event.call_id)
    elif kind == EventType.DISPOSITION_RECORDED:
        DispositionRecord.model_validate(p)
    elif kind == EventType.WORK_RESOLVED:
        required("resolution_id", p.get("resolution_id"))
        required("outcome", p.get("outcome"))
        required("work_item_id", event.work_item_id)
    if kind in CAPACITY_TYPES | CAPTURE_TYPES:
        for name in ("active", "queued", "held_uncertain", "blocked", "known_gaps"):
            if name in p and (type(p[name]) is not int or p[name] < 0):
                raise ValueError(f"{name} must be a nonnegative integer")
        if "capture_state" in p and p["capture_state"] not in ("healthy", "degraded", "unknown"):
            raise ValueError("invalid capture_state")
        pressure = p.get("spool_pressure")
        if pressure is not None and (type(pressure) not in (int, float)
                                     or not math.isfinite(pressure) or not 0 <= pressure <= 1):
            raise ValueError("spool_pressure must be between zero and one")


def missing_references(conn, event: EventEnvelope) -> list[str]:
    p = event.payload
    missing = []
    def require(table: str, column: str, value: str | None, label: str):
        if value and not conn.execute(f"SELECT 1 FROM {table} WHERE {column}=%s", (value,)).fetchone():
            missing.append(f"{label}:{value}")
    if event.event_type == EventType.WORK_ITEM_CREATED:
        require("tasks", "task_id", event.task_id or p.get("task_id"), "task")
        require("work_items", "work_item_id", p.get("parent_work_item_id"), "work_item")
    elif event.event_type == EventType.CALL_RECEIVED:
        require("work_items", "work_item_id", event.work_item_id, "work_item")
        require("calls", "call_id", p.get("parent_call_id"), "call")
    elif event.event_type == EventType.DELEGATION_DECIDED:
        require("work_items", "work_item_id", event.work_item_id, "work_item")
        require("calls", "call_id", event.call_id, "call")
    elif event.event_type == EventType.RESULT_DELIVERED:
        require("calls", "call_id", event.call_id, "call")
        require("attempts", "attempt_id", event.attempt_id, "attempt")
        for name in ("raw_content_id", "delivered_content_id"):
            require("content_objects", "content_id", p.get(name), "content")
    elif event.event_type == EventType.MODEL_SELECTED:
        require("attempts", "attempt_id", event.attempt_id, "attempt")
    elif event.event_type == EventType.MODEL_REPORTED:
        require("model_executions", "model_execution_id", event.model_execution_id, "model_execution")
    elif event.event_type == EventType.USAGE_REPORTED:
        require("model_executions", "model_execution_id", event.model_execution_id, "model_execution")
    elif event.event_type == EventType.DISPOSITION_RECORDED:
        require("results", "result_id", p.get("result_id"), "result")
        require("dispositions", "disposition_id", p.get("supersedes_id"), "disposition")
    elif event.event_type == EventType.WORK_RESOLVED:
        require("work_items", "work_item_id", event.work_item_id, "work_item")
        require("results", "result_id", p.get("final_result_id"), "result")
    elif event.event_type in (EventType.MESSAGE_SENT, EventType.MESSAGE_RECEIVED):
        require("calls", "call_id", event.call_id, "call")
        for name in ("original_content_id", "effective_content_id"):
            require("content_objects", "content_id", p.get(name), "content")
    elif event.event_type == EventType.RESPONSE_DELTA:
        require("content_objects", "content_id", p.get("content_id"), "content")
    elif event.event_type not in (EventType.TASK_CREATED, EventType.CAPACITY_RESERVED,
                                 EventType.CAPACITY_RELEASED, EventType.CAPTURE_GAP,
                                 EventType.CAPTURE_DEGRADED, EventType.CAPTURE_RECOVERED,
                                 EventType.SPOOL_PRESSURE):
        require("calls", "call_id", event.call_id or p.get("call_id"), "call")
    return missing


def project_status(conn, event: EventEnvelope) -> None:
    if event.event_type not in CAPACITY_TYPES | CAPTURE_TYPES:
        return
    allowed = {"active", "queued", "held_uncertain", "blocked", "capture_state",
               "known_gaps", "spool_pressure"}
    payload = {key: value for key, value in event.payload.items() if key in allowed}
    if not payload:
        return
    assignments = ", ".join(f"{key}=%s" for key in payload)
    conn.execute(f"UPDATE dashboard_status SET {assignments}, updated_at=now() WHERE singleton=true",
                 tuple(payload.values()))


def project_domain(conn, event: EventEnvelope) -> None:
    p = event.payload
    kind = event.event_type
    if kind == EventType.TASK_CREATED:
        conn.execute("""INSERT INTO tasks(task_id,origin,scope,created_at)
                     VALUES (%s,%s,%s,%s) ON CONFLICT(task_id) DO NOTHING""",
                     (event.task_id or p["task_id"], event.origin, Jsonb(p.get("scope", {})), event.occurred_at))
    elif kind == EventType.WORK_ITEM_CREATED:
        conn.execute("""INSERT INTO work_items(work_item_id,task_id,parent_work_item_id,origin,category,created_at)
                     VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(work_item_id) DO NOTHING""",
                     (event.work_item_id or p["work_item_id"], event.task_id or p.get("task_id"),
                      p.get("parent_work_item_id"), event.origin, p.get("category"), event.occurred_at))
    elif kind == EventType.CALL_RECEIVED:
        conn.execute("""INSERT INTO calls(call_id,work_item_id,parent_call_id,parent_attempt_id,requested_model,created_at)
                     VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(call_id) DO NOTHING""",
                     (event.call_id or p["call_id"], event.work_item_id, p.get("parent_call_id"),
                      p.get("parent_attempt_id"), event.requested_model, event.occurred_at))
    elif kind == EventType.ATTEMPT_STARTED:
        conn.execute("""INSERT INTO attempts(attempt_id,call_id,attempt_number,status)
                     VALUES (%s,%s,%s,'started') ON CONFLICT(attempt_id) DO NOTHING""",
                     (event.attempt_id or p["attempt_id"], event.call_id or p["call_id"],
                      p["attempt_number"]))
    elif kind == EventType.MODEL_SELECTED:
        conn.execute("""INSERT INTO model_executions
                     (model_execution_id,attempt_id,requested_model,model_evidence)
                     VALUES (%s,%s,%s,%s) ON CONFLICT(model_execution_id) DO NOTHING""",
                     (event.model_execution_id, event.attempt_id, event.requested_model,
                      Jsonb(p.get("evidence", {}))))
    elif kind == EventType.MODEL_REPORTED:
        conn.execute("""UPDATE model_executions SET reported_model=%s,
                     model_evidence=model_evidence || %s WHERE model_execution_id=%s""",
                     (event.reported_model, Jsonb(p.get("evidence", {})),
                      event.model_execution_id))
    elif kind == EventType.USAGE_REPORTED:
        conn.execute("""INSERT INTO usage_observations(observation_id,model_execution_id,scope,
                     is_cumulative,input_tokens,output_tokens,provenance,price_basis)
                     VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(observation_id) DO NOTHING""",
                     (p["observation_id"], event.model_execution_id, p.get("scope", "execution"),
                      p.get("is_cumulative", True), p.get("input_tokens"), p.get("output_tokens"),
                      p.get("provenance", "runtime_reported"), p.get("price_basis")))
    elif kind == EventType.DELEGATION_DECIDED:
        conn.execute("""INSERT INTO delegation_decisions
                     (decision_id,work_item_id,child_call_id,route,eligibility,reason,policy_version,
                      guidance_manifest,gate_facts,decided_at)
                     VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                     ON CONFLICT(decision_id) DO NOTHING""",
                     (p["decision_id"], event.work_item_id, event.call_id,
                      p["route"], p.get("eligibility", "unknown"), p.get("reason"),
                      event.config_version, Jsonb(p.get("guidance_manifest", {"state": "no_guidance"})),
                      Jsonb(p.get("gate_facts", {})), event.occurred_at))
    elif kind in (EventType.MESSAGE_SENT, EventType.MESSAGE_RECEIVED):
        message_id = p.get("message_id")
        if message_id:
            conn.execute("""INSERT INTO messages(message_id,call_id,role,direction,original_content_id,effective_content_id)
                         VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(message_id) DO NOTHING""",
                         (message_id, event.call_id, p.get("role", "user"),
                          p.get("direction", "out"), p.get("original_content_id"),
                          p.get("effective_content_id")))
    elif kind == EventType.RESPONSE_DELTA:
        message_id, sequence, content_id = p["message_id"], p["chunk_sequence"], p.get("content_id")
        if not isinstance(sequence, int) or sequence < 1:
            raise ValueError("chunk sequence must be positive")
        existing = conn.execute("""SELECT content_id FROM message_chunks
            WHERE message_id=%s AND chunk_sequence=%s""", (message_id, sequence)).fetchone()
        if existing and existing[0] != content_id:
            raise ValueError("chunk sequence conflict")
        conn.execute("""INSERT INTO message_chunks(message_id,chunk_sequence,content_id)
            VALUES (%s,%s,%s) ON CONFLICT(message_id,chunk_sequence) DO NOTHING""",
            (message_id, sequence, content_id))
    elif kind == EventType.RESULT_DELIVERED:
        conn.execute("""INSERT INTO results(result_id,call_id,attempt_id,raw_content_id,delivered_content_id,delivered_at)
                     VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(result_id) DO NOTHING""",
                     (p["result_id"], event.call_id, event.attempt_id,
                      p.get("raw_content_id"), p.get("delivered_content_id"), event.occurred_at))
    elif kind == EventType.DISPOSITION_RECORDED:
        conn.execute("""INSERT INTO dispositions
                     (disposition_id,result_id,actor_id,stage,judgment,reason,supersedes_id,recorded_at)
                     VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(disposition_id) DO NOTHING""",
                     (p["disposition_id"], p["result_id"], p["actor_id"], p["stage"],
                      p["judgment"], p.get("reason"), p.get("supersedes_id"), event.occurred_at))
    elif kind == EventType.WORK_RESOLVED:
        conn.execute("""INSERT INTO work_resolutions
                     (resolution_id,work_item_id,outcome,final_result_id,recorded_at)
                     VALUES (%s,%s,%s,%s,%s) ON CONFLICT(resolution_id) DO NOTHING""",
                     (p["resolution_id"], event.work_item_id, p["outcome"],
                      p.get("final_result_id"), event.occurred_at))
