"""Profile execution and receipt accounting; spend/token references never block."""
from datetime import datetime, timezone
from bridge.adapters.base import AdapterResult
from bridge.runtime.codex_claude import price_usage, settle_codex_claude_receipt, _record
from bridge.runtime.admission import ARBITRATION_LOCK
import psycopg

def execute_profile(dsn, adapter, lease, task, prompt, emitter, profile):
    if (profile.model_id not in getattr(adapter,"profile_models",())
            or not callable(getattr(adapter,"run_profile",None))):
        return AdapterResult(state="denied",uncertainty="profile_adapter_unavailable")
    result=adapter.run_profile(lease,task,prompt,emitter,profile=profile)
    if result.reported_model != profile.model_id:
        if result.receipt_sha256:
            with psycopg.connect(dsn) as conn,conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(%s)",(ARBITRATION_LOCK,))
                _record(conn,"unpriced:"+lease.call_id+":"+result.receipt_sha256,"unpriced_receipt",
                    {"call_id":lease.call_id,"receipt_sha256":result.receipt_sha256,
                     "requested_model":profile.model_id,"reported_model":result.reported_model,
                     "counters":result.usage,"reason":"model_drift_or_unreported_model"})
        return AdapterResult(state="uncertain",uncertainty="bounded_model_identity_unverified",terminated=result.terminated,cancelled=result.cancelled)
    amount=price_usage(profile,result.usage)
    if amount is not None and result.receipt_sha256:
        settle_codex_claude_receipt(dsn,call_id=lease.call_id,
            receipt_id=lease.call_id+":provider:1",amount=amount,
            receipt_sha256=result.receipt_sha256,rate_card_version=profile.rate_card_version,
            counters=result.usage,observed_at=datetime.now(timezone.utc))
    if (result.state=="completed" and (result.reported_model!=profile.model_id
            or result.local_tool_attempts or result.completion_claim is None)):
        return AdapterResult(state="uncertain",uncertainty="bounded_result_validation_failed",terminated=result.terminated,cancelled=result.cancelled)
    return result
