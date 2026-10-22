"""Candidate lifecycle integration. Requires an explicitly selected isolated test database."""
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep
import psycopg
import pytest
from contracts.events import EventEnvelope, EventType
from contracts.event_reader import read_event
from contracts.operations import candidate_event_hash
from bridge.telemetry.spool import DurableSpool
from bridge.telemetry.emitter import TelemetryEmitter
from database.migrate import migrate
from service.ingest.store import ingest_batch, _event_hash
from service.queries.operations import operation_snapshot

ROOT=Path(__file__).parents[2]
BASE=datetime(2026,10,22,tzinfo=timezone.utc)
PAYLOADS={
 'started':{'start_basis':'observed'},'completed':{'result_ref':'r'},
 'failed':{'failure_code':'stub','retryable':False},
 'cancelled':{'cancelled_by':'user','reason_code':'stop'},
 'unknown':{'unknown_reason':'lost_contact','last_observed_at':BASE.isoformat()},
 'verification_recorded':{'verification_id':'v','verifier_id':'stub','verdict':'passed','input_sha256':'a'*64,'evidence_refs':[]},
 'delivery_recorded':{'delivery_id':'d','result_ref':'r','delivery_status':'delivered'}}

def event(kind='started',seq=1,**extra):
    raw=dict(schema_version=2,event_type='operation.'+kind,event_id='e'+str(seq),
        producer_id='lab',producer_incarnation='one',producer_sequence=seq,
        occurred_at=BASE+timedelta(seconds=seq),origin='synthetic',observation_layer='bridge',
        capture_completeness='complete',operation_id='op',operation_kind='stub.read',
        task_id='t',work_item_id='w',payload=PAYLOADS[kind])
    raw.update(extra)
    return read_event(raw,allow_candidate=True).event

def put(dsn,events,**kw):
    return ingest_batch(dsn,'lab',events,[],allow_operations=True,**kw)

def reset_lab_database(test_dsn):
    migrate(test_dsn)
    sql=(ROOT/'database/candidates/operation_lifecycle_v2.sql').read_text()
    with psycopg.connect(test_dsn) as conn:
        conn.execute(sql);conn.execute(sql)
        conn.execute('TRUNCATE events, producer_sequences, publication_stream, content_objects, operation_identities CASCADE')
    return test_dsn

@pytest.fixture
def db(test_dsn):
    return reset_lab_database(test_dsn)

def snapshot(dsn,**kw):
    with psycopg.connect(dsn) as conn:
        return operation_snapshot(conn,'op',allow_operations=True,**kw)

def counts(dsn):
    with psycopg.connect(dsn) as conn:
        return tuple(conn.execute('SELECT (SELECT count(*) FROM events), (SELECT count(*) FROM producer_sequences), (SELECT count(*) FROM operation_event_facts), (SELECT count(*) FROM publication_stream)').fetchone())

def test_disabled_ingest_rejects_before_connect(monkeypatch):
    calls=[]
    def no(*a,**k):calls.append(True);raise AssertionError('connection')
    monkeypatch.setattr(psycopg,'connect',no)
    with pytest.raises(ValueError,match='disabled'): ingest_batch('unused','lab',[event()],[])
    for value in [1,'true',None]:
        with pytest.raises(TypeError): ingest_batch('unused','lab',[],[],allow_operations=value)
    bad=event().model_copy(update={'operation_kind':''})
    with pytest.raises(ValueError): put('unused',[bad])
    with pytest.raises(ValueError): ingest_batch('unused','lab',[event()],[object()],allow_operations=True)
    with pytest.raises(PermissionError): ingest_batch('unused','other',[event()],[],allow_operations=True)
    assert calls==[]

def test_query_opt_in_and_bounds_before_connection():
    with pytest.raises(ValueError,match='disabled'): operation_snapshot(None,'op')
    for value in [1,'true',None]:
        with pytest.raises(TypeError): operation_snapshot(None,'op',allow_operations=value)
    for value in [0,True,1.5,10001]:
        with pytest.raises(ValueError): operation_snapshot(None,'op',allow_operations=True,max_events=value)
    for value in ['', ' ',None]:
        with pytest.raises(ValueError): operation_snapshot(None,value,allow_operations=True)

def test_candidate_storage_is_not_an_automatic_migration(db):
    from database.migrate import MIGRATIONS
    assert not list(MIGRATIONS.glob('*operation*'))
    with psycopg.connect(db) as conn:
        assert not conn.execute("SELECT version FROM schema_migrations WHERE version LIKE '%operation%'").fetchall()

