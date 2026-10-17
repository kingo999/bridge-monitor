import hashlib
from datetime import timezone

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from contracts.content import ContentObject
from contracts.events import EventEnvelope, EventType
from service.ingest.projector import (missing_references, project_domain, project_status,
                                     validate_projectable_event)
from service.ingest.publication import publish


def _event_hash(event: EventEnvelope) -> str:
    import json
    data = json.dumps(event.model_dump(mode="json", exclude={"received_at"}),
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


EVENT_COLUMNS = ("event_id,event_type,schema_version,producer_id,producer_incarnation,"
                 "producer_sequence,occurred_at,received_at,origin,observation_layer,"
                 "capture_completeness,task_id,work_item_id,session_id,call_id,attempt_id,"
                 "model_execution_id,worker_instance_id,parent_event_id,requested_model,"
                 "reported_model,config_version,payload")


def _same_content(stored: tuple, incoming: tuple) -> bool:
    if stored == incoming:
        return True
    # Replay cannot restore bytes removed by the configured retention policy.
    return (stored[0] is None and stored[9] == "removed" and
            incoming[9] == "complete" and stored[1:9] == incoming[1:9] and
            stored[10:] == incoming[10:])


def _reconcile_unresolved(conn) -> None:
    # Scan every pending page before deciding that the fixed point has stalled.
    while True:
        progress = False
        after = None
        while True:
            with conn.cursor(row_factory=dict_row) as cursor:
                pending = cursor.execute(
                    f"SELECT {EVENT_COLUMNS} FROM events "
                    "WHERE cardinality(unresolved_references)>0 "
                    "AND unresolved_references[1] NOT LIKE 'projection_error:%%' "
                    "AND (%s::timestamptz IS NULL OR (occurred_at,event_id)>(%s,%s)) "
                    "ORDER BY occurred_at,event_id LIMIT 1000",
                    (after[0] if after else None, after[0] if after else None,
                     after[1] if after else None)).fetchall()
            if not pending:
                break
            after = (pending[-1]["occurred_at"], pending[-1]["event_id"])
            for row in pending:
                row["occurred_at"] = row["occurred_at"].astimezone(timezone.utc)
                row["received_at"] = row["received_at"].astimezone(timezone.utc)
                event = EventEnvelope.model_validate(row)
                missing = missing_references(conn, event)
                if not missing:
                    try:
                        with conn.transaction():
                            project_domain(conn, event)
                    except (ValueError, KeyError, TypeError, psycopg.IntegrityError,
                            psycopg.DataError) as exc:
                        conn.execute("UPDATE events SET unresolved_references=%s WHERE event_id=%s",
                                     ([f"projection_error:{type(exc).__name__}"], event.event_id))
                    else:
                        conn.execute("UPDATE events SET unresolved_references=ARRAY[]::text[] WHERE event_id=%s",
                                     (event.event_id,))
                        progress = True
                else:
                    conn.execute("UPDATE events SET unresolved_references=%s WHERE event_id=%s",
                                 (missing, event.event_id))
        if not progress:
            break


def ingest_batch(dsn: str, authenticated_producer: str,
                 events: list[EventEnvelope], content: list[ContentObject]) -> list[str]:
    if not authenticated_producer:
        raise PermissionError("authenticated producer required")
    if any(e.producer_id != authenticated_producer for e in events):
        raise PermissionError("producer identity mismatch")
    for event in events:
        validate_projectable_event(event)
    receipts: list[str] = []
    with psycopg.connect(dsn) as conn:
        with conn.transaction():
            seen_content = set()
            for item in content:
                if item.content_id in seen_content:
                    raise ValueError("duplicate content identity in batch")
                seen_content.add(item.content_id)
                old_content = conn.execute("""SELECT raw_bytes,byte_length,sha256,role,direction,
                    content_type,encoding,origin,capture_layer,completeness,message_id,block_order
                    FROM content_objects WHERE content_id=%s""", (item.content_id,)).fetchone()
                incoming = (item.raw_bytes, item.byte_length, item.sha256, item.role,
                            item.direction, item.content_type, item.encoding, item.origin,
                            item.capture_layer, item.completeness, item.message_id,
                            item.block_order)
                if old_content:
                    stored = ((bytes(old_content[0]) if old_content[0] is not None else None),
                              *old_content[1:])
                    if not _same_content(stored, incoming):
                        raise ValueError("content ID conflict")
                    continue
                inserted_content = conn.execute("""INSERT INTO content_objects
                    (content_id,raw_bytes,byte_length,sha256,role,direction,content_type,encoding,
                     origin,capture_layer,completeness,message_id,block_order)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(content_id) DO NOTHING RETURNING content_id""",
                    (item.content_id, item.raw_bytes, item.byte_length, item.sha256, item.role,
                     item.direction, item.content_type, item.encoding, item.origin,
                     item.capture_layer, item.completeness, item.message_id, item.block_order)).fetchone()
                if not inserted_content:
                    old_content = conn.execute("""SELECT raw_bytes,byte_length,sha256,role,direction,
                        content_type,encoding,origin,capture_layer,completeness,message_id,block_order
                        FROM content_objects WHERE content_id=%s""", (item.content_id,)).fetchone()
                    stored = ((bytes(old_content[0]) if old_content[0] is not None else None),
                              *old_content[1:])
                    if not _same_content(stored, incoming):
                        raise ValueError("content ID conflict")
            for event in events:
                digest = _event_hash(event)
                existing = conn.execute("SELECT payload_hash FROM events WHERE event_id=%s",
                                        (event.event_id,)).fetchone()
                if existing:
                    if existing[0] != digest:
                        raise ValueError("event ID conflict")
                    receipts.append("duplicate")
                    continue
                sequence = conn.execute("""SELECT event_id FROM producer_sequences
                    WHERE producer_id=%s AND producer_incarnation=%s AND producer_sequence=%s""",
                    (event.producer_id, event.producer_incarnation, event.producer_sequence)).fetchone()
                if sequence:
                    raise ValueError("producer sequence conflict")
                call_id = event.call_id or event.payload.get("call_id")
                unresolved = missing_references(conn, event)
                inserted_event = conn.execute("""INSERT INTO events
                    (event_id,event_type,schema_version,producer_id,producer_incarnation,
                     producer_sequence,occurred_at,received_at,origin,observation_layer,
                     capture_completeness,task_id,work_item_id,session_id,call_id,attempt_id,
                     model_execution_id,worker_instance_id,parent_event_id,requested_model,
                     reported_model,config_version,payload,payload_hash,unresolved_references)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,now(),%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(event_id) DO NOTHING RETURNING event_id""",
                    (event.event_id, event.event_type.value, event.schema_version,
                     event.producer_id, event.producer_incarnation, event.producer_sequence,
                     event.occurred_at, event.origin, event.observation_layer,
                     event.capture_completeness, event.task_id, event.work_item_id,
                     event.session_id, call_id, event.attempt_id, event.model_execution_id,
                     event.worker_instance_id, event.parent_event_id, event.requested_model,
                     event.reported_model, event.config_version, Jsonb(event.payload), digest,
                     unresolved)).fetchone()
                if not inserted_event:
                    existing = conn.execute("SELECT payload_hash FROM events WHERE event_id=%s",
                                            (event.event_id,)).fetchone()
                    if not existing or existing[0] != digest:
                        raise ValueError("event ID conflict")
                    receipts.append("duplicate")
                    continue
                sequence_insert = conn.execute("""INSERT INTO producer_sequences
                    (producer_id, producer_incarnation, producer_sequence, event_id)
                    VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING event_id""",
                    (event.producer_id, event.producer_incarnation,
                     event.producer_sequence, event.event_id)).fetchone()
                if not sequence_insert:
                    raise ValueError("producer sequence conflict")
                if not unresolved:
                    project_domain(conn, event)
                project_status(conn, event)
                publish(conn, event.event_id)
                receipts.append("committed")
            _reconcile_unresolved(conn)
    return receipts


def read_content(dsn: str, content_id: str, *, role: str) -> bytes | None:
    if role != "operator":
        raise PermissionError("operator content access required")
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT raw_bytes FROM content_objects WHERE content_id=%s",
                           (content_id,)).fetchone()
        return bytes(row[0]) if row and row[0] is not None else None
