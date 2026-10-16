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
    assert row == (proposed["review"]["approval_reference"], expected.source_hash)


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