def test_all_variants_are_retained_and_separate(db):
    events=[event(k,i) for i,k in enumerate(PAYLOADS,1)]
    assert put(db,events)==['committed']*7
    result=snapshot(db)
    assert result['state']=='conflicted'
    assert result['outcomes']==['cancelled','completed','failed']
    assert result['unknown_observed'] is True
    assert len(result['verification'])==len(result['delivery'])==1
    assert result['elapsed_ms'] is None and result['observation_count']==7
    assert counts(db)==(7,7,7,0)

def test_actual_emitter_spool_ingest_projection(db,tmp_path):
    class Collector:
        def ingest_batch(self,producer,events,content):
            return ingest_batch(db,producer,events,content,allow_operations=True)
    spool=DurableSpool(tmp_path/'actual.sqlite',limit_bytes=100000,allow_operations=True)
    emitter=TelemetryEmitter(spool,Collector(),producer_id='lab',incarnation='one',allow_operations=True)
    base=dict(operation_id='op',operation_kind='stub.read',task_id='t',work_item_id='w',
              origin='synthetic',capture_completeness='complete')
    for kind in ['started','completed','verification_recorded','delivery_recorded']:
        assert emitter.emit_operation('operation.'+kind,payload=PAYLOADS[kind],**base)
    result=snapshot(db)
    assert result['state']=='completed' and result['timing_state']=='available'
    assert result['elapsed_ms']>=0 and result['origins']==['synthetic']
    assert spool.pending_count()==0 and counts(db)==(4,4,4,0)
    assert len(result['verification'])==len(result['delivery'])==1

def test_committed_lost_receipt_replays_without_execution(db,tmp_path):
    class Collector:
        def __init__(self):self.receipts=[];self.lose=True
        def ingest_batch(self,producer,events,content):
            r=ingest_batch(db,producer,events,content,allow_operations=True);self.receipts.extend(r)
            if self.lose:self.lose=False;raise ConnectionError('receipt lost after commit')
            return r
    c=Collector();s=DurableSpool(tmp_path/'lost.sqlite',limit_bytes=100000,allow_operations=True)
    e=TelemetryEmitter(s,c,producer_id='lab',incarnation='one',allow_operations=True)
    execution_count=1 # Stub operation ran once, outside telemetry.
    assert e.emit_operation('operation.completed',operation_id='op',operation_kind='stub.read',
        task_id='t',work_item_id='w',payload={},origin='synthetic',capture_completeness='complete')
    assert s.pending_count()==1 and counts(db)==(1,1,1,0)
    e.replay()
    assert c.receipts==['committed','duplicate'] and s.pending_count()==0 and execution_count==1
    assert snapshot(db)['elapsed_ms'] is None

@pytest.mark.parametrize('order',[(0,1,2),(2,0,1),(1,2,0)])
def test_out_of_order_and_duplicate_identity(db,order):
    events=[event('started',1),event('completed',3),event('verification_recorded',2)]
    for i in order: assert put(db,[events[i]])==['committed']
    first=snapshot(db)
    assert put(db,events)==['duplicate']*3
    assert snapshot(db)==first
    assert first['state']=='completed' and first['elapsed_ms']==2000 and first['observation_count']==3

@pytest.mark.parametrize('kind,state',[('cancelled','cancelled'),('unknown','unknown'),('failed','failed')])
def test_outcome_states(db,kind,state):
    put(db,[event(),event(kind,2)])
    result=snapshot(db)
    assert result['state']==state
    assert result['unknown_observed']==(kind=='unknown')
    if kind=='unknown':assert result['elapsed_ms'] is None

@pytest.mark.parametrize('change,reason',[
 ({'capture_completeness':'partial'},'capture_incomplete'),
 ({'origin':'imported'},'mixed_origins'),
 ({'payload':{'start_basis':'inferred'}},'missing_observed_start')])
def test_start_capture_or_basis_limits_timing(db,change,reason):
    put(db,[event(**change),event('completed',2)])
    result=snapshot(db)
    assert result['state']=='completed' and result['elapsed_ms'] is None and result['timing_state']==reason

def test_clock_conflict_and_unknown_remain_visible(db):
    put(db,[event('completed',1),event('started',3)])
    assert snapshot(db)['timing_state']=='clock_order'
    put(db,[event('unknown',4)])
    r=snapshot(db)
    assert r['state']=='completed' and r['unknown_observed'] and r['elapsed_ms'] is None

