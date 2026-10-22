"""Opt-in current-snapshot evidence reader. No runtime registration or writes."""
from datetime import datetime
import hashlib
import json
import psycopg
from psycopg.rows import dict_row
from contracts.measurement import COUNTERS, EXPORT_VERSION
from service.queries.operations import operation_snapshot
from service.queries.usage_reconciliation import reconcile_execution_usage, summarize_usage

MAX_BYTES=16*1024*1024
SAFE_REASONS=frozenset(('router_disabled','verifier_missing','capture_degraded',
    'unsupported_category','unsupported_output_contract','scope_unapproved',
    'task_snapshot_mismatch','case_digest_invalid','baseline_unverified',
    'verifier_withdrawn','agent_unavailable','shadow_budget_exhausted','shadow_queue_full',
    'context_unverified','prompt_too_large','source_too_large','outside_shadow_sample','shadow_candidate'))

def _json_default(value):
    if isinstance(value,datetime):return value.isoformat()
    raise TypeError('unsupported export value')

def encode_validation_export(report: dict) -> bytes:
    """Stable strict JSON. Caller chooses whether/where to save the returned bytes."""
    body=json.dumps(report,sort_keys=True,separators=(',',':'),ensure_ascii=True,
                    allow_nan=False,default=_json_default).encode('utf-8')
    if len(body)>MAX_BYTES:raise ValueError('export_too_large')
    return body

class _Rows:
    def __init__(self,conn,limit):self.conn=conn;self.limit=limit;self.used=0
    def take(self,count):
        if self.used+count>self.limit:raise ValueError('row_budget_exceeded')
        self.used+=count
    def query(self,sql,params=()):
        rows=self.conn.execute(sql+' LIMIT %s',(*params,self.limit-self.used+1)).fetchall()
        self.take(len(rows))
        return rows

def _identifier(value):return isinstance(value,str) and bool(value.strip()) and len(value)<=256

def _bad_parents(rows,key,parent):
    index={r[key]:r[parent] for r in rows}
    for start in index:
        seen=set();node=start
        while node is not None:
            if node not in index or node in seen:return True
            seen.add(node);node=index[node]
    return False

