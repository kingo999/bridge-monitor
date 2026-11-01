"""Deterministic named task presets on top of the existing Codex/Claude gates."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from bridge.router.codex_claude import evaluate_codex_claude, prepare_codex_claude_route
from contracts.codex_claude import (CodexClaudeDecision, CodexClaudePolicy, CodexClaudeTaskFacts,
                                    HostRoutingFacts, TASK_PRESETS, TASK_PRESET_VERSION)

LEGACY_KEYS = {"action", "reason", "selected_profile", "requested_override", "evaluator_version",
               "policy_digest", "policy_snapshot", "facts", "host_facts", "candidates"}
MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]


def _policy(enabled=True, disabled=()):
    policy = CodexClaudePolicy(enabled=enabled)
    for name, profile in policy.profiles.items():
        profile.enabled = name not in disabled
    return policy


def _host(**changes):
    values = dict(authorized=True, context_available=True, supplied_context_only=True,
                  profile_models=list(MODELS))
    values.update(changes)
    return HostRoutingFacts(**values)


def _facts(complexity="low", risk="low"):
    return CodexClaudeTaskFacts(category="coding", complexity=complexity, risk=risk)


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_versioned_definition_maps_to_existing_profile_slots():
    assert TASK_PRESET_VERSION == "task-presets-v1"
    assert {name: item["profile"] for name, item in TASK_PRESETS.items()} == {
        "mechanical": "haiku", "standard": "sonnet", "deep_review": "opus"}
    starter = CodexClaudePolicy()
    assert [starter.profiles[TASK_PRESETS[n]["profile"]].effort
            for n in ("mechanical", "standard", "deep_review")] == ["low", "medium", "medium"]


@pytest.mark.parametrize("complexity,risk", [("low", "low"), ("ordinary", "high"), ("complex", "unknown")])
def test_no_preset_is_byte_equivalent_legacy_payload(complexity, risk):
    plain = evaluate_codex_claude(_policy(), _facts(complexity, risk), _host())
    explicit_none = evaluate_codex_claude(_policy(), _facts(complexity, risk), _host(), task_preset=None)
    payload = plain.model_dump(mode="json")
    assert set(payload) == LEGACY_KEYS
    assert explicit_none.model_dump(mode="json") == payload
    assert _digest(explicit_none.model_dump(mode="json")) == _digest(payload)
    assert json.loads(plain.model_dump_json()) == payload
    assert plain.reason == "complexity_rule"


def test_old_stored_json_still_validates_and_round_trips():
    old = evaluate_codex_claude(_policy(), _facts("ordinary", "ordinary"), _host()).model_dump(mode="json")
    old.pop("task_preset", None)
    restored = CodexClaudeDecision.model_validate(old)
    assert restored.task_preset is None
    assert restored.model_dump(mode="json") == old
    assert _digest(restored.model_dump(mode="json")) == _digest(old)


def test_unknown_or_empty_preset_is_explicitly_blocked():
    for value in ("turbo", "", "Mechanical"):
        decision = evaluate_codex_claude(_policy(), _facts(), _host(), task_preset=value)
        assert (decision.action, decision.reason) == ("blocked", "invalid_task_preset")
        assert decision.task_preset["requested"] == value
        assert decision.task_preset["applied"] is None
        assert decision.task_preset["status"] == "blocked"
    unknown_with_family = evaluate_codex_claude(_policy(), _facts(), _host(), "sonnet", task_preset="turbo")
    assert unknown_with_family.reason == "invalid_task_preset"


def test_mechanical_only_for_known_low_complexity_and_low_risk():
    facts = _facts("low", "low")
    before = facts.model_dump()
    decision = evaluate_codex_claude(_policy(), facts, _host(), task_preset="mechanical")
    assert (decision.action, decision.selected_profile, decision.reason) == ("claude", "haiku", "task_preset")
    assert decision.task_preset == {"requested": "mechanical", "applied": "mechanical",
        "version": TASK_PRESET_VERSION, "status": "applied", "reason": "task_preset",
        "profile": "haiku"}
    assert facts.model_dump() == before and decision.facts == before
    for complexity, risk in (("ordinary", "low"), ("low", "high"), ("unknown", "low"), ("low", "unknown")):
        blocked = evaluate_codex_claude(_policy(), _facts(complexity, risk), _host(), task_preset="mechanical")
        assert (blocked.action, blocked.reason) == ("blocked", "task_preset_incompatible")
        assert blocked.task_preset["applied"] is None and blocked.selected_profile is None


def test_standard_cannot_downgrade_complex_opus_requirement():
    blocked = evaluate_codex_claude(_policy(), _facts("complex", "low"), _host(), task_preset="standard")
    assert (blocked.action, blocked.reason) == ("blocked", "task_preset_incompatible")
    for complexity in ("low", "ordinary", "unknown"):
        decision = evaluate_codex_claude(_policy(), _facts(complexity, "low"), _host(), task_preset="standard")
        assert (decision.action, decision.selected_profile) == ("claude", "sonnet")


def test_deep_review_requests_stronger_existing_profile():
    for complexity in ("low", "ordinary", "complex"):
        decision = evaluate_codex_claude(_policy(), _facts(complexity, "low"), _host(), task_preset="deep_review")
        assert (decision.action, decision.selected_profile) == ("claude", "opus")
        assert decision.task_preset["applied"] == "deep_review"


def test_explicit_family_override_wins_and_is_recorded_as_overridden():
    decision = evaluate_codex_claude(_policy(), _facts("complex"), _host(), "haiku", task_preset="deep_review")
    assert (decision.action, decision.selected_profile, decision.reason) == ("claude", "haiku", "caller_override")
    assert decision.requested_override == "haiku"
    assert decision.task_preset["requested"] == "deep_review"
    assert decision.task_preset["applied"] is None
    assert decision.task_preset["status"] == "overridden"


def test_codex_keep_owner_override_retains_behavior_without_authority():
    decision = evaluate_codex_claude(_policy(), _facts(), _host(), "codex", task_preset="mechanical")
    assert (decision.action, decision.reason, decision.selected_profile) == ("keep_owner", "caller_keep_owner", None)
    assert decision.task_preset["status"] == "overridden" and decision.task_preset["applied"] is None
    invalid = evaluate_codex_claude(_policy(), _facts(), _host(), "gpt", task_preset="mechanical")
    assert invalid.reason == "invalid_override"


@pytest.mark.parametrize("policy,host,reason,action", [
    (_policy(enabled=False), _host(), "workflow_disabled", "blocked"),
    (_policy(), _host(authorized=False), "scope_not_authorized", "blocked"),
    (_policy(), _host(context_available=False), "context_unavailable", "blocked"),
    (_policy(), _host(durable_logging_available=False), "durable_logging_unavailable", "blocked"),
    (_policy(), _host(needs_tools=True), "owner_tools_required", "keep_owner"),
    (_policy(), _host(supplied_context_only=False), "owner_tools_required", "keep_owner"),
])
def test_all_existing_gates_precede_presets(policy, host, reason, action):
    for preset in ("mechanical", "deep_review", "turbo"):
        decision = evaluate_codex_claude(policy, _facts(), host, task_preset=preset)
        if preset == "turbo" and reason == "owner_tools_required":
            assert decision.reason == "invalid_task_preset"
            continue
        assert (decision.action, decision.reason) == (action, reason)
        assert decision.selected_profile is None
        assert decision.task_preset["applied"] is None


def test_disabled_or_unavailable_preset_profile_stays_blocked():
    disabled = evaluate_codex_claude(_policy(disabled=("opus",)), _facts(), _host(), task_preset="deep_review")
    assert (disabled.action, disabled.reason) == ("blocked", "profile_disabled")
    assert disabled.task_preset["status"] == "blocked" and disabled.task_preset["applied"] is None
    missing = evaluate_codex_claude(_policy(), _facts(), _host(profile_models=MODELS[:2]), task_preset="deep_review")
    assert (missing.action, missing.reason) == ("blocked", "execution_profile_unavailable")


def test_prepare_route_reads_preset_and_pins_snapshot_model_and_effort():
    from contracts.work import TaskPacket
    policy = _policy()
    task = TaskPacket(task_id="t", work_item_id="w", category="coding", scope=["analysis"],
        token_budget=1000, call_budget=1,
        context={"complexity": "low", "risk": "low", "task_preset": "mechanical"})
    adapter = SimpleNamespace(profile_models=list(MODELS))
    decision = prepare_codex_claude_route(policy, task, "rename variable", adapter)
    assert (decision.action, decision.selected_profile) == ("claude", "haiku")
    pinned = decision.policy_snapshot["profiles"][decision.selected_profile]
    assert (pinned["model_id"], pinned["effort"]) == ("claude-haiku-5-5", "low")
    del task.context["task_preset"]
    legacy = prepare_codex_claude_route(policy, task, "rename variable", adapter)
    assert set(legacy.model_dump(mode="json")) == LEGACY_KEYS
    assert legacy.policy_digest == decision.policy_digest
