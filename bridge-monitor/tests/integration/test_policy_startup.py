import uuid

import psycopg
import pytest

from contracts.policy import validate_policy
from database.migrate import migrate
from bridge.runtime.policy_startup import activate_policy, active_policy
from tests.integration.test_admission import _policy


def test_policy_activation_binds_reviewed_hash(runtime_dsn):
    test_dsn = runtime_dsn
    proposed = _policy().model_dump()
    proposed["review"]["approval_reference"] = f"approval-{uuid.uuid4()}"
    expected = validate_policy(proposed, {"claude-test": {"context_tokens": 10000}}).snapshot
    with pytest.raises(ValueError, match="hash"):
        activate_policy(test_dsn, proposed, {"claude-test": {"context_tokens": 10000}},
                        approved_hash="wrong", operator_id="operator-1")
    activated = activate_policy(test_dsn, proposed, {"claude-test": {"context_tokens": 10000}},
                                approved_hash=expected.source_hash, operator_id="operator-1")
    assert activated.source_hash == expected.source_hash
    assert active_policy(test_dsn).version == activated.version
    with psycopg.connect(test_dsn) as conn:
        row = conn.execute("SELECT review_reference, source_hash FROM config_versions WHERE version=%s",
                           (activated.version,)).fetchone()
        activation = conn.execute("SELECT approval_reference FROM runtime_policy_activations "
            "WHERE version=%s AND operator_id='operator-1' "
            "ORDER BY activated_at DESC LIMIT 1", (activated.version,)).fetchone()
    assert row[1] == expected.source_hash
    assert activation == (proposed["review"]["approval_reference"],)


def test_placeholder_review_and_unknown_capability_rejected(runtime_dsn):
    test_dsn = runtime_dsn
    proposed = _policy().model_dump()
    proposed["review"]["approval_reference"] = "REQUIRED"
    with pytest.raises(ValueError, match="review"):
        activate_policy(test_dsn, proposed, {"claude-test": {"context_tokens": 10000}},
                        approved_hash="x", operator_id="operator-1")
    proposed["review"]["approval_reference"] = "approval-1"
    with pytest.raises(ValueError, match="profile"):
        activate_policy(test_dsn, proposed, {}, approved_hash="x", operator_id="operator-1")


def test_reactivation_keeps_original_policy_review_snapshot(runtime_dsn):
    dsn = runtime_dsn
    proposed = _policy().model_dump()
    proposed["deployment"]["qwen_endpoint"] += "/" + str(uuid.uuid4())
    caps = {"claude-test": {"context_tokens": 10000}}
    digest = validate_policy(proposed, caps).snapshot.source_hash
    first = activate_policy(dsn, proposed, caps, approved_hash=digest, operator_id="operator-1")
    original_ref = first.review.approval_reference
    proposed["review"]["approval_reference"] = "second-independent-review"
    activate_policy(dsn, proposed, caps, approved_hash=digest, operator_id="operator-2")
    with psycopg.connect(dsn) as conn:
        stored = conn.execute("SELECT review_reference,policy->'review'->>'approval_reference' "
            "FROM config_versions WHERE version=%s", (first.version,)).fetchone()
        activations = conn.execute("SELECT approval_reference FROM runtime_policy_activations "
            "WHERE version=%s ORDER BY activated_at", (first.version,)).fetchall()
    assert stored == (original_ref, original_ref)
    assert sorted(row[0] for row in activations) == sorted([original_ref, "second-independent-review"])
