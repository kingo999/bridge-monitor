"""Bounded, atomic persistence for automatic captures. Errors are never retried here."""

from datetime import datetime
import json

import psycopg

from bridge.classifier import store
from bridge.classifier.config import ClassifierShadowConfig
from contracts.classifier import ObservationCapture

DB_OPTIONS = '-c lock_timeout=50 -c statement_timeout=250'
BUDGET_SCOPE = 'route_work-v1'


class CaptureDatabaseError(RuntimeError):
    """Persistence failed before a commit was attempted; the sink still latches off."""


class CaptureCommitUncertain(RuntimeError):
    """Commit may have reached PostgreSQL. Never retry this arrival automatically."""


def capture_hook(dsn: str, cap: ObservationCapture, cfg: ClassifierShadowConfig, *,
                 captured_at: datetime, now: datetime | None = None) -> store.CaptureResult:
    commit_pending = False
    try:
        now = now or store.utcnow()
        def drop(reason):
            return store.CaptureResult('dropped', skip_reason=reason)
        if not cfg.enabled:
            return store.CaptureResult('disabled')
        if (now - captured_at).total_seconds() > min(600, cfg.max_queue_age_seconds):
            return drop('expired')
        size = cap.input.input_bytes()
        if size > min(24576, cfg.max_input_bytes, cap.max_input_bytes or 24576):
            return drop('input_too_large')
        key = store.source_key(cap, cfg)
        obs_id = store.observation_id_for(key)
        with psycopg.connect(dsn, connect_timeout=1, options=DB_OPTIONS) as conn, conn.transaction():
            conn.execute('SELECT pg_advisory_xact_lock(%s)', (store.CAPTURE_LOCK,))
            existing = conn.execute('SELECT state FROM classifier_observations WHERE source_key=%s', (key,)).fetchone()
            if existing:
                return store.CaptureResult('duplicate', obs_id, existing_state=existing[0])
            budget = conn.execute('SELECT consumed_rows,consumed_bytes,last_persisted_at,clock_timestamp() '
                'FROM classifier_hook_capture_budget WHERE scope=%s FOR UPDATE', (BUDGET_SCOPE,)).fetchone()
            if budget is None:
                raise RuntimeError('missing hook budget')
            rows, used, last, db_now = budget
            if rows >= 256 or used + size > 4194304:
                return drop('capture_budget_exhausted')
            if last is not None and (db_now - last).total_seconds() < 2:
                return drop('rate_limited')
            pending = conn.execute("SELECT count(*) FROM classifier_observations "
                "WHERE state IN ('captured','dispatching')").fetchone()[0]
            if pending >= min(8, cfg.max_queue):
                return drop('database_queue_full')
            # Metadata only. A later answer is allowed; an answer already delivered at arrival is not.
            prior = conn.execute('SELECT EXISTS (SELECT 1 FROM results r JOIN calls c USING(call_id) '
                'WHERE c.work_item_id=%s AND r.delivered_at<=%s)', (cap.work_item_id, captured_at)).fetchone()[0]
            if prior:
                return drop('solution_already_available')
            provenance = {**cap.facts_provenance, 'persisted_at': db_now.isoformat()}
            if len(json.dumps(provenance, ensure_ascii=False).encode('utf-8')) > 4096:
                return drop('provenance_too_large')
            origin = store._origin(cap.origin_kind)
            request_id = store.put_content(conn, f'classifier:{obs_id}:request', cap.input.request.encode('utf-8'),
                role='classifier_request', origin=origin)
            source_id = (store.put_content(conn, f'classifier:{obs_id}:source', cap.input.source.encode('utf-8'),
                role='classifier_source', origin=origin) if cap.input.source else None)
            store.insert_capture(conn, cap.model_copy(update={'facts_provenance': provenance}), cfg,
                obs_id=obs_id, key=key, size=size, request_id=request_id, source_id=source_id,
                skip=None, now=captured_at)
            conn.execute('UPDATE classifier_hook_capture_budget SET consumed_rows=consumed_rows+1, '
                'consumed_bytes=consumed_bytes+%s,last_persisted_at=clock_timestamp() WHERE scope=%s', (size, BUDGET_SCOPE))
            commit_pending = True
    except Exception as exc:
        if commit_pending:
            raise CaptureCommitUncertain('commit_outcome_unknown') from exc
        raise CaptureDatabaseError('capture_not_committed') from exc
    return store.CaptureResult('captured', obs_id)
