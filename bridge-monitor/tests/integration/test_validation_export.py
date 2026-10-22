"""Explicit BM-04 API against the caller-selected disposable PostgreSQL database."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
import psycopg
import pytest
from contracts.events import EventEnvelope, EventType
from service.ingest.store import ingest_batch
from service.queries.validation_export import build_validation_export, encode_validation_export
from tests.integration.test_operation_lifecycle import reset_lab_database, event, put

BASE=datetime(2026,10,22,tzinfo=timezone.utc)
SECRET='SYNTHETIC_PRIVATE_BODY_MUST_NOT_ESCAPE'

@pytest.fixture
def db(test_dsn):
    reset_lab_database(test_dsn)
    with psycopg.connect(test_dsn) as c:
        c.execute('TRUNCATE tasks CASCADE')
        c.execute('TRUNCATE runtime_jobs, runtime_task_budgets CASCADE')
    return test_dsn

def seed(db, *, second=False):
    with psycopg.connect(db) as c:
        for t in ['t','outside']:
            c.execute('INSERT INTO tasks(task_id,origin,created_at) VALUES(%s,%s,%s)',(t,'synthetic',BASE))
            w='w' if t=='t' else 'outside-w'
            c.execute('INSERT INTO work_items(work_item_id,task_id,origin,created_at) VALUES(%s,%s,%s,%s)',(w,t,'synthetic',BASE))
        for name,parent,mode in [('c',None,'production'),('child','c','shadow')] if second else [('c',None,'production')]:
            c.execute('INSERT INTO calls(call_id,work_item_id,parent_call_id,requested_model,created_at,execution_mode) VALUES(%s,%s,%s,%s,%s,%s)',(name,'w',parent,'sonnet',BASE,mode))
            c.execute('INSERT INTO attempts VALUES(%s,%s,1,%s)',('a-'+name,name,'completed'))
            c.execute('INSERT INTO model_executions(model_execution_id,attempt_id,requested_model,reported_model) VALUES(%s,%s,%s,%s)',('m-'+name,'a-'+name,'sonnet','claude-sonnet-5-5'))
        c.execute("INSERT INTO results(result_id,call_id,attempt_id,delivered_at) VALUES('r','c','a-c',%s)",(BASE,))
        c.execute("INSERT INTO dispositions(disposition_id,result_id,actor_id,stage,judgment,reason,recorded_at) VALUES('disp','r','parent','gpt','accepted',%s,%s)",(SECRET,BASE))
        c.execute("INSERT INTO delegation_decisions(decision_id,work_item_id,child_call_id,route,eligibility,reason,gate_facts,decided_at) VALUES('d','w','c','paid','eligible',%s,%s,%s)",(SECRET,json.dumps({'private':SECRET}),BASE))
        c.execute("INSERT INTO runtime_task_budgets(task_id,scope,call_budget,token_budget) VALUES('t','{}',5,9000)")
        c.execute("INSERT INTO runtime_jobs(call_id,task_id,work_item_id,pool,request,policy_version,state,queue_deadline_at,reserved_tokens,launch_claimed_at) VALUES('c','t','w','claude',%s,'fixture-policy','completed',%s,9000,%s)",(json.dumps({'prompt':SECRET}),BASE,BASE))

def usage(db, seq=1, model='m-c', **overrides):
    payload=dict(observation_id='u'+str(seq),scope='execution',is_cumulative=True,provenance='claude_cli_result',input_tokens=4,output_tokens=80,cache_read_input_tokens=2440,cache_creation_input_tokens=12)
    payload.update(overrides.pop('payload',{}))
    raw=dict(event_id='u-event-'+str(seq),event_type=EventType.USAGE_REPORTED,schema_version=1,producer_id='measurement',producer_incarnation='one',producer_sequence=seq,occurred_at=BASE+timedelta(seconds=seq),origin='synthetic',observation_layer='bridge',capture_completeness='complete',task_id='t',work_item_id='w',call_id='c' if model=='m-c' else 'child',attempt_id='a-c' if model=='m-c' else 'a-child',model_execution_id=model,payload=payload)
    raw.update(overrides)
    assert ingest_batch(db,'measurement',[EventEnvelope(**raw)],[])==['committed']

def report(db,**kw):return build_validation_export(db,['t'],source_ref='fixture-source',allow_export=True,**kw)

def fingerprints(db):
    with psycopg.connect(db) as c:
        return [c.execute('SELECT md5(coalesce(jsonb_agg(to_jsonb(s) ORDER BY to_jsonb(s)::text)::text,\'\')) FROM '+name+' s').fetchone()[0] for name in ['events','tasks','calls','model_executions','runtime_jobs','dispositions','publication_stream']]

def test_snapshot_separate_counters_redaction_and_no_writes(db):
    seed(db);usage(db)
    before=fingerprints(db);r=report(db)
    assert fingerprints(db)==before
    assert r['schema_version']=='bm04-evidence-v1'
    assert r['usage']['input_tokens']['total']==4
    assert r['usage']['cache_read_input_tokens']['total']==2440
    assert r['models'][0]['identity_comparison']=='different_identifiers'
    assert r['models'][0]['provider'] is None
    assert r['runtime_jobs'][0]['reserved_tokens']==9000
    assert r['denominators']['recorded_model_executions']==1
    assert r['denominators']['reported_accepted_deliveries']==1
    assert r['acceptance_basis']=='reported_unverified'
    assert r['decisions'][0]['reason_code'] is None
    assert r['decisions'][0]['reason_sha256']==hashlib.sha256(SECRET.encode()).hexdigest()
    assert SECRET not in encode_validation_export(r).decode()
    assert 'outside-w' not in encode_validation_export(r).decode()
    assert r['billed_cost'] is None and r['counterfactual_savings'] is None
    assert r['evidence']['hash_basis']=='stored_event_hashes_not_provider_attestation'

def test_cumulative_updates_count_execution_once_and_include_shadow(db):
    seed(db,second=True);usage(db);usage(db,2,payload={'input_tokens':10,'output_tokens':100})
    usage(db,3,model='m-child')
    r=report(db)
    assert r['usage']['input_tokens']['total']==14
    assert r['usage']['output_tokens']['total']==180
    assert len(r['models'])==2 and len(r['calls'])==2
    assert {c['execution_mode'] for c in r['calls']}=={'production','shadow'}
    assert r['denominators']['usage_observations']==3

@pytest.mark.parametrize('change,reason',[
    ({'capture_completeness':'partial'},'capture_incomplete'),
    ({'payload':{'provenance':'unmapped'}},'unmapped_source'),
    ({'task_id':'outside'},'unresolved_lineage')])
def test_unknown_usage_does_not_become_zero_or_complete_total(db,change,reason):
    seed(db);usage(db,**change);r=report(db)
    assert r['models'][0]['usage']['reason']==reason
    assert r['usage']['input_tokens']['total'] is None
    assert r['usage']['input_tokens']['known_executions']==0

def test_conflicting_observation_ids_not_hidden_by_projection(db):
    seed(db);usage(db);usage(db,2,payload={'observation_id':'u1','input_tokens':8})
    assert report(db)['models'][0]['usage']['reason']=='observation_id_conflict'

def test_missing_usage_and_selected_task_are_explicit(db):
    seed(db,second=True);usage(db)
    r=build_validation_export(db,['t','missing'],source_ref='s',allow_export=True)
    assert r['missing_task_ids']==['missing']
    assert r['usage']['input_tokens']['known_subtotal']==4
    assert r['usage']['input_tokens']['total'] is None
    assert r['incomplete_lineage']
    assert r['models'][1]['usage']['reason']=='usage_missing'

def test_row_budget_fails_closed(db):
    seed(db);usage(db)
    with pytest.raises(ValueError,match='row_budget_exceeded'):report(db,max_rows=5)

def test_cycle_and_external_parent_prevent_complete_totals(db):
    seed(db);usage(db)
    with psycopg.connect(db) as c:c.execute("UPDATE calls SET parent_call_id='c' WHERE call_id='c'")
    r=report(db)
    assert r['incomplete_lineage'] and r['usage']['input_tokens']['total'] is None

def test_existing_operation_projection_is_included_and_bounded(db):
    seed(db);usage(db)
    assert put(db,[event('started',21),event('completed',22)])==['committed']*2
    r=report(db)
    assert r['operations']['state']=='available'
    assert r['operations']['items'][0]['state']=='completed'
    assert r['operations']['items'][0]['elapsed_ms']==1000
    assert r['rows_read']<=2000
    with pytest.raises(ValueError,match='row_budget_exceeded'):report(db,max_rows=r['rows_read']-1)

def test_direct_records_are_visible_but_not_model_usage(db):
    seed(db);usage(db)
    with psycopg.connect(db) as c:
        c.execute("INSERT INTO direct_executions VALUES('direct','w','desktop','session',%s,%s)",(SECRET,BASE))
    r=report(db)
    assert r['direct_executions'][0]['usage_state']=='unsupported_mapping'
    assert r['usage']['input_tokens']['total'] is None
    assert SECRET not in encode_validation_export(r).decode()

def test_repeatable_read_read_only_is_established_before_data(db,monkeypatch):
    seed(db);usage(db)
    real=psycopg.connect;seen=[]
    class Proxy:
        def __init__(self,c):self.c=c
        def __getattr__(self,n):return getattr(self.c,n)
        def __enter__(self):self.c.__enter__();return self
        def __exit__(self,*a):return self.c.__exit__(*a)
        def execute(self,q,*a,**kw):
            result=self.c.execute(q,*a,**kw)
            if str(q).startswith('SET TRANSACTION'):
                assert self.c.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']=='on'
                assert self.c.execute('SHOW transaction_isolation').fetchone()['transaction_isolation']=='repeatable read'
                seen.append(True)
            if 'transaction_timestamp()' in str(q):
                with real(db) as other:other.execute("UPDATE model_executions SET reported_model='concurrent-change' WHERE model_execution_id='m-c'")
            return result
    monkeypatch.setattr(psycopg,'connect',lambda *a,**kw:Proxy(real(*a,**kw)))
    r=report(db)
    assert seen==[True]
    assert r['models'][0]['reported_model']=='claude-sonnet-5-5'

def test_manifest_is_deterministic_for_stable_snapshot(db):
    seed(db);usage(db)
    a=report(db);b=report(db)
    assert a['evidence']==b['evidence']
    assert a['snapshot']['kind']=='current_mvcc_snapshot'
    assert a['capture_coverage']=='selected_recorded_lineage_only_global_gaps_unattributed'
