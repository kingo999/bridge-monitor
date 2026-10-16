import uuid

from bridge.runtime.admission import admit_call
from bridge.runtime.workspaces import acquire_source, release_source
from tests.integration.test_admission import _call
from tests.integration.test_ownership import _setup


def test_shared_checkout_writes_are_serial_and_reads_share_snapshot(runtime_dsn):
    dsn = runtime_dsn
    policy, version = _setup(dsn)
    policy = policy.model_copy(update={"limits": policy.limits.model_copy(update={"claude_waiting": 3})})
    calls = [f"call-{uuid.uuid4()}" for _ in range(3)]
    for index, call in enumerate(calls):
        admit_call(dsn, _call(f"root-{call}", call), policy,
                   policy_version=version, pool="claude", reserve_tokens=100,
                   source_snapshot="git:abc", source_mode="write" if index == 2 else "read")
    assert acquire_source(dsn, calls[0], "checkout-A", "read", "git:abc")
    assert acquire_source(dsn, calls[1], "checkout-A", "read", "git:abc")
    assert not acquire_source(dsn, calls[2], "checkout-A", "write", "git:abc")
    assert acquire_source(dsn, calls[2], "checkout-B", "write", "git:abc")
    release_source(dsn, calls[0], "checkout-A")
    release_source(dsn, calls[1], "checkout-A")
    assert acquire_source(dsn, calls[2], "checkout-A", "write", "git:abc")
