"""No-guidance baseline; registered guidance is deliberately unavailable until Task 14."""

from dataclasses import dataclass
from datetime import datetime

from bridge.runtime.deadlines import reserve_local_deadlines


@dataclass(frozen=True)
class ReadinessDecision:
    route: str
    reason: str
    manifest: dict
    gate_facts: dict
    local_cutoff_at: datetime | None


def check_readiness(*, task, prompt: str, classification_source: str,
                    parent_scope: list[str], required_context: dict[str, str],
                    measured_input_tokens: int | None, local_context_tokens: int,
                    local_reserved_output_tokens: int, paid_context_tokens: int,
                    paid_reserved_output_tokens: int, observed_owner_model: str | None,
                    compatible_owner_models: list[str], now: datetime,
                    parent_deadline_at: datetime, client_deadline_at: datetime,
                    fallback_seconds: int, delivery_margin_seconds: int) -> ReadinessDecision:
    facts = {"category": task.category, "scope": task.scope,
             "classification_source": classification_source,
             "required_context_keys": sorted(required_context),
             "measured_input_tokens": measured_input_tokens,
             "local_context_tokens": local_context_tokens,
             "local_reserved_output_tokens": local_reserved_output_tokens,
             "paid_context_tokens": paid_context_tokens,
             "paid_reserved_output_tokens": paid_reserved_output_tokens,
             "observed_owner_model": observed_owner_model}
    manifest = {"state": "no_guidance", "version": 1, "pack_ids": []}

    def paid(reason):
        return ReadinessDecision("paid", reason, manifest, facts, None)

    if classification_source != "ingress_verified" or not task.category or task.category == "unknown":
        return paid("untrusted_or_unknown_category")
    if not set(task.scope).issubset(parent_scope):
        return paid("out_of_scope")
    if not prompt.strip() or any(not value.strip() for value in required_context.values()):
        return paid("required_context_missing")
    if measured_input_tokens is None or measured_input_tokens < 0:
        return paid("context_measurement_unavailable")
    if min(local_context_tokens, paid_context_tokens, local_reserved_output_tokens,
           paid_reserved_output_tokens) <= 0:
        return paid("profile_unvalidated")
    if measured_input_tokens + local_reserved_output_tokens > local_context_tokens:
        return paid("local_context_exceeded")
    if measured_input_tokens + paid_reserved_output_tokens > paid_context_tokens:
        return paid("fallback_context_exceeded")
    if not observed_owner_model or observed_owner_model not in compatible_owner_models:
        return paid("owner_model_incompatible")
    try:
        deadlines = reserve_local_deadlines(now=now, parent_deadline_at=parent_deadline_at,
            client_deadline_at=client_deadline_at, fallback_seconds=fallback_seconds,
            delivery_margin_seconds=delivery_margin_seconds)
    except ValueError:
        return paid("fallback_time_unavailable")
    facts["local_cutoff_at"] = deadlines.local_cutoff_at.isoformat()
    return ReadinessDecision("local", "baseline_ready", manifest, facts,
                             deadlines.local_cutoff_at)
