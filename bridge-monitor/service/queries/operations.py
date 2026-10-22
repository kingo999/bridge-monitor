"""Bounded projection of explicitly ingested operation facts; no client registration."""
from psycopg.rows import tuple_row
from contracts.event_reader import read_event
from contracts.operations import candidate_event_hash
from service.ingest.operations import IDENTITY_FIELDS

def operation_snapshot(conn, operation_id: str, *, allow_operations: bool = False,
                       max_events: int = 1000) -> dict | None:
    if type(allow_operations) is not bool:
        raise TypeError('allow_operations must be a boolean')
    if not allow_operations:
        raise ValueError('operation query disabled')
    if not isinstance(operation_id, str) or not operation_id.strip() or len(operation_id) > 256:
        raise ValueError('bounded operation ID required')
    if type(max_events) is not int or not 1 <= max_events <= 10000:
        raise ValueError('max_events must be an integer in 1..10000')
    with conn.cursor(row_factory=tuple_row) as cursor:
        rows = cursor.execute("""SELECT f.envelope,e.payload_hash FROM operation_event_facts f
            JOIN events e USING(event_id) WHERE f.operation_id=%s
            ORDER BY e.occurred_at,e.event_id LIMIT %s""",
            (operation_id,max_events+1)).fetchall()
    if len(rows) > max_events:
        raise ValueError('operation history exceeds bound')
    if not rows:
        return None
    events = []
    for body, digest in rows:
        decoded = read_event(body, allow_candidate=True)
        if decoded.kind != 'operation_v2' or candidate_event_hash(decoded.event) != digest:
            raise ValueError('operation evidence integrity failure')
        events.append(decoded.event)
    identity = {name: getattr(events[0],name) for name in IDENTITY_FIELDS}
    if any(any(getattr(e,n) != v for n,v in identity.items()) for e in events):
        raise ValueError('operation evidence identity conflict')
    if identity['operation_id'] != operation_id:
        raise ValueError('operation evidence identity conflict')
    starts = [e for e in events if e.event_type == 'operation.started']
    terminal = [e for e in events if e.event_type in {
        'operation.completed','operation.failed','operation.cancelled'}]
    outcomes = sorted({e.event_type.removeprefix('operation.') for e in terminal})
    unknown = any(e.event_type == 'operation.unknown' for e in events)
    capture = sorted({e.capture_completeness for e in events})
    origins = sorted({e.origin for e in events})
    state = ('conflicted' if len(outcomes)>1 else outcomes[0] if outcomes else
             'unknown' if unknown else 'started' if starts else 'unobserved')
    timing = 'available'
    if capture != ['complete']:
        timing = 'capture_incomplete'
    elif len(origins) != 1:
        timing = 'mixed_origins'
    elif unknown:
        timing = 'uncertainty_observed'
    elif len(starts) != 1 or starts[0].payload.start_basis != 'observed':
        timing = 'missing_observed_start'
    elif len(terminal) != 1:
        timing = 'ambiguous_outcome'
    elif terminal[0].occurred_at < starts[0].occurred_at:
        timing = 'clock_order'
    elapsed = ((terminal[0].occurred_at-starts[0].occurred_at).total_seconds()*1000
               if timing == 'available' else None)
    def facts(kind):
        return [dict(event_id=e.event_id, **e.payload.model_dump(mode='json')) for e in events
                if e.event_type == 'operation.'+kind]
    return dict(identity=identity,state=state,outcomes=outcomes,unknown_observed=unknown,
                observation_count=len(events),outcome_observation_count=len(terminal),
                observation_ids=[e.event_id for e in events],capture_states=capture,origins=origins,
                verification=facts('verification_recorded'),delivery=facts('delivery_recorded'),
                elapsed_ms=elapsed,timing_state=timing)
