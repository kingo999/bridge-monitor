"""Persistence for classifier observations and append-only evaluations.

These tables are separate from calls/runtime_jobs/delegation_decisions, so a classifier
observation can never be counted as a routed dispatch, a Qwen job or a paid request.
Protected request/source/response bytes use the existing ``content_objects`` store.
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from bridge.classifier.config import ClassifierShadowConfig
from bridge.classifier.rubric import RUBRIC_SHA256, RUBRIC_VERSION
from contracts.classifier import CONTRACT_VERSION, ObservationCapture

CAPTURE_LOCK = 0x4C534F43  # serializes queue-bound checks; independent of admission locks
RUNNER_LOCK = 0x4C53524E  # at most one classifier runner process at a time
NAMESPACE = "bridge-monitor:classifier-shadow"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def milliseconds(delta: timedelta) -> int:
    return max(0, round(delta.total_seconds() * 1000))


def source_key(cap: ObservationCapture, cfg: ClassifierShadowConfig) -> str:
    """Durable identity of one intended observation. A rerun needs a distinct replay_id."""
    parts = [cap.origin_kind, cap.origin_ref, cap.input.request_sha256(),
             cap.input.source_sha256(), cfg.profile.profile_id, RUBRIC_SHA256,
             cfg.model_digest, cap.replay_id or ""]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def observation_id_for(key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{NAMESPACE}:{key}"))


@dataclass
class CaptureResult:
    state: str  # captured | skipped | duplicate | disabled
    observation_id: str | None = None
    skip_reason: str | None = None
    existing_state: str | None = None


def put_content(conn, content_id: str, raw: bytes, *, role: str, origin: str,
                completeness: str = "complete") -> str:
    digest = hashlib.sha256(raw).hexdigest()
    conn.execute("""INSERT INTO content_objects(content_id,raw_bytes,byte_length,sha256,role,
        direction,content_type,encoding,origin,capture_layer,completeness,block_order)
        VALUES (%s,%s,%s,%s,%s,'in','text/plain','utf-8',%s,'classifier',%s,0)
        ON CONFLICT(content_id) DO NOTHING""",
        (content_id, raw, len(raw), digest, role, origin, completeness))
    stored = conn.execute("SELECT sha256 FROM content_objects WHERE content_id=%s",
                          (content_id,)).fetchone()
    if stored[0] not in (digest, None):
        raise ValueError("content ID conflict")
    return content_id


def _origin(kind: str) -> str:
    return "observed" if kind in ("natural", "controlled") else "synthetic"


def capture(dsn: str, cap: ObservationCapture, cfg: ClassifierShadowConfig, *,
            now: datetime | None = None) -> CaptureResult:
    """Record the original input and trusted facts. Never calls a model."""
    if not cfg.enabled:
        return CaptureResult("disabled")
    now = now or utcnow()
    key = source_key(cap, cfg)
    obs_id = observation_id_for(key)
    size = cap.input.input_bytes()
    with psycopg.connect(dsn, connect_timeout=5) as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (CAPTURE_LOCK,))
        existing = conn.execute("SELECT state FROM classifier_observations WHERE source_key=%s",
                                (key,)).fetchone()
        if existing:
            return CaptureResult("duplicate", obs_id, existing_state=existing[0])
        skip = None
        if size > min(cfg.max_input_bytes, cap.max_input_bytes or cfg.max_input_bytes):
            skip = "input_too_large"
        else:
            pending = conn.execute("SELECT count(*) FROM classifier_observations "
                                   "WHERE state IN ('captured','dispatching')").fetchone()[0]
            if pending >= cfg.max_queue:
                skip = "queue_full"
        request_id = source_id = None
        if skip != "input_too_large":
            origin = _origin(cap.origin_kind)
            request_id = put_content(conn, f"classifier:{obs_id}:request",
                                     cap.input.request.encode("utf-8"),
                                     role="classifier_request", origin=origin)
            if cap.input.source:
                source_id = put_content(conn, f"classifier:{obs_id}:source",
                                        cap.input.source.encode("utf-8"),
                                        role="classifier_source", origin=origin)
        conn.execute("""INSERT INTO classifier_observations
            (observation_id,source_key,origin_kind,origin_ref,replay_id,task_id,work_item_id,
             request_sha256,source_sha256,input_bytes,request_content_id,source_content_id,
             facts,facts_captured_at,facts_provenance,contract_version,rubric_version,
             rubric_sha256,profile_id,profile,requested_model,expected_digest,state,skip_reason,
             captured_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (obs_id, key, cap.origin_kind, cap.origin_ref, cap.replay_id, cap.task_id,
             cap.work_item_id, cap.input.request_sha256(), cap.input.source_sha256(), size,
             request_id, source_id, Jsonb(cap.input.facts), now, Jsonb(cap.facts_provenance),
             CONTRACT_VERSION, RUBRIC_VERSION, RUBRIC_SHA256, cfg.profile.profile_id,
             Jsonb(cfg.profile.model_dump()), cfg.model, cfg.model_digest,
             "skipped" if skip else "captured", skip, now))
    return CaptureResult("skipped" if skip else "captured", obs_id, skip_reason=skip)


