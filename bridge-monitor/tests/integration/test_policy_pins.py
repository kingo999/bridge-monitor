import uuid

import psycopg

from bridge.runtime.admission import admit_active_call
from bridge.runtime.policy_reload import reload_policy
from bridge.runtime.policy_startup import active_policy
from bridge.adapters.codex_ingress import CodexIngress
from bridge.telemetry.emitter import TelemetryEmitter
from bridge.telemetry.spool import DurableSpool
from tests.adapters.test_ingress import Collector
from contracts.policy import validate_policy
from tests.concurrency.test_limits import _activate_two
from tests.integration.test_admission import _call


def test_new_child_admission_uses_active_version_and_old_queue_keeps_pin(runtime_dsn, monkeypatch):
    monkeypatch.setenv("BRIDGE_MONITOR_OPERATOR_TOKEN", "operator-secret")
    dsn = runtime_dsn
    _, first_version = _activate_two(dsn)
    root = f"root-{uuid.uuid4()}"
    first = f"call-{uuid.uuid4()}"
    second = f"call-{uuid.uuid4()}"
    assert admit_active_call(dsn, _call(root, first, budget=2), pool="claude",
                             reserve_tokens=100).state == "queued"
    proposed = active_policy(dsn).model_dump(exclude={"version", "source_hash"})
    proposed["limits"]["claude_waiting"] = 1
    caps = {"claude-test": {"context_tokens": 10000}}
    digest = validate_policy(proposed, caps).snapshot.source_hash
    outcome = reload_policy(dsn, proposed, caps, approved_hash=digest,
                            operator_id="operator-1", auth_token="operator-secret")
    assert outcome.status == "activated"
    # The smaller queue does not delete the already admitted child.
    assert admit_active_call(dsn, _call(root, second, budget=2), pool="claude",
                             reserve_tokens=100).reason == "queue_full"
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT policy_version,state FROM runtime_jobs WHERE call_id=%s",
                            (first,)).fetchone() == (first_version, "queued")
    proposed["limits"]["claude_waiting"] = 2
    digest = validate_policy(proposed, caps).snapshot.source_hash
    assert reload_policy(dsn, proposed, caps, approved_hash=digest,
                         operator_id="operator-1", auth_token="operator-secret").status == "activated"
    assert admit_active_call(dsn, _call(root, second, budget=2), pool="claude",
                             reserve_tokens=100).state == "queued"
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT policy_version FROM runtime_jobs WHERE call_id=%s",
                            (second,)).fetchone()[0] == active_policy(dsn).version


def test_running_ingress_picks_up_soft_reset_for_new_call(runtime_dsn, tmp_path, monkeypatch):
    monkeypatch.setenv("BRIDGE_MONITOR_OPERATOR_TOKEN", "operator-secret")
    dsn = runtime_dsn
    _, old_version = _activate_two(dsn)
    emitter = TelemetryEmitter(DurableSpool(tmp_path / "reload-spool.sqlite", limit_bytes=100000),
        Collector(dsn), producer_id=f"reload-{uuid.uuid4()}", incarnation="boot-1")
    ingress = CodexIngress(dsn, emitter, active_policy(dsn), old_version)
    proposed = active_policy(dsn).model_dump(exclude={"version", "source_hash"})
    proposed["deployment"]["queue_deadline_seconds"] += 15
    caps = {"claude-test": {"context_tokens": 10000}}
    digest = validate_policy(proposed, caps).snapshot.source_hash
    new = reload_policy(dsn, proposed, caps, approved_hash=digest,
        operator_id="operator-1", auth_token="operator-secret")
    assert new.status == "activated"
    call = f"call-{uuid.uuid4()}"
    decision = ingress.submit(_call(f"root-{uuid.uuid4()}", call), prompt="Check this",
                              pool="claude", reserve_tokens=100)
    assert decision.state == "queued" and decision.policy_version == new.version
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT policy_version FROM runtime_jobs WHERE call_id=%s",
                            (call,)).fetchone()[0] == new.version
