"""Isolated, explicit one-head recovery. Fake collectors model telemetry receipts only."""
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import pytest
from bridge.telemetry.spool import DurableSpool
from bridge.telemetry.recovery import SpoolRecovery, RecoveryError, HeadIdentity
from contracts.ingest_conflicts import OperationConflict, CONFLICT_CODES
from contracts.event_reader import read_event
from contracts.operations import candidate_event_hash

FIXTURE=Path(__file__).parents[1]/'fixtures/operation_lifecycle/candidate-v2.json'
def candidate():
    raw=json.loads(FIXTURE.read_text())[0]
    raw.update(producer_id='lab',producer_incarnation='one',event_id='head',producer_sequence=1,
               task_id='task-東京',work_item_id='work',operation_id='operation')
    return read_event(raw,allow_candidate=True).event

class Collector:
    def __init__(self,mode='conflict'):self.mode=mode;self.calls=[]
    def ingest_batch(self,producer,events,content):
        self.calls.extend(events)
        e=events[0]
        if self.mode=='conflict':raise OperationConflict('operation_identity_conflict',candidate_event_hash(e))
        if self.mode=='wrong_hash':raise OperationConflict('operation_identity_conflict','f'*64)
        if self.mode=='wrong_code':raise OperationConflict('producer_sequence_conflict',candidate_event_hash(e))
        if self.mode=='text':raise ValueError('operation identity conflict')
        if self.mode=='offline':raise ConnectionError('untrusted sensitive detail')
        if self.mode=='ambiguous':return []
        if self.mode=='bad_receipt':return ['failed']
        return [self.mode]

def make(tmp_path,mode='conflict',**kw):
    s=DurableSpool(tmp_path/'queue.sqlite',limit_bytes=100000,allow_operations=True)
    s.append(candidate())
    c=Collector(mode)
    r=SpoolRecovery(s,c,producer_id='lab',allow_recovery=True,**kw)
    return s,c,r

def recover(r,h,**kw):
    values=dict(recovery_id='recover-1',actor_id='lab.operator',reason_code='reviewed_conflict',
                expected_conflict_code='operation_identity_conflict')
    values.update(kw)
    return r.recover_head(h,**values)

def exists(s):
    with sqlite3.connect(s.path) as db:
        return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='spool_recoveries'").fetchone())

def raw_head(s):
    with sqlite3.connect(s.path) as db:
        return db.execute('SELECT seq,event_json,content_json,size_bytes FROM entries ORDER BY seq LIMIT 1').fetchone()

@pytest.mark.parametrize('code,message',[
 ('event_id_conflict','event ID conflict'),('producer_sequence_conflict','producer sequence conflict'),
 ('operation_identity_conflict','operation identity conflict')])
def test_typed_conflict_preserves_valueerror_contract(code,message):
    error=OperationConflict(code,'a'*64)
    assert isinstance(error,ValueError) and str(error)==message
    assert error.code==code and error.event_sha256=='a'*64

def test_bad_typed_evidence_is_rejected():
    for code,h in [('unknown','a'*64),('event_id_conflict','wrong')]:
        with pytest.raises(ValueError):OperationConflict(code,h)

@pytest.mark.parametrize('value',[1,'true',None])
def test_recovery_flag_is_strict(tmp_path,value):
    s=DurableSpool(tmp_path/'x.sqlite',limit_bytes=10000)
    with pytest.raises(TypeError):SpoolRecovery(s,Collector(),producer_id='lab',allow_recovery=value)
    assert not exists(s)

def test_disabled_and_incompatible_recovery_create_no_schema(tmp_path):
    s=DurableSpool(tmp_path/'x.sqlite',limit_bytes=10000)
    r=SpoolRecovery(s,Collector(),producer_id='lab')
    with pytest.raises(RecoveryError,match='recovery_disabled'):r.inspect_head()
    with pytest.raises(RecoveryError,match='recovery_disabled'):recover(r,HeadIdentity(1,'a'*64,'b'*64))
    with pytest.raises(ValueError):SpoolRecovery(s,Collector(),producer_id='lab',allow_recovery=True)
    assert not exists(s)

@pytest.mark.parametrize('value',[0,True,1.1,100001])
def test_journal_bound_is_strict(tmp_path,value):
    s=DurableSpool(tmp_path/'x.sqlite',limit_bytes=100000,allow_operations=True)
    with pytest.raises(ValueError):SpoolRecovery(s,Collector(),producer_id='lab',allow_recovery=True,journal_limit_bytes=value)
    assert not exists(s)

def test_exact_raw_evidence_and_idempotent_retry(tmp_path):
    s,c,r=make(tmp_path)
    with sqlite3.connect(s.path) as db:
        row=raw_head(s);text=json.dumps(json.loads(row[1]),ensure_ascii=False,indent=3)
        db.execute('UPDATE entries SET event_json=?,size_bytes=? WHERE seq=?',(text,len(text.encode())+len(row[2].encode()),row[0]))
    original=raw_head(s);h=r.inspect_head()
    assert not exists(s)
    receipt=recover(r,h)
    assert receipt.action=='quarantined' and receipt.conflict_code=='operation_identity_conflict'
    assert receipt.event_sha256==candidate_event_hash(candidate())
    assert s.pending_count()==0
    evidence=r.evidence('recover-1')
    assert (evidence.event_json,evidence.content_json)==original[1:3]
    assert evidence.receipt==receipt
    assert recover(r,h)==receipt and len(c.calls)==1
    assert SpoolRecovery(s,c,producer_id='lab',allow_recovery=True).evidence('recover-1')==evidence