def test_repeated_outcomes_never_inflate_operations_or_invent_duration(db):
    put(db,[event(),event('completed',2),event('completed',3)])
    r=snapshot(db)
    assert r['state']=='completed' and r['outcome_observation_count']==2 and r['elapsed_ms'] is None

def test_verification_and_delivery_do_not_establish_execution_success(db):
    put(db,[event('verification_recorded',1),event('delivery_recorded',2)])
    r=snapshot(db)
    assert r['state']=='unobserved' and r['outcomes']==[] and r['elapsed_ms'] is None
    assert len(r['verification'])==len(r['delivery'])==1

def test_same_id_mutation_and_atomic_batch_rollback(db):
    original=event()
    put(db,[original])
    with pytest.raises(ValueError,match='event ID conflict'):
        put(db,[event('completed',2),event(payload={'start_basis':'inferred'})])
    assert counts(db)==(1,1,1,0)
    with psycopg.connect(db) as conn:
        h,body=conn.execute('SELECT e.payload_hash,f.envelope FROM events e JOIN operation_event_facts f USING(event_id)').fetchone()
    assert h==candidate_event_hash(original)==candidate_event_hash(read_event(body,allow_candidate=True).event)

def test_sequence_conflict_rolls_back_candidate_and_legacy(db):
    put(db,[event()])
    legacy=EventEnvelope(event_id='legacy',event_type=EventType.CAPTURE_GAP,schema_version=1,
        producer_id='lab',producer_incarnation='one',producer_sequence=99,occurred_at=BASE,
        origin='synthetic',observation_layer='bridge',capture_completeness='partial',payload={'gap_count':1,'known_gaps':1,'capture_state':'degraded'})
    with pytest.raises(ValueError,match='producer sequence conflict'):
        ingest_batch(db,'lab',[legacy,event('completed',1,event_id='different')],[],allow_operations=True)
    assert counts(db)==(1,1,1,0)

@pytest.mark.parametrize('field,value',[('task_id','other'),('work_item_id','other'),
 ('operation_kind','other'),('producer_incarnation','other'),('call_id','c')])
def test_immutable_operation_identity(db,field,value):
    put(db,[event()])
    with pytest.raises(ValueError,match='operation identity conflict'):
        put(db,[event('completed',2,**{field:value})])
    assert counts(db)==(1,1,1,0)

def test_candidate_does_not_modify_legacy_publication_or_status(db):
    with psycopg.connect(db) as conn:
        before=conn.execute('SELECT * FROM dashboard_status').fetchall()
        counter=conn.execute('SELECT * FROM publication_counter').fetchall()
    put(db,[event(),event('unknown',2)])
    with psycopg.connect(db) as conn:
        assert conn.execute('SELECT * FROM dashboard_status').fetchall()==before
        assert conn.execute('SELECT * FROM publication_counter').fetchall()==counter
    assert counts(db)[3]==0

def test_mixed_queue_retains_historical_hash_and_stream_boundary(db,tmp_path):
    raw=json.loads((ROOT/'tests/fixtures/operation_lifecycle/legacy-inputs.json').read_text())[0]
    # Use a contract-valid historical tool.completed without rewriting meaning.
    legacy=EventEnvelope(event_id='legacy',event_type=EventType.TOOL_COMPLETED,schema_version=1,
        producer_id='lab',producer_incarnation='one',producer_sequence=50,occurred_at=BASE,
        origin='synthetic',observation_layer='bridge',capture_completeness='complete',payload={'stream':'boundary'})
    before=_event_hash(legacy)
    assert ingest_batch(db,'lab',[event(),legacy],[],allow_operations=True)==['committed','committed']
    with psycopg.connect(db) as conn:
        row=conn.execute("SELECT payload_hash,payload FROM events WHERE event_id='legacy'").fetchone()
        assert row==(before,{'stream':'boundary'})
        assert conn.execute('SELECT event_id FROM publication_stream').fetchall()==[('legacy',)]
    assert snapshot(db)['state']=='started'

def test_history_bound_and_absent_operation(db):
    with psycopg.connect(db) as conn: assert operation_snapshot(conn,'absent',allow_operations=True) is None
    put(db,[event(),event('completed',2)])
    with pytest.raises(ValueError,match='history exceeds bound'):snapshot(db,max_events=1)

