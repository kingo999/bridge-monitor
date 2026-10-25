"""Provider-reported API equivalents, deduplicated per execution; never invoice claims."""
from collections import defaultdict
from decimal import Decimal,InvalidOperation

def provider_quotes(conn, as_of, *, created_from=None, exclude_calls=()):
    rows=conn.execute("SELECT e.model_execution_id,e.call_id,e.work_item_id,e.payload,e.occurred_at "
        "FROM events e JOIN calls c USING(call_id) JOIN content_objects content "
        "ON content.content_id=e.payload->>'content_id' "
        "WHERE e.event_type='usage.reported' AND e.model_execution_id IS NOT NULL "
        "AND c.execution_mode='production' AND e.capture_completeness='complete' "
        "AND cardinality(e.unresolved_references)=0 "
        "AND e.payload->>'provenance'='claude_cli_result' AND e.payload->>'scope'='execution' "
        "AND e.payload->>'is_cumulative'='true' AND e.payload->>'api_equivalent_usd' IS NOT NULL "
        "AND content.sha256=e.payload->>'receipt_sha256' "
        "AND e.occurred_at<=%s AND e.received_at<=%s "
        "AND (%s::timestamptz IS NULL OR e.occurred_at>=%s) "
        "ORDER BY e.occurred_at,e.event_id",(as_of,as_of,created_from,created_from)).fetchall()
    groups=defaultdict(list)
    for execution,call,work,payload,observed in rows:
        if call in exclude_calls:continue
        try:
            amount=Decimal(payload["api_equivalent_usd"])
            if not amount.is_finite() or amount<0:continue
        except (ValueError,TypeError,InvalidOperation):continue
        groups[execution].append({"call_id":call,"work_item_id":work,"amount":amount,
            "receipt_sha256":payload["receipt_sha256"],"counters":{key:payload.get(key) for key in
                ("input_tokens","output_tokens","cache_read_input_tokens","cache_creation_input_tokens")}})
    result=[]
    for execution,items in groups.items():
        if len({item["call_id"] for item in items})!=1:continue
        if any(items[i]["amount"]<items[i-1]["amount"] for i in range(1,len(items))):continue
        result.append({"model_execution_id":execution,**items[-1]})
    return result