def build_validation_export(dsn,task_ids,*,source_ref,allow_export=False,max_rows=2000) -> dict:
    """Read selected recorded lineage in one REPEATABLE READ READ ONLY transaction.

    max_rows counts returned SQL rows, including metadata probes and re-read
    operation histories. An overflow rejects the whole report, never a partial one.
    """
    if type(allow_export) is not bool:raise TypeError('allow_export must be boolean')
    if not allow_export:raise ValueError('export_disabled')
    if not isinstance(task_ids,list) or not 1<=len(task_ids)<=16 or any(not _identifier(t) for t in task_ids):
        raise ValueError('bounded task IDs required')
    if len(set(task_ids))!=len(task_ids):raise ValueError('duplicate task IDs')
    if not _identifier(source_ref):raise ValueError('bounded source_ref required')
    if type(max_rows) is not int or not 1<=max_rows<=10000:raise ValueError('invalid max_rows')
    selected=sorted(task_ids)
    with psycopg.connect(dsn,row_factory=dict_row) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        rows=_Rows(conn,max_rows)
        stamp=rows.query('SELECT transaction_timestamp() AS observed_at')[0]['observed_at']
        tasks=rows.query('SELECT task_id,origin,created_at FROM tasks WHERE task_id=ANY(%s) ORDER BY task_id',(selected,))
        works=rows.query('SELECT work_item_id,task_id,parent_work_item_id,origin,category,created_at,resolved_at FROM work_items WHERE task_id=ANY(%s) ORDER BY work_item_id',(selected,))
        wids=[w['work_item_id'] for w in works]
        calls=rows.query('SELECT call_id,work_item_id,parent_call_id,parent_attempt_id,requested_model,created_at,execution_mode FROM calls WHERE work_item_id=ANY(%s) ORDER BY call_id',(wids,))
        cids=[c['call_id'] for c in calls]
        attempts=rows.query('SELECT attempt_id,call_id,attempt_number,status FROM attempts WHERE call_id=ANY(%s) ORDER BY attempt_id',(cids,))
        aids=[a['attempt_id'] for a in attempts]
        models=rows.query('SELECT model_execution_id,attempt_id,requested_model,reported_model FROM model_executions WHERE attempt_id=ANY(%s) ORDER BY model_execution_id',(aids,))
        mids=[m['model_execution_id'] for m in models]
        jobs=rows.query('SELECT call_id,task_id,work_item_id,pool,requested_model,policy_version,state,admitted_at,launch_claimed_at,reserved_tokens,execution_mode FROM runtime_jobs WHERE task_id=ANY(%s) OR call_id=ANY(%s) ORDER BY call_id',(selected,cids))
        decisions=rows.query('SELECT decision_id,work_item_id,child_call_id,route,eligibility,reason,policy_version,decided_at,decision_stage,execution_mode FROM delegation_decisions WHERE work_item_id=ANY(%s) OR child_call_id=ANY(%s) ORDER BY decision_id',(wids,cids))
        results=rows.query('SELECT result_id,call_id,attempt_id,delivered_at FROM results WHERE call_id=ANY(%s) ORDER BY result_id',(cids,))
        rids=[r['result_id'] for r in results]
        dispositions=rows.query('SELECT disposition_id,result_id,actor_id,stage,judgment,supersedes_id,recorded_at FROM dispositions WHERE result_id=ANY(%s) ORDER BY disposition_id',(rids,))
        direct=rows.query('SELECT execution_id,work_item_id,first_observed_at FROM direct_executions WHERE work_item_id=ANY(%s) ORDER BY execution_id',(wids,))
        fallback=rows.query('SELECT child_call_id,recipient_call_id,local_attempt_id,state,timeout_stage,elapsed_ms,policy_version,selected_model,paid_result_id,decision_id FROM runtime_fallback_transitions WHERE child_call_id=ANY(%s) OR recipient_call_id=ANY(%s) ORDER BY child_call_id',(cids,cids))
        recovery=rows.query('SELECT recovery_call_id,child_call_id,recipient_call_id FROM runtime_recovery_links WHERE recovery_call_id=ANY(%s) OR child_call_id=ANY(%s) OR recipient_call_id=ANY(%s) ORDER BY recovery_call_id',(cids,cids,cids))
        # Only allowlisted usage fields leave the events table. Other payloads,
        # raw content, requests, gate facts and free-text dispositions are not read.
        usage_fields=('observation_id','scope','is_cumulative','provenance',*COUNTERS)
        projection=','.join("'%s',payload->'%s'"%(k,k) for k in usage_fields)
        events=rows.query('SELECT event_id,event_type,payload_hash,task_id,work_item_id,call_id,attempt_id,model_execution_id,occurred_at,producer_id,producer_incarnation,origin,capture_completeness,unresolved_references, CASE WHEN event_type=\'usage.reported\' THEN jsonb_build_object('+projection+') ELSE NULL END AS usage FROM events WHERE task_id=ANY(%s) OR work_item_id=ANY(%s) OR call_id=ANY(%s) OR model_execution_id=ANY(%s) ORDER BY event_id',(selected,wids,cids,mids))
        windex={w['work_item_id']:w for w in works};cindex={c['call_id']:c for c in calls}
        aindex={a['attempt_id']:a for a in attempts};mindex={m['model_execution_id']:m for m in models}
        missing=sorted(set(selected)-{t['task_id'] for t in tasks})
        incomplete=bool(missing or direct or _bad_parents(works,'work_item_id','parent_work_item_id') or _bad_parents(calls,'call_id','parent_call_id'))
        for c in calls:
            parent_attempt=c['parent_attempt_id']
            if parent_attempt and (parent_attempt not in aindex or aindex[parent_attempt]['call_id']!=c['parent_call_id']):incomplete=True
        for job in jobs:
            c=cindex.get(job['call_id']);w=windex.get(job['work_item_id'])
            if not c or not w or c['work_item_id']!=job['work_item_id'] or w['task_id']!=job['task_id']:incomplete=True
        for item in (*fallback,*recovery):
            for k in ('child_call_id','recipient_call_id','recovery_call_id'):
                if item.get(k) and item[k] not in cindex:incomplete=True
        modeled_attempts={m['attempt_id'] for m in models}
        if any(a['attempt_id'] not in modeled_attempts for a in attempts):incomplete=True
        if any(j['launch_claimed_at'] and not any(a['call_id']==j['call_id'] for a in attempts) for j in jobs):incomplete=True
        observations={mid:[] for mid in mids};orphans=0
        for e in events:
            if e['event_type']!='usage.reported':continue
            m=mindex.get(e['model_execution_id'])
            if m is None:orphans+=1;incomplete=True;continue
            a=aindex[m['attempt_id']];c=cindex[a['call_id']];w=windex[c['work_item_id']]
            expected=dict(task_id=w['task_id'],work_item_id=w['work_item_id'],call_id=c['call_id'],attempt_id=a['attempt_id'])
            mismatch=any(e[k] is not None and e[k]!=v for k,v in expected.items())
            unresolved=bool(e['unresolved_references']) or mismatch
            if unresolved:incomplete=True
            observations[e['model_execution_id']].append(dict(e['usage'],
                **{k:e[k] for k in ('event_id','payload_hash','model_execution_id','occurred_at','producer_id','producer_incarnation','origin','capture_completeness')},unresolved=unresolved))
        for m in models:
            m['usage']=reconcile_execution_usage(m['model_execution_id'],observations[m['model_execution_id']])
            m['provider']=None
            m['identity_comparison']=('unknown' if not m['requested_model'] or not m['reported_model'] else 'same_identifier' if m['requested_model']==m['reported_model'] else 'different_identifiers')
        for d in decisions:
            reason=d.pop('reason')
            d['reason_code']=reason if reason in SAFE_REASONS else None
            d['reason_state']='known_code' if reason in SAFE_REASONS else 'missing' if reason is None else 'redacted_unclassified'
            d['reason_sha256']=hashlib.sha256(reason.encode()).hexdigest() if reason is not None else None
        for d in direct:d['usage_state']='unsupported_mapping'
        delivered=[r for r in results if r['delivered_at'] is not None]
        reported_accepted=0;unknown_acceptance=0
        for r in delivered:
            ds=[d for d in dispositions if d['result_id']==r['result_id'] and d['stage']=='gpt']
            # Report only one unambiguous claim. This is not parent-authenticated
            # final survival or an independent quality/acceptance verification.
            if len(ds)==1 and ds[0]['supersedes_id'] is None:
                reported_accepted+=int(ds[0]['judgment']=='accepted')
            else:unknown_acceptance+=1
        schema=rows.query("SELECT to_regclass('operation_event_facts') IS NOT NULL AND to_regclass('operation_identities') IS NOT NULL AS available")[0]['available']
        operations=dict(state='unavailable_schema',items=[])
        if schema:
            opids=rows.query('SELECT DISTINCT f.operation_id FROM operation_event_facts f JOIN events e USING(event_id) WHERE e.task_id=ANY(%s) OR e.work_item_id=ANY(%s) ORDER BY f.operation_id',(selected,wids))
            if len(opids)>16:raise ValueError('operation_budget_exceeded')
            operations['state']='available'
            for op in opids:
                remaining=rows.limit-rows.used
                if remaining<1:raise ValueError('row_budget_exceeded')
                try:snapshot=operation_snapshot(conn,op['operation_id'],allow_operations=True,max_events=remaining)
                except ValueError as exc:
                    if str(exc)=='operation history exceeds bound':raise ValueError('row_budget_exceeded') from exc
                    raise
                if snapshot is None:raise ValueError('operation_evidence_missing')
                rows.take(snapshot['observation_count'])
                if snapshot['identity']['task_id'] not in selected:raise ValueError('operation_cohort_mismatch')
                # Existing projection supplies timing/state. Free-form payload refs
                # and verification facts are omitted from this metadata export.
                operations['items'].append({k:snapshot[k] for k in ('identity','state','outcomes','unknown_observed','observation_count','observation_ids','capture_states','origins','elapsed_ms','timing_state')})
        manifest=[dict(event_id=e['event_id'],payload_hash=e['payload_hash']) for e in events]
        evidence=dict(events=manifest,sha256=hashlib.sha256(encode_validation_export(manifest)).hexdigest(),hash_basis='stored_event_hashes_not_provider_attestation')
        report=dict(schema_version=EXPORT_VERSION,source_ref=source_ref,
            snapshot=dict(kind='current_mvcc_snapshot',observed_at=stamp,isolation='repeatable_read',read_only=True),
            selected_task_ids=selected,missing_task_ids=missing,tasks=tasks,work_items=works,calls=calls,attempts=attempts,models=models,
            runtime_jobs=jobs,budget_basis='reservations_are_not_usage',decisions=decisions,results=results,dispositions=dispositions,
            direct_executions=direct,fallback_transitions=fallback,recovery_links=recovery,
            incomplete_lineage=incomplete,usage=summarize_usage([m['usage'] for m in models],incomplete_lineage=incomplete),
            usage_scope='recorded_model_execution_lineage',acceptance_basis='reported_unverified',
            denominators=dict(selected_tasks=len(selected),found_tasks=len(tasks),recorded_work_items=len(works),recorded_calls=len(calls),
                admitted_jobs=len(jobs),launch_claimed_jobs=sum(j['launch_claimed_at'] is not None for j in jobs),
                recorded_attempts=len(attempts),recorded_model_executions=len(models),usage_observations=sum(e['event_type']=='usage.reported' for e in events),
                orphan_usage_observations=orphans,delivered_results=len(delivered),reported_accepted_deliveries=reported_accepted,
                acceptance_unknown_deliveries=unknown_acceptance,direct_executions=len(direct)),
            operations=operations,evidence=evidence,rows_read=rows.used,row_budget=max_rows,
            capture_coverage='selected_recorded_lineage_only_global_gaps_unattributed',
            billed_cost=None,counterfactual_savings=None,cost_basis='no_verified_billing_or_counterfactual')
        # Enforce serialization and size before returning a usable report.
        return json.loads(encode_validation_export(report))
