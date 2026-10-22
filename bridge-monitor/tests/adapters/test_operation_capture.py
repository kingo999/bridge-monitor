from datetime import datetime, timezone
import pytest
from bridge.telemetry.emitter import TelemetryEmitter
from bridge.telemetry.spool import DurableSpool
from contracts.events import EventType
from contracts.event_reader import read_event

class Collector:
    def __init__(self): self.events=[]; self.online=True
    def ingest_batch(self, producer, events, content):
        if not self.online: raise OSError('lab outage')
        self.events.extend(events)
        return ['committed']*len(events)

def make(tmp_path, *, enabled=True, limit=100000):
    collector=Collector()
    spool=DurableSpool(tmp_path/'queue.sqlite',limit_bytes=limit,allow_operations=enabled)
    emitter=TelemetryEmitter(spool,collector,producer_id='lab',incarnation='one',allow_operations=enabled)
    return emitter,spool,collector

def emit(emitter, name='operation.started', **extra):
    args=dict(operation_id='op',operation_kind='stub.read',task_id='t',work_item_id='w',
              payload={'start_basis':'observed'},origin='synthetic',capture_completeness='complete')
    args.update(extra)
    return emitter.emit_operation(name,**args)

@pytest.mark.parametrize('value',[1,'true',None])
def test_flags_are_strict(tmp_path,value):
    with pytest.raises(TypeError): DurableSpool(tmp_path/'bad.sqlite',limit_bytes=1000,allow_operations=value)
    spool=DurableSpool(tmp_path/'ok.sqlite',limit_bytes=1000)
    with pytest.raises(TypeError): TelemetryEmitter(spool,Collector(),producer_id='p',incarnation='i',allow_operations=value)

def test_default_emit_refuses_without_queue_change(tmp_path):
    e,s,c=make(tmp_path,enabled=False)
    with pytest.raises(ValueError,match='disabled'): emit(e)
    assert s.pending_count()==0 and not c.events

def test_enabled_emitter_requires_enabled_spool(tmp_path):
    s=DurableSpool(tmp_path/'off.sqlite',limit_bytes=10000)
    with pytest.raises(ValueError): TelemetryEmitter(s,Collector(),producer_id='p',incarnation='i',allow_operations=True)

def test_owned_envelope_and_validated_correlation(tmp_path):
    e,s,c=make(tmp_path)
    for kw in [{'event_id':'override'},{'producer_id':'override'},{'producer_sequence':99},{'observation_layer':'fake'},{'attempt_id':'a'}]:
        with pytest.raises(ValueError): emit(e,**kw)
    assert not c.events and s.pending_count()==0
    assert emit(e,call_id='c',attempt_id='a',decision_id='d')
    v=c.events[0]
    assert (v.producer_id,v.producer_incarnation,v.operation_id,v.call_id,v.decision_id)==('lab','one','op','c','d')
    assert v.origin=='synthetic' and v.observation_layer=='bridge'
    assert read_event(v.model_dump_json(),allow_candidate=True).kind=='operation_v2'

@pytest.mark.parametrize('name,payload',[
 ('operation.started',{'start_basis':'observed'}),('operation.completed',{}),
 ('operation.failed',{'failure_code':'stub','retryable':False}),
 ('operation.cancelled',{'cancelled_by':'user','reason_code':'stop'}),
 ('operation.unknown',{'unknown_reason':'capture_gap','last_observed_at':'2026-10-22T00:00:00Z'}),
 ('operation.verification_recorded',{'verification_id':'v','verifier_id':'stub','verdict':'passed','input_sha256':'a'*64,'evidence_refs':[]}),
 ('operation.delivery_recorded',{'delivery_id':'d','result_ref':'r','delivery_status':'delivered'})])
def test_all_variants(tmp_path,name,payload):
    e,s,c=make(tmp_path);assert emit(e,name,payload=payload)
    assert len(c.events)==1 and c.events[0].event_type==name and s.pending_count()==0

def test_restart_mixed_queue_and_disabled_emitter(tmp_path):
    e,s,c=make(tmp_path);c.online=False
    assert emit(e)
    assert e.emit(EventType.TOOL_COMPLETED,payload={'stream':'boundary'})
    assert s.pending_count()==2
    reopened=DurableSpool(s.path,limit_bytes=100000,allow_operations=True);c.online=True
    disabled=TelemetryEmitter(reopened,c,producer_id='lab',incarnation='one')
    disabled.replay()
    assert reopened.pending_count()==2 and not c.events and not disabled.collector_healthy
    enabled=TelemetryEmitter(reopened,c,producer_id='lab',incarnation='one',allow_operations=True)
    enabled.replay()
    assert reopened.pending_count()==0
    assert [x.event_type for x in c.events]==['operation.started',EventType.TOOL_COMPLETED]
    assert c.events[0].producer_sequence<c.events[1].producer_sequence

def test_overflow_records_existing_gap(tmp_path):
    e,s,c=make(tmp_path,limit=1)
    assert not emit(e)
    assert s.known_gaps()==1 and s.pending_count()==0 and not c.events
