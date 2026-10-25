"""Receipt-backed, filter-independent notices; no billing or quota inference."""
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
import psycopg
from psycopg.types.json import Jsonb
from contracts.codex_claude import CodexClaudePolicy, SubscriptionObservation
from bridge.runtime.admission import ARBITRATION_LOCK
from bridge.runtime.codex_claude import _record, _utc

def calendar_day_bounds(as_of):
    local=_utc(as_of).astimezone(ZoneInfo("Europe/Prague"))
    zone=local.tzinfo
    start=datetime.combine(local.date(),time.min,zone)
    end=datetime.combine(local.date()+timedelta(days=1),time.min,zone)
    return start.astimezone(timezone.utc),end.astimezone(timezone.utc)

def record_subscription_observation(dsn, observation: SubscriptionObservation, *, producer_id, trusted_producers):
    if producer_id not in trusted_producers:
        raise PermissionError("trusted provider telemetry producer required")
    body=observation.model_dump(mode="json")
    with psycopg.connect(dsn) as conn,conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)",(ARBITRATION_LOCK,))
        _record(conn,"subscription:"+observation.observation_id,"subscription_observation",
                {**body,"producer_id":producer_id})
        conn.execute("INSERT INTO cc_subscription_observations(observation_id,provider,window_id,consumed_percent,"
            "window_start,resets_at,observed_at,valid_until,producer_id,evidence_sha256) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(observation_id) DO NOTHING",
            (observation.observation_id,observation.provider,observation.window_id,observation.consumed_percent,
             observation.window_start,observation.resets_at,observation.observed_at,observation.valid_until,
             producer_id,observation.evidence_sha256))
    return {"state":"recorded","observation_id":observation.observation_id}

def query_usage_notices(dsn, *, as_of=None):
    as_of=_utc(as_of or datetime.now(timezone.utc))
    start,end=calendar_day_bounds(as_of)
    policy=CodexClaudePolicy()
    base={"definition_version":"usage-notices-v1","as_of":as_of.isoformat(),"notification_only":True,
        "daily":{"triggered":False,"known_subtotal_usd":None,"threshold_usd":str(policy.daily_notice_usd),
            "comparison":"gt","period_start":start.isoformat(),"period_end":end.isoformat(),
            "timezone":"Europe/Prague","basis":"USD API-equivalent","status":"unknown",
            "scope":"Observed settled Bridge receipts; reservations excluded; not total account spending",
            "receipt_count":0,"unknown_usage_calls":None},
        "subscription":{"triggered":False,"status":"unknown","reason":"No current trusted provider observation",
            "threshold_percent":policy.subscription_notice_percent,"comparison":"gte","windows":[]},
        "notices":[]}
    with psycopg.connect(dsn) as conn,conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        if not conn.execute("SELECT to_regclass('cc_receipts')").fetchone()[0]:
            base["available"]=False
            base["reason"]="Codex/Claude migration has not been applied"
            return base
        base["available"]=True
        total,count=conn.execute("SELECT COALESCE(sum(amount),0),count(*) FROM cc_receipts "
            "WHERE observed_at>=%s AND observed_at<%s AND observed_at<=%s AND recorded_at<=%s",
            (start,end,as_of,as_of)).fetchone()
        from service.queries.provider_cost import provider_quotes
        settled_calls={row[0] for row in conn.execute("SELECT call_id FROM cc_receipts WHERE recorded_at<=%s",(as_of,))}
        quotes=provider_quotes(conn,as_of,created_from=start,exclude_calls=settled_calls)
        total+=sum((quote["amount"] for quote in quotes),Decimal(0))
        count+=len(quotes)
        quoted_calls=[quote["call_id"] for quote in quotes]
        # Failed/cancelled attempts without receipts remain unknown, never zero-cost.
        unknown=conn.execute("SELECT count(DISTINCT e.call_id) FROM events e JOIN calls c USING(call_id) "
            "WHERE e.event_type='call.dispatched' AND e.payload->>'destination'='claude' "
            "AND c.execution_mode='production' AND e.occurred_at>=%s AND e.occurred_at<=%s "
            "AND e.received_at<=%s AND NOT EXISTS(SELECT 1 FROM cc_receipts r WHERE r.call_id=e.call_id "
            "AND r.recorded_at<=%s) AND NOT(e.call_id=ANY(%s))",
            (start,as_of,as_of,as_of,quoted_calls)).fetchone()[0]
        base["daily"].update(known_subtotal_usd=str(total),receipt_count=count,
            unknown_usage_calls=unknown,status="partial" if unknown else "observed_scope",
            triggered=total>policy.daily_notice_usd)
        if base["daily"]["triggered"]:
            base["notices"].append({"id":"daily:"+start.isoformat(),"condition":"daily_usd",
                "title":"Daily API-equivalent usage exceeds $10",
                "message":f"${total:.2f} observed today; threshold is strictly above $10.00.",
                "basis":"USD API-equivalent, not invoice charges","period_end":end.isoformat()})
        rows=conn.execute("SELECT DISTINCT ON(provider,window_id) provider,window_id,consumed_percent,"
            "window_start,resets_at,observed_at,valid_until FROM cc_subscription_observations "
            "WHERE observed_at<=%s AND recorded_at<=%s ORDER BY provider,window_id,observed_at DESC,recorded_at DESC",
            (as_of,as_of)).fetchall()
        for provider,window,percent,window_start,resets,observed,valid in rows:
            current=window_start<=as_of<min(resets,valid)
            entry={"provider":provider,"window_id":window,"consumed_percent":str(percent) if current else None,
                "window_start":window_start.isoformat(),"resets_at":resets.isoformat(),
                "observed_at":observed.isoformat(),"valid_until":valid.isoformat(),"status":"current" if current else "stale"}
            base["subscription"]["windows"].append(entry)
            if current:
                base["subscription"].update(status="current",reason=None)
                if percent>=policy.subscription_notice_percent:
                    base["subscription"]["triggered"]=True
                    base["notices"].append({"id":f"subscription:{provider}:{window}:{window_start.isoformat()}",
                        "condition":"subscription","title":"Subscription allowance at or above 80%",
                        "message":f"{provider}: {percent}% of {window} allowance consumed.",
                        "basis":"Provider-reported subscription allowance","period_end":resets.isoformat()})
    return base
