"""Bounded exclusion metadata, separate from observations and content hashes."""
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import uuid

import psycopg
from psycopg.types.json import Jsonb

from bridge.classifier import store
from bridge.classifier.scenarios import rubric_for_config
from contracts.route_capture import BridgeCapture, RouteCapture, valid_metadata_text


@dataclass(frozen=True, slots=True)
class CaptureExclusion:
    task_id: str
    work_item_id: str
    paid_call_id: str
    decision_id: str
    origin_kind: str
    capture_method: str
    captured_at: datetime
    input_size: int
    size_kind: str
    input_limit: int = 24576


def project_exclusion(item: RouteCapture | BridgeCapture) -> CaptureExclusion | None:
    """Caller-thread projection: len/short fields only, no payload encode/hash/I/O."""
    if not item.valid_binding():
        return None
    chars = len(item.prompt) + (len(item.source) if isinstance(item, RouteCapture) else 0)
    if chars <= 24576:
        return None
    method = 'bridge_ingress' if isinstance(item, BridgeCapture) and item.method == 'delegate_claude' else 'route_work'
    return CaptureExclusion(item.task_id,item.work_item_id,item.paid_call_id,item.decision_id,
                            item.origin_kind,method,item.captured_at,chars,'character_lower_bound')


def from_capture(cap, cfg, captured_at: datetime) -> CaptureExclusion:
    binding = cap.facts_provenance['route_work_binding']
    return CaptureExclusion(cap.task_id,cap.work_item_id,binding['paid_call_id'],binding['decision_id'],
        cap.origin_kind,cap.facts_provenance['capture_method'],captured_at,
        cap.input.input_bytes(),'serialized_utf8_exact',min(24576,cfg.max_input_bytes,cap.max_input_bytes or 24576))


def persist_exclusion(dsn, item: CaptureExclusion, settings, *, now=None):
    return persist_record(dsn,item,settings.shadow_config(),settings.config_sha256,now=now)


def persist_record(dsn, item: CaptureExclusion, cfg, config_sha256: str, *, now=None):
    from bridge.classifier.hook_store import DB_OPTIONS, BUDGET_SCOPE, CaptureDatabaseError, CaptureCommitUncertain
    now = now or store.utcnow()
    if not cfg.enabled:
        return store.CaptureResult('disabled')
    if (not all(valid_metadata_text(v) for v in (item.task_id,item.work_item_id,item.paid_call_id,item.decision_id))
            or item.origin_kind not in ('natural','controlled')
            or item.capture_method not in ('route_work','bridge_ingress')):
        return store.CaptureResult('dropped',skip_reason='binding_skipped')
    if (now-item.captured_at).total_seconds() > min(600,cfg.max_queue_age_seconds):
        return store.CaptureResult('dropped',skip_reason='expired')
    if item.input_size <= item.input_limit:
        raise ValueError('exclusion_not_oversized')
    rubric = rubric_for_config(cfg)
    metadata = {**asdict(item),'captured_at':item.captured_at.isoformat(),
                'reason':'input_too_large','version':'classifier-exclusion-v1',
                'profile_id':cfg.profile.profile_id,'rubric_version':rubric.version,
                'rubric_sha256':rubric.sha256,'requested_model':cfg.model,'config_sha256':config_sha256}
    payload = json.dumps(metadata,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
    if len(payload)>4096:
        return store.CaptureResult('dropped',skip_reason='provenance_too_large')
    # This digest identifies metadata, never request/source content.
    identity = [item.task_id,item.work_item_id,item.paid_call_id,item.decision_id,item.capture_method,
                item.origin_kind,config_sha256,cfg.profile.profile_id]
    key = hashlib.sha256(json.dumps(identity,separators=(',',':')).encode()).hexdigest()
    eid = str(uuid.uuid5(uuid.NAMESPACE_URL,'classifier-exclusion-v1:'+key))
    commit_pending = False
    try:
        with psycopg.connect(dsn,connect_timeout=1,options=DB_OPTIONS) as c, c.transaction():
            c.execute('SELECT pg_advisory_xact_lock(%s)',(store.CAPTURE_LOCK,))
            if c.execute('SELECT 1 FROM classifier_capture_exclusions WHERE source_key=%s',(key,)).fetchone():
                return store.CaptureResult('duplicate',eid)
            budget = c.execute('SELECT consumed_rows,consumed_bytes,last_persisted_at,clock_timestamp() '
                'FROM classifier_hook_capture_budget WHERE scope=%s FOR UPDATE',(BUDGET_SCOPE,)).fetchone()
            if budget is None: raise RuntimeError('missing hook budget')
            rows,used,last,db_now = budget
            if rows>=256 or used+len(payload)>4194304:
                return store.CaptureResult('dropped',skip_reason='capture_budget_exhausted')
            if last is not None and (db_now-last).total_seconds()<2:
                return store.CaptureResult('dropped',skip_reason='rate_limited')
            prior = c.execute('SELECT EXISTS (SELECT 1 FROM results r JOIN calls c USING(call_id) '
                'WHERE c.work_item_id=%s AND r.delivered_at<=%s)',(item.work_item_id,item.captured_at)).fetchone()[0]
            if prior:
                return store.CaptureResult('dropped',skip_reason='solution_already_available')
            c.execute('''INSERT INTO classifier_capture_exclusions
                (exclusion_id,source_key,task_id,work_item_id,paid_call_id,decision_id,origin_kind,
                 capture_method,profile_id,rubric_version,rubric_sha256,requested_model,config_sha256,
                 reason,input_size,size_kind,input_limit,metadata_bytes,captured_at,metadata)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'input_too_large',%s,%s,%s,%s,%s,%s)''',
                (eid,key,item.task_id,item.work_item_id,item.paid_call_id,item.decision_id,item.origin_kind,
                 item.capture_method,cfg.profile.profile_id,rubric.version,rubric.sha256,cfg.model,config_sha256,
                 item.input_size,item.size_kind,item.input_limit,len(payload),item.captured_at,Jsonb(metadata)))
            c.execute('UPDATE classifier_hook_capture_budget SET consumed_rows=consumed_rows+1,'
                'consumed_bytes=consumed_bytes+%s,last_persisted_at=clock_timestamp() WHERE scope=%s',
                (len(payload),BUDGET_SCOPE))
            commit_pending = True
    except Exception as exc:
        if commit_pending: raise CaptureCommitUncertain('commit_outcome_unknown') from exc
        raise CaptureDatabaseError('exclusion_not_committed') from exc
    return store.CaptureResult('excluded',eid,skip_reason='input_too_large')