@pytest.mark.parametrize('mode',['same_id','sequence','identity'])
def test_concurrent_conflict_or_duplicate(db,mode):
    a=event()
    b=(a if mode=='same_id' else event('completed',1,event_id='other',operation_id='different')
       if mode=='sequence' else event('completed',2,task_id='other'))
    race=db+' application_name=operation_lifecycle_race'
    def write(item):
        try:return put(race,[item])[0]
        except ValueError as error:return str(error)
    with psycopg.connect(db) as gate:
        gate.execute('LOCK TABLE events IN SHARE MODE')
        with psycopg.connect(db,autocommit=True) as monitor,ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(write,item) for item in [a,b]]
            try:
                deadline=monotonic()+8
                while monotonic()<deadline:
                    waiting=monitor.execute("SELECT count(*) FROM pg_stat_activity WHERE application_name='operation_lifecycle_race' AND wait_event_type='Lock'").fetchone()[0]
                    if waiting==2:break
                    sleep(.02)
                assert waiting==2
            finally:gate.commit()
            results=[f.result(timeout=10) for f in futures]
    assert results.count('committed')==1
    assert ('duplicate' in results if mode=='same_id' else any('conflict' in r for r in results))
    assert counts(db)==(1,1,1,0)

@pytest.mark.parametrize('view',['detail_all','detail_window','inspect'])
def test_candidates_do_not_enter_legacy_history_windows(db,view):
    from service.queries.history import read_work_item_detail, inspect_exchange
    with psycopg.connect(db) as conn:
        conn.execute("INSERT INTO tasks(task_id,origin,created_at) VALUES('hist-task','synthetic',%s) ON CONFLICT DO NOTHING",(BASE,))
        conn.execute("INSERT INTO work_items(work_item_id,task_id,origin,created_at) VALUES('hist-work','hist-task','synthetic',%s) ON CONFLICT DO NOTHING",(BASE,))
    legacy=[EventEnvelope(event_id='hist-'+str(i),event_type=EventType.TOOL_COMPLETED,
        schema_version=1,producer_id='lab',producer_incarnation='one',producer_sequence=400+i,
        occurred_at=BASE+timedelta(seconds=i),origin='synthetic',observation_layer='bridge',
        capture_completeness='complete',task_id='hist-task',work_item_id='hist-work',payload={'stream':'boundary'}) for i in range(3)]
    ingest_batch(db,'lab',legacy,[])
    def read():
        if view=='inspect':return inspect_exchange(db,'hist-work',role='operator')
        return read_work_item_detail(db,'hist-work',**({'head_events':1,'tail_events':1} if view=='detail_window' else {}))
    before=read()
    put(db,[event('started',i,event_id='hist-op-'+str(i),operation_id='hist-op',
        task_id='hist-task',work_item_id='hist-work',occurred_at=BASE+timedelta(seconds=500+i)) for i in range(1,206)])
    after=read()
    assert after['events']==before['events']
    assert after['events_truncated']==before['events_truncated']
    with psycopg.connect(db) as conn:
        assert operation_snapshot(conn,'hist-op',allow_operations=True)['observation_count']==205

def test_permanent_conflict_retains_candidate_and_legacy_tail(db,tmp_path):
    put(db,[event(operation_id='blocked-op')])
    class Collector:
        def ingest_batch(self,producer,events,content):
            return ingest_batch(db,producer,events,content,allow_operations=True)
    s=DurableSpool(tmp_path/'conflict.sqlite',limit_bytes=100000,allow_operations=True)
    e=TelemetryEmitter(s,Collector(),producer_id='lab',incarnation='new',allow_operations=True)
    assert e.emit_operation('operation.completed',operation_id='blocked-op',operation_kind='stub.read',
        task_id='t',work_item_id='w',payload={},origin='synthetic',capture_completeness='complete',call_id='late')
    assert e.emit(EventType.TOOL_COMPLETED,payload={'stream':'boundary'})
    e.replay()
    assert s.pending_count()==2 and counts(db)==(1,1,1,0)
    assert s.known_gaps()==0 and e.health()['state']=='degraded'
    assert not e.may_admit_new_work('reject_new_work')
    assert s.pending_count()==2

def test_lab_reset_removes_stale_operation_identity(db):
    put(db,[event(operation_id='reset-op',task_id='other')])
    reset_lab_database(db)
    assert put(db,[event(operation_id='reset-op')])==['committed']
