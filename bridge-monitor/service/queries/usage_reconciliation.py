"""Pure per-execution cumulative reconciliation; no sums across snapshots or cache fields."""
from pydantic import ValidationError
from contracts.measurement import COUNTERS,MAPPING_ID,UsageSnapshot

def reconcile_execution_usage(execution_id: str, observations: list[dict]) -> dict:
    if not isinstance(execution_id,str) or not execution_id.strip() or len(execution_id)>256:
        raise ValueError('bounded execution ID required')
    if not isinstance(observations,list) or len(observations)>10000:
        raise ValueError('bounded observation list required')
    result=dict(model_execution_id=execution_id,status='unknown',reason='usage_missing',
                mapping_id=None,counters={field:None for field in COUNTERS},
                observations=len(observations),unique_observations=None,source_event_ids=[],origins=[])
    def unknown(reason):
        result['reason']=reason
        return result
    if not observations:return result
    try:rows=[UsageSnapshot.model_validate(row) for row in observations]
    except (ValidationError,TypeError,ValueError):return unknown('invalid_observation')
    result['source_event_ids']=sorted({row.event_id for row in rows})
    result['origins']=sorted({row.origin for row in rows})
    if any(row.model_execution_id!=execution_id for row in rows):return unknown('execution_identity_mismatch')
    if any(row.scope!='execution' for row in rows):return unknown('unsupported_scope')
    if any(not row.is_cumulative for row in rows):return unknown('noncumulative_unsupported')
    if any(row.provenance!='claude_cli_result' for row in rows):return unknown('unmapped_source')
    result['mapping_id']=MAPPING_ID
    if any(row.unresolved for row in rows):return unknown('unresolved_lineage')
    if any(row.capture_completeness!='complete' for row in rows):return unknown('capture_incomplete')
    if len({(row.producer_id,row.producer_incarnation,row.origin) for row in rows})!=1:
        return unknown('mixed_capture_provenance')
    unique={}
    def semantics(row):
        return tuple(getattr(row,key) for key in (*COUNTERS,'scope','is_cumulative','provenance',
                     'model_execution_id','producer_id','producer_incarnation','origin','capture_completeness'))
    for row in rows:
        previous=unique.get(row.observation_id)
        if previous is not None and semantics(previous)!=semantics(row):
            return unknown('observation_id_conflict')
        if previous is None or row.occurred_at<previous.occurred_at:unique[row.observation_id]=row
    result['unique_observations']=len(unique)
    ordered=sorted(unique.values(),key=lambda row:(row.occurred_at,row.observation_id))
    by_time={}
    for row in ordered:
        values=tuple(getattr(row,field) for field in COUNTERS)
        if row.occurred_at in by_time and by_time[row.occurred_at]!=values:
            return unknown('ambiguous_update_order')
        by_time[row.occurred_at]=values
    previous={}
    for row in ordered:
        for field in COUNTERS:
            value=getattr(row,field)
            if value is not None:
                if field in previous and value<previous[field]:return unknown('counter_regression')
                previous[field]=value
    counters={field:getattr(ordered[-1],field) for field in COUNTERS}
    if all(value is None for value in counters.values()):return unknown('missing_counters')
    result['counters']=counters
    result['status']='mapped' if all(value is not None for value in counters.values()) else 'partial'
    result['reason']=None if result['status']=='mapped' else 'partial_counters'
    return result

def summarize_usage(executions: list[dict], *, incomplete_lineage: bool=False) -> dict:
    if type(incomplete_lineage) is not bool:raise TypeError('incomplete_lineage must be boolean')
    unique={}
    for item in executions:
        key=item['model_execution_id']
        if key in unique and unique[key]!=item:raise ValueError('duplicate_execution_summary')
        unique[key]=item
    summaries=list(unique.values());result={}
    for field in COUNTERS:
        known=[item['counters'][field] for item in summaries if item['counters'][field] is not None]
        result[field]=dict(known_subtotal=sum(known),known_executions=len(known),
                          expected_executions=len(summaries),
                          total=sum(known) if summaries and len(known)==len(summaries) and not incomplete_lineage else None)
    return result
