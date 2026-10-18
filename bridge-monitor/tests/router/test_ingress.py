import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from bridge.adapters.base import AdapterResult
from contracts.events import EventType
from bridge.code_fix_verifier import VerificationResult
from bridge.mcp_server import BridgeCoordinator, create_mcp
from bridge.router.cases import register_case
from bridge.telemetry.emitter import TelemetryEmitter
from bridge.telemetry.spool import DurableSpool
from bridge.runtime.policy_startup import activate_policy
from contracts.policy import validate_policy
from contracts.router import RouterCase, RouterPolicy
from contracts.work import TaskPacket
from service.ingest.store import ingest_batch
from tests.integration.test_admission import _policy


class Collector:
    def __init__(self, dsn): self.dsn = dsn
    def ingest_batch(self, producer, events, content):
        return ingest_batch(self.dsn, producer, events, content)


class Paid:
    def __init__(self): self.calls = 0
    def run(self, lease, task, prompt, emitter):
        self.calls += 1
        return AdapterResult(state="completed", answer="Claude fixed response",
            reported_model="claude-test", completion_claim={"route_choice": "paid_direct",
            "local_results": [], "work_resolution": "resolved"}, local_tool_attempts=0)


class Local:
    def preflight(self):
        from bridge.adapters.qwen_runtime import ModelPreflight
        return ModelPreflight(True, "model_profile_verified", "b" * 64)


@pytest.fixture
def router_env(runtime_dsn, tmp_path, monkeypatch):
    monkeypatch.setattr("bridge.router.cases.DockerVerifier.verify",
        lambda self, source, tests: VerificationResult("fail", "tests_failed", 0.1,
            hashlib.sha256(source.encode()).hexdigest()))
    router = RouterPolicy.model_validate({"mode": "shadow", "verifier": {
        "image": "python@sha256:" + "a" * 64,
        "daemon_endpoint": "npipe:////./pipe/dockerDesktopLinuxEngine"}})
    base = _policy()
    deployment = base.deployment.model_copy(update={
        "qwen_model_profile": "qwen-test", "qwen_model_digest": "b" * 64,
        "qwen_agent_endpoint": "http://127.0.0.1:8874",
        "qwen_agent_token_ref": "env:SKYNET_AGENT_TOKEN"})
    policy = base.model_copy(update={"deployment": deployment, "router": router})
    proposed = policy.model_dump()
    caps = {"claude-test": {"context_tokens": 10000}}
    digest = validate_policy(proposed, caps).snapshot.source_hash
    active = activate_policy(runtime_dsn, proposed, caps,
                             approved_hash=digest, operator_id="router-test")
    emitter = TelemetryEmitter(DurableSpool(tmp_path / "spool.sqlite", limit_bytes=1000000),
        Collector(runtime_dsn), producer_id=f"router-{uuid.uuid4()}", incarnation="boot-1")
    paid = Paid()
    coordinator = BridgeCoordinator(runtime_dsn, active, active.version, emitter,
        claude_adapter=paid, qwen_adapter=None, project_root=tmp_path,
        local_capability={"context_tokens": 10000},
        paid_capability={"context_tokens": 10000})
    arrived = datetime.now(timezone.utc) - timedelta(minutes=2)
    case = RouterCase(origin_kind="natural", origin_ref=f"codex:{uuid.uuid4()}",
        task_id=f"task-{uuid.uuid4()}", task_text="Fix square for n=2",
        task_arrived_at=arrived, captured_at=arrived + timedelta(seconds=5),
        baseline_source="def square(n):\n    return n + 1\n",
        tests=[{"function": "square", "args": [2], "expected": 4}],
        output_contract="complete_python_module_v1")
    receipt = register_case(tmp_path, case, router)
    task = TaskPacket(task_id=case.task_id, work_item_id=f"work-{uuid.uuid4()}",
        category="single_file_python_fix", scope=["analysis"],
        token_budget=5000, call_budget=2)
    return coordinator, paid, task, case, receipt


