"""Actual telemetry-conflict recovery in an explicitly selected isolated database."""
from threading import Event
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import pytest
import psycopg
from .test_operation_lifecycle import db, event, put, counts, BASE
from contracts.events import EventEnvelope,EventType
from contracts.operations import candidate_event_hash
from contracts.ingest_conflicts import OperationConflict
from bridge.telemetry.spool import DurableSpool
from bridge.telemetry.emitter import TelemetryEmitter
from bridge.telemetry.recovery import SpoolRecovery,RecoveryError
from service.ingest.store import ingest_batch

class Collector:
    def __init__(self,dsn):self.dsn=dsn;self.calls=[];self.lose=False;self.offline=False
    def ingest_batch(self,producer,events,content):
        self.calls.extend(events)
        if self.offline:raise ConnectionError('lab unavailable')
        result=ingest_batch(self.dsn,producer,events,content,allow_operations=True)
        if self.lose:self.lose=False;raise ConnectionError('committed receipt lost')
        return result

def setup(tmp_path,dsn,item):
    s=DurableSpool(tmp_path/'recovery.sqlite',limit_bytes=100000,allow_operations=True)
    s.append(item);c=Collector(dsn)
    r=SpoolRecovery(s,c,producer_id='lab',allow_recovery=True)
    return s,c,r

def recover(r,h,code):
    return r.recover_head(h,recovery_id='review-1',actor_id='lab.operator',
        reason_code='confirmed_conflict',expected_conflict_code=code)

def legacy():
    return EventEnvelope(event_id='legacy-tail',event_type=EventType.TOOL_COMPLETED,schema_version=1,
        producer_id='lab',producer_incarnation='one',producer_sequence=1000,occurred_at=BASE,
        origin='synthetic',observation_layer='bridge',capture_completeness='complete',payload={'stream':'boundary'})

@pytest.mark.parametrize('mode,code',[
 ('event','event_id_conflict'),('sequence','producer_sequence_conflict'),
 ('identity','operation_identity_conflict')])
def test_actual_conflict_preserves_facts_and_recovers_mixed_tail(db,tmp_path,mode,code):
    original=event();put(db,[original])
    bad=(event(payload={'start_basis':'inferred'}) if mode=='event' else
         event(event_id='bad-sequence',operation_id='different') if mode=='sequence' else
         event('completed',2,call_id='late-call'))
    # The typed rejection comes from real ingest after its transaction unwinds.
    with pytest.raises(OperationConflict) as error:put(db,[bad])
    assert error.value.code==code and error.value.event_sha256==candidate_event_hash(bad)
    s,c,r=setup(tmp_path,db,bad);s.append(legacy());h=r.inspect_head()
    with sqlite3.connect(s.path) as conn:raw=conn.execute('SELECT event_json,content_json FROM entries ORDER BY seq LIMIT 1').fetchone()
    receipt=recover(r,h,code)
    assert receipt.action=='quarantined' and receipt.conflict_code==code
    assert s.pending_count()==1 and s.quarantined_count()==1
    evidence=r.evidence('review-1')
    assert (evidence.event_json,evidence.content_json)==raw
    emitter=TelemetryEmitter(s,c,producer_id='lab',incarnation='one',allow_operations=True)
    emitter.replay()
    assert s.pending_count()==0 and counts(db)==(2,2,1,1)
    assert emitter.health()['state']=='degraded' and not emitter.may_admit_new_work('reject_new_work')
    assert emitter.may_admit_new_work('allow_with_gaps') and s.known_gaps()==0
    assert recover(r,h,code)==receipt
    assert len(c.calls)==2 # One rejected candidate probe, one legacy delivery.
    with psycopg.connect(db) as conn:
        assert conn.execute("SELECT payload_hash FROM events WHERE event_id='e1'").fetchone()==(candidate_event_hash(original),)
        assert conn.execute('SELECT count(*) FROM operation_identities').fetchone()==(1,)

def test_lost_commit_is_not_quarantined_and_retry_acknowledges_duplicate(db,tmp_path):
    s,c,r=setup(tmp_path,db,event());h=r.inspect_head();c.lose=True
    with pytest.raises(RecoveryError,match='conflict_not_confirmed'):recover(r,h,'operation_identity_conflict')
    assert s.pending_count()==1 and s.quarantined_count()==0 and counts(db)==(1,1,1,0)
    receipt=recover(r,h,'operation_identity_conflict')
    assert receipt.action=='acknowledged' and receipt.collector_receipt=='duplicate'
    assert s.pending_count()==0 and s.quarantined_count()==0 and counts(db)==(1,1,1,0)
    assert r.evidence('review-1').receipt==receipt

def test_transient_failure_retains_head_then_accepts_real_commit(db,tmp_path):
    s,c,r=setup(tmp_path,db,event());h=r.inspect_head();c.offline=True
    with pytest.raises(RecoveryError,match='conflict_not_confirmed'):recover(r,h,'operation_identity_conflict')
    assert s.pending_count()==1 and counts(db)==(0,0,0,0)
    c.offline=False;receipt=recover(r,h,'operation_identity_conflict')
    assert receipt.action=='acknowledged' and receipt.collector_receipt=='committed'
    assert s.quarantined_count()==0 and counts(db)==(1,1,1,0)

def test_recovery_and_normal_replay_share_the_drain_fence(db,tmp_path):
    put(db,[event()])
    bad=event('completed',2,call_id='late')
    s,c,r=setup(tmp_path,db,bad);s.append(legacy());h=r.inspect_head()
    entered=Event();release=Event();replay_entered=Event();replay_done=Event()
    real=c.ingest_batch
    def blocked(producer,events,content):
        if events[0].event_id==bad.event_id:
            entered.set();assert release.wait(10)
        return real(producer,events,content)
    c.ingest_batch=blocked
    emitter=TelemetryEmitter(s,c,producer_id='lab',incarnation='one',allow_operations=True)
    def replay():
        replay_entered.set()
        try:emitter.replay()
        finally:replay_done.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(recover,r,h,'operation_identity_conflict')
        assert entered.wait(10)
        b=pool.submit(replay)
        assert replay_entered.wait(10)
        try:assert not replay_done.is_set()
        finally:release.set()
        assert a.result(timeout=10).action=='quarantined'
        b.result(timeout=10)
    assert len(c.calls)==2 and s.pending_count()==0 and s.quarantined_count()==1
    assert counts(db)==(2,2,1,1)
