"""Passive documented Claude status-line allowance data; no provider API or transcript reads."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import ipaddress
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, field_validator
from contracts.codex_claude import SubscriptionObservation

WINDOWS={"five_hour":timedelta(hours=5),"seven_day":timedelta(days=7)}

class FeedConfig(BaseModel):
    model_config=ConfigDict(extra="forbid")
    enabled: bool=False
    endpoint: str="http://127.0.0.1:8873/api/v1/subscription-usage"
    credential_env: str=Field(default="BRIDGE_CLAUDE_SUBSCRIPTION_TOKEN",pattern=r"^[A-Z][A-Z0-9_]+$")
    state_path: str="var/spool/claude-subscription.sqlite"

    @field_validator("endpoint")
    @classmethod
    def local_endpoint(cls,value):
        parsed=urlsplit(value)
        try:
            local=ipaddress.ip_address(parsed.hostname or "").is_loopback
            _ = parsed.port  # Validate a supplied port without resolving a host.
        except ValueError:
            local=False
        if (not local or parsed.scheme not in {"http","https"} or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or parsed.path!="/api/v1/subscription-usage"):
            raise ValueError("an explicit loopback subscription endpoint is required")
        return value

def _now(value):
    value=value or datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("aware observation timestamp required")
    return value.astimezone(timezone.utc)

def _connect(path):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(path,timeout=.2)
    conn.execute("CREATE TABLE IF NOT EXISTS observations (fingerprint TEXT PRIMARY KEY, "
        "window_id TEXT NOT NULL, observed_at TEXT NOT NULL, body TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0)")
    conn.execute("CREATE TABLE IF NOT EXISTS feed_sessions (session_sha TEXT PRIMARY KEY, "
        "api_ms INTEGER NOT NULL, last_seen TEXT NOT NULL)")
    return conn

def capture_statusline(payload,state_path,*,now=None):
    """First receipt of a changed window is fresh; identical callbacks never renew it.

    A new session first establishes an API-duration checkpoint. Only later observed
    API activity can admit new allowance data; cached initial snapshots stay unknown.
    The session ID is hashed, and its API-duration marker is never quota or billing.
    Only normalized allowance fields are kept in receipts. Window starts are derived
    from the documented 5-hour/7-day durations, not claimed as provider timestamps.
    No spend-limit/credit/context/session-cost field is a subscription allowance.
    """
    now=_now(now)
    if not isinstance(payload,dict):
        return []
    session=payload.get("session_id")
    cost=payload.get("cost")
    api_ms=cost.get("total_api_duration_ms") if isinstance(cost,dict) else None
    if not isinstance(session,str) or not 1<=len(session)<=200 or type(api_ms) is not int or not 0<=api_ms<2**63:
        return []
    session_sha=hashlib.sha256(session.encode("utf-8")).hexdigest()
    rate=payload.get("rate_limits")
    if not isinstance(rate,dict):
        rate={}
    result=[]
    with _connect(state_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        checkpoint=conn.execute("SELECT api_ms FROM feed_sessions WHERE session_sha=?",(session_sha,)).fetchone()
        fresh=checkpoint is not None and api_ms>checkpoint[0]
        conn.execute("INSERT INTO feed_sessions(session_sha,api_ms,last_seen) VALUES(?,?,?) "
            "ON CONFLICT(session_sha) DO UPDATE SET api_ms=max(api_ms,excluded.api_ms),last_seen=excluded.last_seen",
            (session_sha,api_ms,now.isoformat()))
        for window,duration in WINDOWS.items():
            value=rate.get(window)
            if not isinstance(value,dict):
                continue
            raw_percent=value.get("used_percentage");reset=value.get("resets_at")
            if type(raw_percent) not in (int,float) or type(reset) is not int:
                continue
            try:
                percent=Decimal(str(raw_percent))
                resets=datetime.fromtimestamp(reset,timezone.utc)
            except (InvalidOperation,ValueError,OverflowError,OSError):
                continue
            if not percent.is_finite() or not 0<=percent<=100 or not now<resets<=now+duration:
                continue
            evidence={"source":"claude_statusline_rate_limits","provider":"claude","window_id":window,
                "consumed_percent":str(percent.normalize()),"resets_at":resets.isoformat()}
            digest=hashlib.sha256(json.dumps(evidence,sort_keys=True,separators=(",",":")).encode()).hexdigest()
            previous=conn.execute("SELECT body FROM observations WHERE fingerprint=?",(digest,)).fetchone()
            if previous:
                result.append(SubscriptionObservation.model_validate_json(previous[0]))
                continue
            if not fresh:
                continue
            observation=SubscriptionObservation(observation_id="claude-statusline:"+digest,
                provider="claude",window_id=window,consumed_percent=percent,
                window_start=resets-duration,resets_at=resets,observed_at=now,
                valid_until=min(now+timedelta(minutes=15),resets),evidence_sha256=digest)
            body=observation.model_dump_json()
            conn.execute("INSERT INTO observations(fingerprint,window_id,observed_at,body) VALUES(?,?,?,?)",
                (digest,window,now.isoformat(),body))
            result.append(observation)
        conn.execute("DELETE FROM observations WHERE observed_at<?",((now-timedelta(days=30)).isoformat(),))
        conn.execute("DELETE FROM feed_sessions WHERE last_seen<?",((now-timedelta(days=30)).isoformat(),))
    return result

def deliver_pending(state_path,send,*,now=None):
    """Retry telemetry with its original identity/time, never generate a model call."""
    now=_now(now);delivered=0
    if not Path(state_path).is_file():
        return 0
    with _connect(state_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows=conn.execute("SELECT fingerprint,body,delivered FROM observations o "
            "WHERE rowid=(SELECT rowid FROM observations n WHERE n.window_id=o.window_id "
            "ORDER BY observed_at DESC,rowid DESC LIMIT 1) ORDER BY window_id").fetchall()
        for fingerprint,body,already_delivered in rows:
            observation=SubscriptionObservation.model_validate_json(body)
            if already_delivered or not observation.observed_at<=now<observation.valid_until:
                continue
            try:
                ack=send(observation.model_dump(mode="json"))
                if ack.get("state")!="recorded" or ack.get("observation_id")!=observation.observation_id:
                    break
            except Exception:
                break
            conn.execute("UPDATE observations SET delivered=1 WHERE fingerprint=?",(fingerprint,))
            delivered+=1
    return delivered

def subscription_producer_ids(capture):
    value=capture.get("subscription_producer_ids",[])
    if not isinstance(value,list) or any(not isinstance(v,str) or not v.strip() for v in value):
        raise ValueError("subscription_producer_ids must be an explicit list of producer IDs")
    return frozenset(value)
