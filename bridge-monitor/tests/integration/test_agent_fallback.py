import hashlib
import json
import uuid

import httpx
import psycopg

from bridge.adapters.base import AdapterResult
from bridge.adapters.qwen_agent import QwenAgentAdapter
from bridge.mcp_server import BridgeCoordinator
from bridge.runtime.policy_startup import activate_policy
from bridge.telemetry.emitter import TelemetryEmitter
from bridge.telemetry.spool import DurableSpool
from contracts.events import EventType
from contracts.policy import PolicyInput, validate_policy
from contracts.work import TaskPacket
from tests.adapters.test_ingress import Collector
from tests.integration.test_admission import _policy


def test_silent_agent_job_returns_paid_continuation_and_retains_qwen_slot(runtime_dsn, tmp_path):
    dsn = runtime_dsn
    proposed = _policy().model_dump()
    # Allow database setup under full-suite load before testing silent polling.
    proposed["limits"]["qwen_execution_seconds"] = 8
    proposed["deployment"].update(qwen_model_profile="qwen-test",
                                  qwen_model_digest="a" * 64,
                                  qwen_output_tokens=20)
    caps = {"claude-test": {"context_tokens": 10000}}
    version = activate_policy(dsn, proposed, caps,
        approved_hash=validate_policy(proposed, caps).snapshot.source_hash,
        operator_id="agent-test").version
    emitter = TelemetryEmitter(DurableSpool(tmp_path / "agent-spool.sqlite", limit_bytes=1000000),
        Collector(dsn), producer_id=f"agent-fallback-{uuid.uuid4()}", incarnation="boot-1")
    child_result = None
    status_polls = 0
    ack = None

    def agent(request):
        nonlocal ack, status_polls
        if request.url.path == "/v1/health":
            return httpx.Response(200, json={"model_ready": True, "model": "qwen-test",
                "model_digest": "a" * 64, "observed_digest": "a" * 64})
        if request.url.path == "/v1/jobs" and request.method == "POST":
            wire = json.loads(request.content)
            canonical = json.dumps({key: value for key, value in wire.items()
                                    if key != "job_id"}, sort_keys=True,
                                   separators=(",", ":"), ensure_ascii=False)
            ack = {"job_id": wire["job_id"], "request_sha256": hashlib.sha256(
                canonical.encode()).hexdigest(), "agent_incarnation": "synthetic-agent",
                "state": "accepted"}
            return httpx.Response(202, json=ack)
        if request.url.path.endswith("/cancel"):
            return httpx.Response(200, json={"job_id": ack["job_id"], "state": "unknown"})
        status_polls += 1
        return httpx.Response(200, json={**ack, "state": "accepted", "sequence": 0,
                                         "gap": False, "deltas": [], "terminal": None})

    qwen = QwenAgentAdapter(httpx.Client(transport=httpx.MockTransport(agent)),
        endpoint="http://agent", token="test-token", model="qwen-test",
        expected_digest="a" * 64, context_tokens=1000, output_tokens=20,
        timeout_seconds=8, first_output_seconds=0.5, idle_output_seconds=0.5,
        ack_seconds=0.1, poll_seconds=0.01, poll_timeout_seconds=0.1,
        poll_failures=2, keep_alive="10m", temperature=0.0)

    class Claude:
        def run(self, lease, task, prompt, sink):
            nonlocal child_result
            sink.emit(EventType.MODEL_SELECTED, call_id=lease.call_id,
                attempt_id=f"{lease.call_id}:attempt:1",
                model_execution_id=f"{lease.call_id}:model:1", requested_model="claude-test")
            sink.emit(EventType.MODEL_REPORTED, call_id=lease.call_id,
                attempt_id=f"{lease.call_id}:attempt:1",
                model_execution_id=f"{lease.call_id}:model:1", reported_model="claude-test")
            child = BridgeCoordinator(dsn, PolicyInput.model_validate(proposed), version, emitter,
                claude_adapter=None, qwen_adapter=qwen,
                parent_call_id=lease.call_id, parent_lease_id=lease.lease_id,
                local_capability={"context_tokens": 1000, "byte_upper_bound_validated": True,
                                  "client_timeout_verified": True},
                paid_capability={"context_tokens": 10000, "client_timeout_verified": True,
                                 "reported_models": ["claude-test"]})
            child_result = child.delegate_local_analysis("Small delegated task")
            return AdapterResult(state="completed", answer="Paid continuation",
                                 reported_model="claude-test")

    parent = BridgeCoordinator(dsn, PolicyInput.model_validate(proposed), version, emitter,
                               claude_adapter=Claude())
    task = TaskPacket(task_id=f"task-{uuid.uuid4()}", work_item_id=f"work-{uuid.uuid4()}",
                      category="analysis", scope=["read"], token_budget=1000, call_budget=2)
    parent_result = parent.delegate_claude(task, "Root task", requested_model="claude-test",
                                           reserve_tokens=100)
    assert parent_result["state"] == "completed"
    assert child_result["state"] == "paid_continuation_required"
    assert child_result["route"] == "paid" and child_result["not_routed"] is False
    assert status_polls > 1
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
                            (child_result["child_call_id"],)).fetchone() == ("fallback", True)
