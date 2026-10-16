"""Validated policy activation before dispatch."""

import tomllib
import uuid
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from contracts.policy import PolicySnapshot, validate_policy
from bridge.runtime.admission import ARBITRATION_LOCK


def _reject_placeholder(value: str, label: str) -> None:
    if not value.strip() or "REQUIRED" in value.upper():
        raise ValueError(f"{label} must be a real reviewed value")


def activate_policy(dsn: str, proposed: dict, capabilities: dict, *,
                    approved_hash: str, operator_id: str) -> PolicySnapshot:
    validation = validate_policy(proposed, capabilities)
    if validation.snapshot is None:
        raise ValueError("; ".join(validation.errors))
    snapshot = validation.snapshot
    _reject_placeholder(snapshot.review.reviewer, "reviewer")
    _reject_placeholder(snapshot.review.approval_reference, "review approval reference")
    _reject_placeholder(snapshot.fallback.paid_model_profile, "fallback profile")
    _reject_placeholder(operator_id, "operator identity")
    if snapshot.source_hash != approved_hash:
        raise ValueError("approval hash does not match validated policy hash")
    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (ARBITRATION_LOCK,))
        conn.execute("INSERT INTO config_versions(version,source_hash,review_reference,activated_at,policy) "
                     "VALUES(%s,%s,%s,now(),%s) ON CONFLICT(version) DO UPDATE "
                     "SET review_reference=excluded.review_reference,policy=excluded.policy,activated_at=now() "
                     "WHERE config_versions.source_hash=excluded.source_hash",
                     (snapshot.version, snapshot.source_hash, snapshot.review.approval_reference,
                      Jsonb(snapshot.model_dump())))
        stored = conn.execute("SELECT source_hash FROM config_versions WHERE version=%s",
                              (snapshot.version,)).fetchone()
        if stored[0] != snapshot.source_hash:
            raise ValueError("policy version collision")
        conn.execute("INSERT INTO runtime_active_policy(singleton,version,source_hash) "
                     "VALUES(true,%s,%s) ON CONFLICT(singleton) DO UPDATE "
                     "SET version=excluded.version,source_hash=excluded.source_hash,activated_at=now()",
                     (snapshot.version, snapshot.source_hash))
        conn.execute("INSERT INTO runtime_policy_activations(activation_id,version,source_hash,"
                     "approval_reference,operator_id) VALUES(%s,%s,%s,%s,%s)",
                     (str(uuid.uuid4()), snapshot.version, snapshot.source_hash,
                      snapshot.review.approval_reference, operator_id))
    return snapshot


def activate_policy_file(dsn: str, path: str | Path, capabilities: dict, *,
                         approved_hash: str, operator_id: str) -> PolicySnapshot:
    with Path(path).open("rb") as source:
        return activate_policy(dsn, tomllib.load(source), capabilities,
                               approved_hash=approved_hash, operator_id=operator_id)


def active_policy(dsn: str) -> PolicySnapshot | None:
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT c.policy FROM runtime_active_policy a "
                           "JOIN config_versions c ON c.version=a.version WHERE a.singleton=true").fetchone()
    return PolicySnapshot.model_validate(row[0]) if row else None
