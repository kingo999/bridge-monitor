"""Durable usage statistics and dispatch ownership under the existing arbiter."""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
import psycopg
from psycopg.types.json import Jsonb
from bridge.runtime.admission import ARBITRATION_LOCK
from contracts.codex_claude import CodexClaudePolicy
from contracts.events import EventEnvelope, EventType

def _amount(value, *, positive=False):
    value = Decimal(value)
    if not value.is_finite() or value < 0 or (positive and value == 0):
        raise ValueError("invalid monetary amount")
    return value

def _utc(value):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    return value.astimezone(timezone.utc)

def _record(conn, key, kind, payload):
    normalized = json.loads(json.dumps(payload, default=str, sort_keys=True))
    digest = hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()
    old = conn.execute("SELECT payload_hash FROM cc_records WHERE record_id=%s", (key,)).fetchone()
    if old:
        if old[0] != digest:
            raise ValueError("record identity conflict")
        return
    conn.execute("INSERT INTO cc_records(record_id,kind,payload_hash,payload) VALUES(%s,%s,%s,%s)",
                 (key,kind,digest,Jsonb(normalized)))
    # Store and publish through the existing append-only event stream.
    from service.ingest.store import ingest_batch
    producer = "codex-claude-ledger"
    sequence = conn.execute("SELECT COALESCE(max(producer_sequence),0)+1 FROM events "
                            "WHERE producer_id=%s AND producer_incarnation='v1'", (producer,)).fetchone()[0]
    event = EventEnvelope(event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, "cc-record:"+key)),
        event_type=EventType.CODEX_CLAUDE_RECORDED, schema_version=1,producer_id=producer,
        producer_incarnation="v1",producer_sequence=sequence,occurred_at=datetime.now(timezone.utc),
        origin="observed",observation_layer="bridge_runtime",capture_completeness="complete",
        payload={"kind":kind,**normalized})
    ingest_batch("",producer,[event],[],connection=conn)

def record_codex_claude_decision(dsn, *, task_id, work_item_id, prompt_sha256, decision):
    payload=decision.model_dump(mode="json")
    identity=str(uuid.uuid5(uuid.NAMESPACE_URL,"cc-decision:"+work_item_id))
    with psycopg.connect(dsn) as conn,conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)",(ARBITRATION_LOCK,))
        prior=conn.execute("SELECT task_id,prompt_sha256,payload FROM cc_decisions WHERE work_item_id=%s",
                           (work_item_id,)).fetchone()
        if prior:
            if prior!=(task_id,prompt_sha256,payload):
                raise ValueError("decision identity conflict")
            return identity
        conn.execute("INSERT INTO cc_decisions(decision_id,work_item_id,task_id,prompt_sha256,payload) "
                     "VALUES(%s,%s,%s,%s,%s)",(identity,work_item_id,task_id,prompt_sha256,Jsonb(payload)))
        _record(conn,identity,"decision",{"decision_id":identity,"work_item_id":work_item_id,
            "task_id":task_id,"prompt_sha256":prompt_sha256,"decision":payload})
    return identity

def reserve_codex_claude_call(dsn, *, call_id, work_item_id, workflow_id, profile, amount,
                              policy: CodexClaudePolicy):
    amount=_amount(amount,positive=True)
    if profile not in policy.profiles:
        raise ValueError("unknown execution profile")
    with psycopg.connect(dsn) as conn,conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)",(ARBITRATION_LOCK,))
        old=conn.execute("SELECT work_item_id,workflow_id,profile,amount,state FROM cc_reservations WHERE call_id=%s",
                         (call_id,)).fetchone()
        if old:
            if old[:4]!=(work_item_id,workflow_id,profile,amount):
                raise ValueError("reservation identity conflict")
            return {"state":old[4],"call_id":call_id}
        held=conn.execute("SELECT count(*),COALESCE(sum(amount),0) FROM cc_reservations WHERE state='held'").fetchone()
        if held[0]>=policy.max_active_workers:
            return {"state":"rejected","reason":"worker_limit"}
        # The amount is a reference estimate, never a spending authorization or cap.
        # A held row protects single-worker ownership and uncertain execution only.
        conn.execute("INSERT INTO cc_reservations(call_id,work_item_id,workflow_id,profile,amount,state) "
                     "VALUES(%s,%s,%s,%s,%s,'held')",(call_id,work_item_id,workflow_id,profile,amount))
        _record(conn,"hold:"+call_id,"reservation",{"call_id":call_id,"work_item_id":work_item_id,
            "workflow_id":workflow_id,"profile":profile,"reference_amount":amount,
            "spending_mode":"statistics_only"})
        return {"state":"held","call_id":call_id}

