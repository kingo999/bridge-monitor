"""Exact optional route enrichment and indexed, restart-safe reconciliation pages."""

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from bridge.classifier import store
from bridge.classifier.hook_capture import BINDING_VERSION, binding_hash
from bridge.classifier.hook_store import DB_OPTIONS
from bridge.classifier.policy import map_actual_route

PENDING = """state='completed' AND actual_route IS NULL AND work_item_id IS NOT NULL
    AND facts_provenance->>'capture_method'='route_work'"""
BINDING_KEYS = {'version','sha256','task_id','work_item_id','decision_id','paid_call_id',
                'task_sha256','baseline_sha256','verifier_ref','output_contract'}


def checked_binding(row):
    b = row['facts_provenance'].get('route_work_binding')
    if (not isinstance(b, dict) or set(b) != BINDING_KEYS
            or not all(isinstance(v, str) and 0 < len(v) <= 256 for v in b.values())
            or b['version'] != BINDING_VERSION or b['sha256'] != binding_hash(b)
            or b['task_id'] != row['task_id'] or b['work_item_id'] != row['work_item_id']
            or b['task_sha256'] != row['request_sha256']
            or b['baseline_sha256'] != row['source_sha256']):
        return None
    return b


def decisions(conn, rows):
    ids = [b['decision_id'] for row in rows if (b := checked_binding(row))]
    # Exact primary keys only. No work-item fallback, earliest/latest or alternate decision search.
    return {r['decision_id']: r for r in conn.execute("""SELECT d.decision_id,d.work_item_id,
        d.child_call_id,d.decision_stage,d.execution_mode,d.route,d.gate_facts,d.decided_at,
        c.work_item_id AS call_work_item_id,w.task_id,cw.task_id AS call_task_id
        FROM delegation_decisions d LEFT JOIN calls c ON c.call_id=d.child_call_id
        LEFT JOIN work_items w ON w.work_item_id=d.work_item_id
        LEFT JOIN work_items cw ON cw.work_item_id=c.work_item_id
        WHERE d.decision_id=ANY(%s)""", (ids,)).fetchall()}


def validate_link(row, decision):
    b = checked_binding(row)
    if not b:
        return {'actual_route_source': {'status': 'binding_incomplete'}}
    if decision is None:
        return {'actual_route_source': {'status': 'decision_missing', 'decision_id': b['decision_id']}}
    same = (decision['decision_id'] == b['decision_id']
        and decision['child_call_id'] == b['paid_call_id']
        and decision['work_item_id'] == decision['call_work_item_id'] == b['work_item_id']
        and decision['task_id'] == decision['call_task_id'] == b['task_id']
        and decision['decision_stage'] == 'parent_router' and decision['execution_mode'] == 'production'
        and isinstance(decision['gate_facts'], dict)
        and all(decision['gate_facts'].get(k) == b[k]
                for k in ('task_sha256','baseline_sha256','verifier_ref','output_contract')))
    if not same:
        return {'actual_route_source': {'status': 'binding_mismatch', 'decision_id': b['decision_id']}}
    route = map_actual_route(decision['route'])
    if route == 'unknown':
        return {'actual_route_source': {'status': 'unmapped_route', 'decision_id': b['decision_id']}}
    return {'actual_route': route, 'actual_route_observed_at': store.utcnow(),
        'actual_route_source': {'status': 'matched', 'table': 'delegation_decisions',
            'decision_id': b['decision_id'], 'binding_version': BINDING_VERSION,
            'binding_sha256': b['sha256'], 'checked_identity': b,
            'project_route': decision['route'], 'decided_at': decision['decided_at'].isoformat()}}


def immediate_link(dsn, row):
    with psycopg.connect(dsn, connect_timeout=1, options=DB_OPTIONS, row_factory=dict_row) as conn:
        found = decisions(conn, [row])
        b = checked_binding(row)
        return validate_link(row, found.get(b['decision_id']) if b else None)


def reconcile_page(dsn):
    """One transaction, at most 64 candidates and a fixed number of batched statements.

    Caller holds the existing runner lock. Progress survives a single-pass invocation;
    unresolved candidates advance the cursor. A finished sweep wraps on the next pass.
    """
    with psycopg.connect(dsn, connect_timeout=1, options=DB_OPTIONS,
                         row_factory=dict_row) as conn, conn.transaction():
        progress = conn.execute('SELECT * FROM classifier_route_link_progress WHERE version=%s FOR UPDATE',
                                (BINDING_VERSION,)).fetchone()
        if progress is None:
            raise ValueError('missing route-link progress')
        last = ((progress['last_at'], progress['last_id']) if progress['last_at'] is not None else None)
        upper = ((progress['upper_at'], progress['upper_id']) if progress['upper_at'] is not None else None)
        if ((progress['last_id'] is None) != (last is None)
                or (progress['upper_id'] is None) != (upper is None)
                or (last is not None and (upper is None or last > upper))):
            raise ValueError('corrupt route-link progress')
        if upper is None:
            top = conn.execute(f'SELECT captured_at,observation_id FROM classifier_observations WHERE {PENDING} '
                               'ORDER BY captured_at DESC,observation_id DESC LIMIT 1').fetchone()
            if not top:
                return dict(examined=0, linked=0, sweep_complete=True)
            upper = top['captured_at'], top['observation_id']
        lower_sql = ' AND (captured_at,observation_id)>(%s,%s)' if last else ''
        rows = conn.execute(f'SELECT observation_id,captured_at,task_id,work_item_id,request_sha256,'
            f'source_sha256,facts_provenance FROM classifier_observations WHERE {PENDING}'
            ' AND (captured_at,observation_id)<=(%s,%s)' + lower_sql +
            ' ORDER BY captured_at,observation_id LIMIT 64', (*upper, *(last or ()))).fetchall()
        found = decisions(conn, rows)
        fills = []
        for row in rows:
            b = checked_binding(row)
            fields = validate_link(row, found.get(b['decision_id']) if b else None)
            if 'actual_route' in fields:
                fills.append({**fields, 'observation_id': row['observation_id'], 'binding': b,
                              'actual_route_observed_at': fields['actual_route_observed_at'].isoformat()})
        linked = 0
        if fills:
            linked = conn.execute("""UPDATE classifier_observations o SET actual_route=f.actual_route,
                actual_route_source=f.actual_route_source,actual_route_observed_at=f.actual_route_observed_at
                FROM jsonb_to_recordset(%s) AS f(observation_id text,binding jsonb,actual_route text,
                    actual_route_source jsonb,actual_route_observed_at timestamptz)
                WHERE o.observation_id=f.observation_id AND o.actual_route IS NULL
                AND o.state='completed' AND o.facts_provenance->'route_work_binding'=f.binding""",
                (Jsonb(fills),)).rowcount
        tail = (rows[-1]['captured_at'], rows[-1]['observation_id']) if rows else None
        complete = not rows or len(rows) < 64 or tail == upper
        conn.execute('UPDATE classifier_route_link_progress SET last_at=%s,last_id=%s,upper_at=%s,upper_id=%s '
            'WHERE version=%s', (*(tail if not complete else (None,None)),
                                  *(upper if not complete else (None,None)), BINDING_VERSION))
        return dict(examined=len(rows), linked=linked, sweep_complete=complete)
