from contracts.policy import validate_policy


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
