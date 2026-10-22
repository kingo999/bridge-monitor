"""Measurement rules: synthetic local adapter capture, never a provider execution."""
import copy,json
from datetime import datetime,timezone,timedelta
from pathlib import Path
import pytest
from contracts.measurement import COUNTERS,MAPPING_ID,UsageSnapshot
from service.queries.usage_reconciliation import reconcile_execution_usage,summarize_usage

BASE=datetime(2026,10,22,tzinfo=timezone.utc)
def sample(n=1,**changes):
    value=dict(event_id='event-'+str(n),payload_hash='a'*64,observation_id='obs-'+str(n),
        model_execution_id='model',occurred_at=BASE+timedelta(seconds=n),producer_id='producer',
        producer_incarnation='one',origin='synthetic',capture_completeness='complete',
        unresolved=False,scope='execution',is_cumulative=True,provenance='claude_cli_result',
        input_tokens=4,output_tokens=80,cache_read_input_tokens=2440,cache_creation_input_tokens=12)
    value.update(changes);return value

def result(*rows):return reconcile_execution_usage('model',list(rows))

def test_actual_adapter_capture_maps_four_separate_fields(tmp_path):
    from bridge.adapters.claude_cli import ClaudeCLIAdapter
    from contracts.events import EventType
    from contracts.work import DispatchLease,TaskPacket
    frame=json.loads((Path(__file__).parents[1]/'fixtures/measurement/claude-result.json').read_text())
    class Runner:
        returncode=0
        def stream(self,command,prompt):yield json.dumps(frame)
    class Sink:
        def __init__(self):self.events=[]
        def emit(self,kind,**fields):self.events.append((kind,fields));return True
    sink=Sink()
    adapter=ClaudeCLIAdapter(Runner(),executable='never-launched',model='sonnet',mcp_config_path=tmp_path/'unused.json')
    adapter.run(DispatchLease(lease_id='lease',call_id='call',owner_incarnation='one',fencing_token=1,policy_version='fixture'),
        TaskPacket(task_id='task',work_item_id='work',category='analysis',scope=['analysis'],token_budget=1000,call_budget=2),'SYNTHETIC_PROMPT',sink)
    payload=next(fields['payload'] for kind,fields in sink.events if kind==EventType.USAGE_REPORTED)
    row=sample(**{k:payload[k] for k in ['scope','is_cumulative','provenance',*COUNTERS]})
    r=result(row)
    assert r['mapping_id']==MAPPING_ID and r['status']=='mapped'
    assert r['counters']==frame['usage']
    assert r['counters']['input_tokens']==4 # Never add cache counters.
    assert 'total_tokens' not in r['counters']

def test_strict_frozen_observation():
    model=UsageSnapshot.model_validate(sample())
    with pytest.raises(ValueError):model.input_tokens=5
    with pytest.raises(ValueError):UsageSnapshot.model_validate(sample(unexpected='ignored'))

@pytest.mark.parametrize('value',[True,-1,1.5,'5'])
def test_invalid_counters_stay_unknown(value):
    r=result(sample(input_tokens=value))
    assert r['reason']=='invalid_observation' and all(x is None for x in r['counters'].values())

def test_missing_and_zero_are_distinct():
    assert result()['reason']=='usage_missing'
    r=result(sample(input_tokens=0,output_tokens=0,cache_read_input_tokens=None,cache_creation_input_tokens=None))
    assert r['status']=='partial' and r['counters']['input_tokens']==0
    assert r['counters']['cache_read_input_tokens'] is None

def test_latest_cumulative_not_sum_and_out_of_order():
    a=sample();b=sample(2,input_tokens=10,output_tokens=90)
    r=result(b,a)
    assert r['counters']['input_tokens']==10 and r['counters']['output_tokens']==90
    assert r==result(a,b)

def test_partial_latest_is_not_filled_from_older_snapshot():
    r=result(sample(),sample(2,input_tokens=10,output_tokens=None))
    assert r['counters']['input_tokens']==10 and r['counters']['output_tokens'] is None

def test_identical_observation_id_is_deduplicated():
    a=sample();b=sample(2,observation_id=a['observation_id'])
    r=result(a,b)
    assert r['unique_observations']==1 and r['counters']['input_tokens']==4

