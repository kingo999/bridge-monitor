"""Authenticated, reviewed soft reset over the startup policy validator."""

from dataclasses import dataclass

import psycopg

from bridge.runtime.admission import ARBITRATION_LOCK
from bridge.runtime.policy_startup import _activate_validated, _reject_placeholder
from bridge.runtime.reconciliation import _authenticate
from contracts.policy import PolicySnapshot, validate_policy


@dataclass(frozen=True)
class PolicyReloadResult:
    status: str
    version: str | None
    source_hash: str | None
    reason: str | None = None


_RESTART_DEPLOYMENT = ("postgres_dsn_ref", "qwen_endpoint", "qwen_model_profile",
    "qwen_model_digest", "qwen_output_tokens", "qwen_keep_alive", "qwen_temperature",
    "client_timeout_seconds", "payload_limit_bytes", "spool_limit_bytes", "retention_days")
_RESTART_LIMITS = ("claude_execution_seconds", "qwen_execution_seconds")


def _restart_changes(old: PolicySnapshot, new: PolicySnapshot) -> list[str]:
    changed = [f"deployment.{name}" for name in _RESTART_DEPLOYMENT
               if getattr(old.deployment, name) != getattr(new.deployment, name)]
    changed += [f"limits.{name}" for name in _RESTART_LIMITS
                if getattr(old.limits, name) != getattr(new.limits, name)]
    if old.fallback.paid_model_profile != new.fallback.paid_model_profile:
        changed.append("fallback.paid_model_profile")
    return changed


def reload_policy(dsn, proposed, capabilities, *, approved_hash, operator_id,
                  auth_token) -> PolicyReloadResult:
    _authenticate("operator", operator_id, auth_token)
    validation = validate_policy(proposed, capabilities)
    if validation.snapshot is None:
        return PolicyReloadResult("rejected", None, None, "; ".join(validation.errors))
    snapshot = validation.snapshot
    try:
        _reject_placeholder(snapshot.review.reviewer, "reviewer")
        _reject_placeholder(snapshot.review.approval_reference, "review approval reference")
        _reject_placeholder(snapshot.fallback.paid_model_profile, "fallback profile")
        if snapshot.source_hash != approved_hash:
            raise ValueError("approval hash does not match validated policy hash")
    except ValueError as exc:
        return PolicyReloadResult("rejected", None, None, str(exc))
    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (ARBITRATION_LOCK,))
        active = conn.execute("SELECT c.policy FROM runtime_active_policy a "
                              "JOIN config_versions c ON c.version=a.version "
                              "WHERE a.singleton=true").fetchone()
        if not active:
            return PolicyReloadResult("rejected", None, None, "startup activation required")
        old = PolicySnapshot.model_validate(active[0])
        restart = _restart_changes(old, snapshot)
        if restart:
            return PolicyReloadResult("restart_required", old.version, old.source_hash,
                                      ", ".join(restart))
        try:
            _activate_validated(conn, snapshot, operator_id)
        except ValueError as exc:
            return PolicyReloadResult("rejected", old.version, old.source_hash, str(exc))
        return PolicyReloadResult("activated", snapshot.version, snapshot.source_hash)