@pytest.mark.parametrize('mode',['text','offline','ambiguous','bad_receipt'])
def test_uncertain_or_textual_rejection_cannot_quarantine(tmp_path,mode):
    s,c,r=make(tmp_path,mode);h=r.inspect_head();before=raw_head(s)
    with pytest.raises(RecoveryError,match='conflict_not_confirmed'):recover(r,h)
    assert raw_head(s)==before and not exists(s)

@pytest.mark.parametrize('mode',['wrong_hash','wrong_code'])
def test_typed_evidence_must_match_head_and_expected_code(tmp_path,mode):
    s,c,r=make(tmp_path,mode);h=r.inspect_head()
    with pytest.raises(RecoveryError,match='conflict_evidence_mismatch'):recover(r,h)
    assert s.pending_count()==1 and not exists(s)

@pytest.mark.parametrize('mode',['committed','duplicate'])
def test_success_is_acknowledged_not_quarantined(tmp_path,mode):
    s,c,r=make(tmp_path,mode);receipt=recover(r,r.inspect_head())
    assert receipt.action=='acknowledged' and receipt.collector_receipt==mode and receipt.conflict_code is None
    assert s.pending_count()==0 and r.evidence('recover-1').event_json

def test_stale_selection_and_wrong_producer_refuse_before_probe(tmp_path):
    s,c,r=make(tmp_path);h=r.inspect_head()
    fake=HeadIdentity(h.sequence,h.raw_sha256,'a'*64)
    with pytest.raises(RecoveryError,match='head_changed'):recover(r,fake)
    wrong=SpoolRecovery(s,c,producer_id='other',allow_recovery=True)
    with pytest.raises(RecoveryError,match='producer_mismatch'):recover(wrong,h)
    assert not c.calls and not exists(s)

@pytest.mark.parametrize('overrides',[{'recovery_id':''},{'actor_id':' '},{'reason_code':'raw secret prose'},{'expected_conflict_code':'unknown'}])
def test_metadata_refused_before_probe(tmp_path,overrides):
    s,c,r=make(tmp_path)
    with pytest.raises(RecoveryError,match='invalid_metadata'):recover(r,r.inspect_head(),**overrides)
    assert not c.calls and not exists(s)

def test_recovery_id_reuse_cannot_target_tail(tmp_path):
    s,c,r=make(tmp_path);h=r.inspect_head();first=recover(r,h)
    tail=candidate().model_copy(update={'event_id':'tail','producer_sequence':2})
    s.append(tail);before=raw_head(s)
    with pytest.raises(RecoveryError,match='recovery_id_conflict'):recover(r,r.inspect_head())
    with pytest.raises(RecoveryError,match='recovery_id_conflict'):recover(r,h,actor_id='different')
    assert recover(r,h)==first and raw_head(s)==before and len(c.calls)==1

def test_full_journal_refuses_before_probe(tmp_path):
    s,c,r=make(tmp_path,journal_limit_bytes=1)
    with pytest.raises(RecoveryError,match='journal_full'):recover(r,r.inspect_head())
    assert not c.calls and s.pending_count()==1 and not exists(s)

def test_database_failure_rolls_back_retention_and_head_removal(tmp_path,monkeypatch):
    s,c,r=make(tmp_path);h=r.inspect_head();before=raw_head(s);real=s._connect
    class Fault:
        def __init__(self):self.conn=real()
        def __getattr__(self,name):return getattr(self.conn,name)
        def __enter__(self):self.conn.__enter__();return self
        def __exit__(self,*args):return self.conn.__exit__(*args)
        def execute(self,sql,*args,**kw):
            if sql.startswith('DELETE FROM entries'):raise OSError('simulated local write failure')
            return self.conn.execute(sql,*args,**kw)
    with monkeypatch.context() as patch:
        patch.setattr(s,'_connect',Fault)
        with pytest.raises(OSError):recover(r,h)
    assert raw_head(s)==before and not exists(s)

def test_lost_local_completion_returns_same_receipt_without_reprobe(tmp_path,monkeypatch):
    s,c,r=make(tmp_path);h=r.inspect_head();persist=r._persist
    def lost(*args,**kw):
        persist(*args,**kw)
        raise ConnectionError('local completion response lost')
    with monkeypatch.context() as patch:
        patch.setattr(r,'_persist',lost)
        with pytest.raises(ConnectionError):recover(r,h)
    assert s.pending_count()==0
    assert recover(r,h)==r.evidence('recover-1').receipt and len(c.calls)==1

def test_concurrent_same_recovery_probes_once(tmp_path):
    s,c,r=make(tmp_path);h=r.inspect_head()
    other=SpoolRecovery(s,c,producer_id='lab',allow_recovery=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(recover,obj,h) for obj in [r,other]]
        receipts=[f.result(timeout=15) for f in futures]
    assert receipts[0]==receipts[1] and len(c.calls)==1 and s.pending_count()==0

def test_retained_evidence_corruption_is_reported(tmp_path):
    s,c,r=make(tmp_path);recover(r,r.inspect_head())
    with sqlite3.connect(s.path) as db:db.execute("UPDATE spool_recoveries SET event_json='corrupted'")
    with pytest.raises(RecoveryError,match='journal_corrupt'):r.evidence('recover-1')
