import hashlib
import json

from contracts.policy import PolicyInput, validate_policy


def base_policy():
    return {
        "limits": {"claude_global_active": 2, "claude_per_root_active": 2,
                   "claude_waiting": 8, "qwen_active": 1, "qwen_waiting": 4,
                   "local_outstanding_per_claude": 1,
                   "claude_execution_seconds": 900, "qwen_execution_seconds": 180},
        "deployment": {"queue_deadline_seconds": 30, "context_wait_seconds": 20,
                       "fallback_allowance_tokens": 1000, "client_timeout_seconds": 1200,
                       "delivery_margin_seconds": 20, "payload_limit_bytes": 1000000,
                       "spool_limit_bytes": 10000000, "retention_days": 30,
                       "postgres_dsn_ref": "env:BRIDGE_MONITOR_DSN",
                       "qwen_endpoint": "http://skynet.local/test"},
        "fallback": {"paid_model_profile": "claude-test"},
        "review": {"reviewer": "operator", "approval_reference": "review-1"},
    }


def test_policy_requires_review_and_known_keys():
    policy = base_policy()
    good = validate_policy(policy, {"claude-test": {"context_tokens": 200000}})
    assert good.snapshot is not None
    assert good.snapshot.limits.claude_global_active == 2
    assert len(good.snapshot.source_hash) == 64
    policy["review"] = {}
    assert validate_policy(policy, {"claude-test": {"context_tokens": 200000}}).snapshot is None
    policy = base_policy()
    policy["surprise"] = True
    assert validate_policy(policy, {"claude-test": {"context_tokens": 200000}}).snapshot is None


def test_policy_requires_deployment_values_and_supported_fallback():
    policy = base_policy()
    del policy["deployment"]["queue_deadline_seconds"]
    assert validate_policy(policy, {"claude-test": {"context_tokens": 200000}}).snapshot is None
    policy = base_policy()
    assert validate_policy(policy, {}).snapshot is None


def test_policy_rejects_inconsistent_capacity():
    policy = base_policy()
    policy["limits"]["claude_per_root_active"] = 3
    assert validate_policy(policy, {"claude-test": {"context_tokens": 200000}}).snapshot is None


def test_local_model_choice_requires_a_pinned_digest():
    policy = base_policy()
    policy["deployment"]["qwen_model_profile"] = "qwen2.5-coder:14b-instruct-q4_K_M"
    capabilities = {"claude-test": {"context_tokens": 200000}}
    assert validate_policy(policy, capabilities).snapshot is None
    policy["deployment"]["qwen_model_digest"] = "a" * 64
    assert validate_policy(policy, capabilities).snapshot is not None


def test_agent_route_requires_token_reference_and_consistent_deadlines():
    policy = base_policy()
    policy["deployment"].update(qwen_model_profile="qwen", qwen_model_digest="a" * 64,
                                 qwen_agent_endpoint="http://skynet:8874")
    capability = {"claude-test": {"context_tokens": 200000}}
    assert validate_policy(policy, capability).snapshot is None
    policy["deployment"]["qwen_agent_token_ref"] = "literal-secret"
    assert validate_policy(policy, capability).snapshot is None
    policy["deployment"]["qwen_agent_token_ref"] = "env:SKYNET_AGENT_TOKEN"
    policy["deployment"]["qwen_first_output_seconds"] = 45
    policy["deployment"]["qwen_idle_output_seconds"] = 20
    policy["limits"]["qwen_execution_seconds"] = 120
    assert validate_policy(policy, capability).snapshot is not None
    policy["deployment"]["qwen_first_output_seconds"] = 121
    assert validate_policy(policy, capability).snapshot is None


def test_router_policy_is_disabled_by_default_and_shadow_requires_verifier():
    capability = {"claude-test": {"context_tokens": 200000}}
    old = validate_policy(base_policy(), capability)
    assert old.snapshot is not None
    assert old.snapshot.router.mode == "disabled"
    proposed = base_policy()
    proposed["router"] = {"mode": "shadow"}
    assert validate_policy(proposed, capability).snapshot is None
    proposed["router"]["verifier"] = {
        "image": "python@sha256:" + "a" * 64,
        "daemon_endpoint": "npipe:////./pipe/dockerDesktopLinuxEngine",
    }
    assert validate_policy(proposed, capability).snapshot is None
    proposed["deployment"].update(qwen_model_profile="qwen-test", qwen_model_digest="b" * 64,
                                  qwen_agent_endpoint="http://skynet:8874",
                                  qwen_agent_token_ref="env:SKYNET_AGENT_TOKEN")
    approved = validate_policy(proposed, capability)
    assert approved.snapshot is not None
    assert approved.snapshot.router.mode == "shadow"
    assert approved.snapshot.source_hash != old.snapshot.source_hash


def test_old_policy_without_router_keeps_its_existing_hash():
    proposed = base_policy()
    parsed = PolicyInput.model_validate(proposed)
    prior_canonical = json.dumps(parsed.model_dump(exclude={"review", "router"}),
                                 sort_keys=True, separators=(",", ":"))
    prior_hash = hashlib.sha256(prior_canonical.encode()).hexdigest()
    current = validate_policy(proposed, {"claude-test": {"context_tokens": 200000}})
    assert current.snapshot is not None
    assert current.snapshot.source_hash == prior_hash