def expire_stale(dsn: str, cfg: ClassifierShadowConfig, now: datetime) -> int:
    with psycopg.connect(dsn, connect_timeout=5) as conn, conn.transaction():
        cursor = conn.execute("""UPDATE classifier_observations SET state='skipped',
            skip_reason='expired_unprocessed' WHERE state='captured' AND captured_at < %s""",
            (now - timedelta(seconds=cfg.max_queue_age_seconds),))
        return cursor.rowcount


def recover_interrupted(dsn: str) -> int:
    """Rows left ``dispatching`` by a dead runner may have reached the model: mark uncertain."""
    with psycopg.connect(dsn, connect_timeout=5) as conn, conn.transaction():
        cursor = conn.execute("""UPDATE classifier_observations SET state='uncertain',
            error_type='runner_interrupted' WHERE state='dispatching'""")
        return cursor.rowcount


def claim_next(dsn: str, runner_id: str, now: datetime) -> dict | None:
    with psycopg.connect(dsn, connect_timeout=5, row_factory=dict_row) as conn, conn.transaction():
        row = conn.execute("""UPDATE classifier_observations SET state='dispatching',
            dispatched_at=%s, attempts=attempts+1, runner_id=%s
            WHERE observation_id=(SELECT observation_id FROM classifier_observations
                WHERE state='captured' ORDER BY captured_at, observation_id
                FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *""", (now, runner_id)).fetchone()
        if row is None:
            return None
        for column in ("request_content_id", "source_content_id"):
            content = conn.execute("SELECT raw_bytes FROM content_objects WHERE content_id=%s",
                                   (row[column],)).fetchone() if row[column] else None
            row[column.replace("_content_id", "_text")] = (
                bytes(content["raw_bytes"]).decode("utf-8")
                if content and content["raw_bytes"] is not None else None)
        return row


def actual_route_for(dsn: str, work_item_id: str | None) -> tuple[str | None, dict | None]:
    """The unchanged system's production parent-router decision, with provenance."""
    if not work_item_id:
        return None, None
    with psycopg.connect(dsn, connect_timeout=2, options="-c statement_timeout=1000") as conn:
        row = conn.execute("""SELECT decision_id,route,decided_at FROM delegation_decisions
            WHERE work_item_id=%s AND decision_stage='parent_router'
            AND execution_mode='production' ORDER BY decided_at,decision_id LIMIT 1""",
            (work_item_id,)).fetchone()
    if not row:
        return None, None
    return row[1], {"table": "delegation_decisions", "decision_id": row[0],
                    "decided_at": row[2].isoformat(), "project_route": row[1]}


FINISH_FIELDS = {
    "error_type", "error_detail", "first_content_at", "completed_at", "runtime_version",
    "observed_digest", "reported_model", "backend", "done_reason", "response_bytes",
    "response_sha256", "valid", "validation_error", "rec_category", "rec_route",
    "policy_version", "policy_allowed", "policy_blocks", "policy_warnings", "actual_route",
    "actual_route_source", "actual_route_observed_at", "queue_ms", "request_ms",
    "first_content_ms", "inference_ms", "load_ms", "prompt_eval_ms", "eval_ms",
    "end_to_end_ms", "input_tokens", "output_tokens", "usage_provenance"}


def finish(dsn: str, observation_id: str, *, state: str, fields: dict,
           response: bytes | None, response_complete: bool) -> None:
    """Commit the outcome once. Only a row still ``dispatching`` can be finished."""
    unknown = set(fields) - FINISH_FIELDS
    if unknown:
        raise ValueError(f"unknown observation fields: {sorted(unknown)}")
    with psycopg.connect(dsn, connect_timeout=5) as conn, conn.transaction():
        origin = conn.execute("SELECT origin_kind FROM classifier_observations "
                              "WHERE observation_id=%s", (observation_id,)).fetchone()
        if response:
            fields = {**fields, "response_content_id": put_content(
                conn, f"classifier:{observation_id}:response", response,
                role="classifier_response", origin=_origin(origin[0]),
                completeness="complete" if response_complete else "partial")}
        columns = ["state"] + list(fields)
        values = [state] + [Jsonb(v) if isinstance(v, (dict, list)) else v
                            for v in fields.values()]
        assignments = ", ".join(f"{c}=%s" for c in columns)
        cursor = conn.execute(f"UPDATE classifier_observations SET {assignments} "
                              "WHERE observation_id=%s AND state='dispatching'",
                              (*values, observation_id))
        if cursor.rowcount != 1:
            raise RuntimeError("observation was not in dispatching state")


def append_evaluation(dsn: str, observation_id: str, *, kind: str, actor_id: str,
                      reference_category: str | None = None, reference_route: str | None = None,
                      acceptable_routes: list[str] | None = None, judgment: str | None = None,
                      reason: str | None = None, evidence: dict | None = None,
                      supersedes_id: str | None = None, evaluation_id: str | None = None,
                      now: datetime | None = None) -> str:
    identifier = evaluation_id or str(uuid.uuid4())
    with psycopg.connect(dsn, connect_timeout=5) as conn, conn.transaction():
        conn.execute("""INSERT INTO classifier_evaluations
            (evaluation_id,observation_id,kind,actor_id,reference_category,reference_route,
             acceptable_routes,judgment,reason,evidence,supersedes_id,recorded_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (identifier, observation_id, kind, actor_id, reference_category, reference_route,
             Jsonb(acceptable_routes) if acceptable_routes is not None else None, judgment,
             reason, Jsonb(evidence or {}), supersedes_id, now or utcnow()))
    return identifier
