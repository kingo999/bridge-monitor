"""Explicit operation support for the existing ingest transaction; no automatic setup."""
from contracts.events import EventEnvelope
from contracts.event_reader import read_event
from contracts.operations import OPERATION_EVENT_CLASSES, candidate_event_hash
from psycopg.types.json import Jsonb

IDENTITY_FIELDS = (
    'producer_id', 'producer_incarnation', 'operation_id', 'operation_kind',
    'task_id', 'work_item_id', 'session_id', 'call_id', 'attempt_id', 'decision_id',
    'parent_operation_id', 'model_execution_id', 'worker_instance_id',
)

def prepare_operation_events(events, *, allow_operations: bool):
    if type(allow_operations) is not bool:
        raise TypeError('allow_operations must be a boolean')
    prepared = []
    for event in events:
        if isinstance(event, EventEnvelope):
            prepared.append(event)
        elif isinstance(event, OPERATION_EVENT_CLASSES):
            if not allow_operations:
                raise ValueError('operation ingestion disabled')
            prepared.append(read_event(event.model_dump(mode='json'), allow_candidate=True).event)
        else:
            raise TypeError('validated event model required')
    return prepared

def lock_operations(conn, events) -> None:
    for operation_id in sorted({e.operation_id for e in events if not isinstance(e, EventEnvelope)}):
        conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                     ('bridge-monitor.operation:' + operation_id,))

def persist_operation(conn, event) -> str:
    digest = candidate_event_hash(event)
    existing = conn.execute('SELECT payload_hash FROM events WHERE event_id=%s',
                            (event.event_id,)).fetchone()
    if existing:
        fact = conn.execute('SELECT 1 FROM operation_event_facts WHERE event_id=%s',
                            (event.event_id,)).fetchone()
        if existing[0] != digest or not fact:
            raise ValueError('event ID conflict')
        return 'duplicate'
    body = event.model_dump(mode='json')
    identity = {name: body[name] for name in IDENTITY_FIELDS}
    matched = conn.execute("""INSERT INTO operation_identities(operation_id,identity) VALUES(%s,%s)
        ON CONFLICT(operation_id) DO UPDATE SET identity=operation_identities.identity
        WHERE operation_identities.identity=EXCLUDED.identity RETURNING operation_id""",
        (event.operation_id, Jsonb(identity))).fetchone()
    if not matched:
        raise ValueError('operation identity conflict')
    inserted = conn.execute("""INSERT INTO events
        (event_id,event_type,schema_version,producer_id,producer_incarnation,producer_sequence,
         occurred_at,received_at,origin,observation_layer,capture_completeness,task_id,work_item_id,
         session_id,call_id,attempt_id,model_execution_id,worker_instance_id,parent_event_id,
         requested_model,reported_model,config_version,payload,payload_hash)
        VALUES (%s,%s,%s,%s,%s,%s,%s,now(),%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(event_id) DO NOTHING RETURNING event_id""",
        (event.event_id,event.event_type,event.schema_version,event.producer_id,
         event.producer_incarnation,event.producer_sequence,event.occurred_at,event.origin,
         event.observation_layer,event.capture_completeness,event.task_id,event.work_item_id,
         event.session_id,event.call_id,event.attempt_id,event.model_execution_id,
         event.worker_instance_id,event.parent_event_id,event.requested_model,event.reported_model,
         event.config_version,Jsonb(body['payload']),digest)).fetchone()
    if not inserted:
        existing = conn.execute('SELECT payload_hash FROM events WHERE event_id=%s',
                                (event.event_id,)).fetchone()
        fact = conn.execute('SELECT 1 FROM operation_event_facts WHERE event_id=%s',
                            (event.event_id,)).fetchone()
        if not existing or existing[0] != digest or not fact:
            raise ValueError('event ID conflict')
        return 'duplicate'
    sequence = conn.execute("""INSERT INTO producer_sequences
        (producer_id,producer_incarnation,producer_sequence,event_id) VALUES(%s,%s,%s,%s)
        ON CONFLICT DO NOTHING RETURNING event_id""",
        (event.producer_id,event.producer_incarnation,event.producer_sequence,event.event_id)).fetchone()
    if not sequence:
        raise ValueError('producer sequence conflict')
    conn.execute('INSERT INTO operation_event_facts(event_id,operation_id,envelope) VALUES(%s,%s,%s)',
                 (event.event_id,event.operation_id,Jsonb(body)))
    return 'committed'