def test_route_work_returns_one_paid_answer_and_queues_one_undelivered_shadow(router_env):
    coordinator, paid, task, case, receipt = router_env
    result = coordinator.route_work(task, case.task_text,
        requested_model="claude-test", reserve_tokens=100,
        output_contract="complete_python_module_v1", verifier_ref=receipt.verifier_ref)
    assert result["state"] == "completed"
    assert result["answer"] == "Claude fixed response"
    assert paid.calls == 1
    assert result["shadow_state"] == "queued"
    assert result["shadow_call_id"]
    with psycopg.connect(coordinator.dsn) as conn:
        decisions = conn.execute("SELECT decision_stage,execution_mode,route,reason "
            "FROM delegation_decisions WHERE work_item_id=%s ORDER BY decided_at,decision_id",
            (task.work_item_id,)).fetchall()
        assert ("parent_router", "production", "paid", "shadow_candidate") in decisions
        assert ("parent_router", "shadow", "local", "shadow_candidate") in decisions
        jobs = conn.execute("SELECT pool,execution_mode,state FROM runtime_jobs "
            "WHERE work_item_id=%s ORDER BY pool", (task.work_item_id,)).fetchall()
        assert jobs == [("claude", "production", "completed"), ("qwen", "shadow", "queued")]
        assert conn.execute("SELECT count(*) FROM events WHERE call_id=%s "
            "AND event_type='call.dispatched'", (result["shadow_call_id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM results WHERE call_id=%s "
            "AND delivered_at IS NOT NULL", (result["call_id"],)).fetchone()[0] == 1
        trace = conn.execute("SELECT event_type,producer_sequence,payload FROM events "
            "WHERE call_id=%s AND event_type IN ('message.sent','delegation.decided',"
            "'call.dispatched') ORDER BY producer_sequence", (result["call_id"],)).fetchall()
        kinds = [row[0] for row in trace]
        assert kinds.index("message.sent") < kinds.index("delegation.decided") < kinds.index("call.dispatched")
        original = conn.execute("SELECT c.raw_bytes FROM messages m "
            "JOIN content_objects c ON c.content_id=m.original_content_id "
            "WHERE m.call_id=%s ORDER BY c.created_at LIMIT 1", (result["call_id"],)).fetchone()[0]
        assert bytes(original).decode() == case.task_text
    replay = coordinator.route_work(task, case.task_text,
        requested_model="claude-test", reserve_tokens=100,
        output_contract="complete_python_module_v1", verifier_ref=receipt.verifier_ref)
    assert replay["call_id"] == result["call_id"]
    assert replay["shadow_call_id"] == result["shadow_call_id"]
    assert paid.calls == 1


def test_missing_verifier_goes_paid_direct_and_never_queues_qwen(router_env):
    coordinator, paid, task, case, _ = router_env
    result = coordinator.route_work(task, case.task_text,
        requested_model="claude-test", reserve_tokens=100,
        output_contract="complete_python_module_v1")
    assert result["state"] == "completed" and paid.calls == 1
    assert result["shadow_call_id"] is None
    assert result["shadow_state"] == "not_eligible"
    with psycopg.connect(coordinator.dsn) as conn:
        assert conn.execute("SELECT count(*) FROM runtime_jobs WHERE work_item_id=%s "
            "AND pool='qwen'", (task.work_item_id,)).fetchone()[0] == 0
        reason = conn.execute("SELECT reason FROM delegation_decisions WHERE work_item_id=%s "
            "AND decision_stage='parent_router' AND execution_mode='production'",
            (task.work_item_id,)).fetchone()[0]
        assert reason == "verifier_missing"


def test_route_work_is_parent_only(router_env):
    coordinator = router_env[0]
    parent = create_mcp(coordinator, audience="parent")
    child = create_mcp(coordinator, audience="claude")
    assert "route_work" in parent._tool_manager._tools
    assert "route_work" not in child._tool_manager._tools


def test_paid_answer_survives_shadow_enqueue_and_audit_failure(router_env, monkeypatch):
    coordinator, paid, task, case, receipt = router_env
    submit = coordinator.ingress.submit
    emit = coordinator.emitter.emit

    def fail_shadow_submit(*args, **kwargs):
        if kwargs.get("pool") == "qwen":
            raise RuntimeError("shadow queue unavailable")
        return submit(*args, **kwargs)

    def fail_shadow_audit(kind, **kwargs):
        if kind == EventType.ADMISSION_BLOCKED and kwargs.get("call_id"):
            raise RuntimeError("shadow audit unavailable")
        return emit(kind, **kwargs)

    monkeypatch.setattr(coordinator.ingress, "submit", fail_shadow_submit)
    monkeypatch.setattr(coordinator.emitter, "emit", fail_shadow_audit)
    result = coordinator.route_work(task, case.task_text,
        requested_model="claude-test", reserve_tokens=100,
        output_contract="complete_python_module_v1", verifier_ref=receipt.verifier_ref)
    assert result["state"] == "completed" and result["answer"] == "Claude fixed response"
    assert result["shadow_state"] == "enqueue_failed" and paid.calls == 1


def test_existing_claude_to_qwen_child_prevents_duplicate_shadow_job(router_env):
    coordinator, paid, task, case, receipt = router_env

    def paid_with_child(lease, current_task, prompt, emitter):
        paid.calls += 1
        emitter.emit(EventType.CALL_RECEIVED, task_id=current_task.task_id,
            work_item_id=current_task.work_item_id, call_id=f"child-{lease.call_id}",
            requested_model="qwen-test", config_version=lease.policy_version,
            payload={"parent_call_id": lease.call_id, "execution_mode": "production"})
        return AdapterResult(state="completed", answer="Claude answer after child",
            reported_model="claude-test")

    paid.run = paid_with_child
    result = coordinator.route_work(task, case.task_text,
        requested_model="claude-test", reserve_tokens=100,
        output_contract="complete_python_module_v1", verifier_ref=receipt.verifier_ref)
    assert result["state"] == "completed" and paid.calls == 1
    assert result["shadow_call_id"] is None
    assert result["shadow_state"] == "existing_local_delegation"


def test_paid_answer_survives_local_history_check_failure(router_env, monkeypatch):
    coordinator, paid, task, case, receipt = router_env

    def failed_history(*args, **kwargs):
        raise RuntimeError("history store unavailable")

    monkeypatch.setattr("bridge.mcp_server.existing_local_delegation", failed_history,
                        raising=False)
    result = coordinator.route_work(task, case.task_text,
        requested_model="claude-test", reserve_tokens=100,
        output_contract="complete_python_module_v1", verifier_ref=receipt.verifier_ref)
    assert result["state"] == "completed" and result["answer"] == "Claude fixed response"
    assert result["shadow_state"] == "enqueue_failed" and paid.calls == 1