def test_changed_observation_id_is_a_conflict():
    r=result(sample(),sample(2,observation_id='obs-1',input_tokens=10))
    assert r['reason']=='observation_id_conflict' and r['counters']['input_tokens'] is None

@pytest.mark.parametrize('field',COUNTERS)
def test_regressing_counters_are_not_maximized(field):
    a=sample();b=sample(2,**{field:a[field]-1})
    assert result(a,b)['reason']=='counter_regression'

def test_same_time_different_updates_are_ambiguous():
    assert result(sample(),sample(2,occurred_at=BASE+timedelta(seconds=1),input_tokens=10))['reason']=='ambiguous_update_order'

@pytest.mark.parametrize('change,reason',[
 ({'scope':'session'},'unsupported_scope'),
 ({'is_cumulative':False},'noncumulative_unsupported'),
 ({'provenance':'other'},'unmapped_source'),
 ({'capture_completeness':'partial'},'capture_incomplete'),
 ({'unresolved':True},'unresolved_lineage'),
 ({'model_execution_id':'other'},'execution_identity_mismatch')])
def test_unknown_sources_or_scope_are_explicit(change,reason):
    assert result(sample(**change))['reason']==reason

@pytest.mark.parametrize('change',[{'producer_id':'other'},{'producer_incarnation':'two'},{'origin':'imported'}])
def test_mixed_capture_owners_are_not_combined(change):
    assert result(sample(),sample(2,**change))['reason']=='mixed_capture_provenance'

def test_mixed_cumulative_and_other_sources_are_not_summed():
    assert result(sample(),sample(2,is_cumulative=False))['counters']['input_tokens'] is None
    assert result(sample(),sample(2,provenance='other'))['counters']['input_tokens'] is None

def test_aggregate_counts_execution_once_and_retains_missing_coverage():
    a=result(sample());b=reconcile_execution_usage('child',[])
    totals=summarize_usage([a,a,b])
    assert totals['input_tokens']==dict(known_subtotal=4,known_executions=1,expected_executions=2,total=None)
    assert totals['output_tokens']['known_subtotal']==80

def test_complete_known_model_total_still_separates_cache_fields():
    totals=summarize_usage([result(sample())])
    assert totals['input_tokens']['total']==4 and totals['cache_read_input_tokens']['total']==2440
    assert summarize_usage([result(sample())],incomplete_lineage=True)['input_tokens']['total'] is None
    assert summarize_usage([])['input_tokens']['total'] is None

def test_conflicting_execution_summary_is_not_double_counted():
    a=result(sample());b=copy.deepcopy(a);b['counters']['input_tokens']=100
    with pytest.raises(ValueError,match='duplicate_execution_summary'):summarize_usage([a,b])

def test_export_default_and_options_reject_before_connection(monkeypatch):
    from service.queries.validation_export import build_validation_export
    import psycopg
    calls=[]
    def forbidden(*a,**kw):calls.append(True);raise AssertionError('unexpected connection')
    monkeypatch.setattr(psycopg,'connect',forbidden)
    with pytest.raises(ValueError,match='export_disabled'):build_validation_export('unused',['t'],source_ref='fixture')
    for value in [1,'true',None]:
        with pytest.raises(TypeError):build_validation_export('unused',['t'],source_ref='fixture',allow_export=value)
    for ids in [[],['t','t'],[' '],[True],['x'*257],['t'+str(i) for i in range(17)]]:
        with pytest.raises(ValueError):build_validation_export('unused',ids,source_ref='fixture',allow_export=True)
    for value in [0,True,1.5,10001]:
        with pytest.raises(ValueError):build_validation_export('unused',['t'],source_ref='fixture',allow_export=True,max_rows=value)
    assert calls==[]

def test_export_encoding_is_deterministic_strict_and_bounded():
    from service.queries.validation_export import encode_validation_export
    a={'version':'bm04-evidence-v1','unicode':'東京','unknown':None,'count':0}
    assert encode_validation_export(a)==encode_validation_export(dict(reversed(list(a.items()))))
    assert json.loads(encode_validation_export(a))==a
    with pytest.raises(ValueError):encode_validation_export({'x':float('nan')})
    with pytest.raises(ValueError,match='export_too_large'):encode_validation_export({'x':'a'*(16*1024*1024)})