def claim_codex_claude_dispatch(dsn, call_id):
    """One durable launch intention; a lost pre-admission owner must be reconciled."""
    with psycopg.connect(dsn) as conn,conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)",(ARBITRATION_LOCK,))
        hold=conn.execute("SELECT state FROM cc_reservations WHERE call_id=%s",(call_id,)).fetchone()
        if not hold or hold[0]!="held":return False
        key="dispatch:"+call_id
        if conn.execute("SELECT 1 FROM cc_records WHERE record_id=%s",(key,)).fetchone():return False
        _record(conn,key,"dispatch_claim",{"call_id":call_id})
        return True

def settle_codex_claude_receipt(dsn, *, call_id, receipt_id, amount, receipt_sha256,
                                rate_card_version, counters, observed_at):
    amount=_amount(amount)
    observed_at=_utc(observed_at)
    if len(receipt_sha256)!=64 or any(c not in "0123456789abcdef" for c in receipt_sha256):
        raise ValueError("receipt hash required")
    if not rate_card_version or any(type(v) is not int or v<0 for v in counters.values()):
        raise ValueError("scoped receipt counters required")
    with psycopg.connect(dsn) as conn,conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)",(ARBITRATION_LOCK,))
        old=conn.execute("SELECT call_id,amount,receipt_sha256,rate_card_version,counters FROM cc_receipts "
                         "WHERE receipt_id=%s",(receipt_id,)).fetchone()
        if old:
            if old!=(call_id,amount,receipt_sha256,rate_card_version,counters):
                raise ValueError("receipt identity conflict")
            return {"state":"settled","call_id":call_id}
        hold=conn.execute("SELECT state FROM cc_reservations WHERE call_id=%s FOR UPDATE",(call_id,)).fetchone()
        if not hold:raise ValueError("missing reservation")
        if hold[0]=="settled":raise ValueError("call already settled")
        # Late usage may settle a released hold; retain the actual charge instead of hiding it.
        conn.execute("INSERT INTO cc_receipts(receipt_id,call_id,amount,receipt_sha256,rate_card_version,counters,"
            "observed_at) VALUES(%s,%s,%s,%s,%s,%s,%s)",(receipt_id,call_id,amount,receipt_sha256,
            rate_card_version,Jsonb(counters),observed_at))
        conn.execute("UPDATE cc_reservations SET state='settled',terminal_at=now() WHERE call_id=%s",(call_id,))
        _record(conn,"receipt:"+receipt_id,"settlement",{"call_id":call_id,"receipt_id":receipt_id,
            "amount":amount,"receipt_sha256":receipt_sha256,"rate_card_version":rate_card_version,
            "counters":counters,"observed_at":observed_at.isoformat()})
    return {"state":"settled","call_id":call_id}

def release_codex_claude_call(dsn, call_id, *, termination_confirmed, no_charge_confirmed):
    if not termination_confirmed or not no_charge_confirmed:return False
    with psycopg.connect(dsn) as conn,conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)",(ARBITRATION_LOCK,))
        changed=conn.execute("UPDATE cc_reservations SET state='released',terminal_at=now() "
            "WHERE call_id=%s AND state='held' RETURNING call_id",(call_id,)).fetchone()
        if changed:_record(conn,"release:"+call_id,"release",{"call_id":call_id,
            "termination_confirmed":True,"no_charge_confirmed":True})
    return bool(changed)

def price_usage(profile, counters):
    """Claude receipt input/cache counters are disjoint; output includes reasoning."""
    fields=("input_tokens","output_tokens","cache_read_input_tokens","cache_creation_input_tokens")
    if any(type(counters.get(k)) is not int or counters[k]<0 for k in fields):
        return None
    # No cache discount: known reads cost standard input; writes use conservative 1-hour rate.
    return ((counters["input_tokens"]+counters["cache_read_input_tokens"])*profile.input_per_million
        + counters["cache_creation_input_tokens"]*profile.cache_write_per_million
        + counters["output_tokens"]*profile.output_per_million)/Decimal(1000000)
