import asyncio
import json
import threading
import uuid
from types import SimpleNamespace

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from bridge.mcp_server import BridgeCoordinator, DesignMode, create_mcp
from contracts.work import TaskPacket


class ProbeCoordinator(BridgeCoordinator):
    def __init__(self):
        self.policy = "initial"
        self.ingress = SimpleNamespace(policy="initial")
        self.owned_calls = set()
        self.barrier = threading.Barrier(2, timeout=2)

    def route_work(self, task, prompt, **kwargs):
        self.policy = self.ingress.policy = task.task_id
        self.owned_calls.add(task.work_item_id)
        try:
            self.barrier.wait()
        except threading.BrokenBarrierError:
            return {"state": "serialized"}
        assert self.policy == self.ingress.policy == task.task_id
        return {"state": "overlapped", "work_item_id": task.work_item_id}

    delegate_claude = route_work


def task_packet(label):
    return {"task_id": label, "work_item_id": label, "category": "analysis",
            "scope": ["analysis"], "token_budget": 100, "call_budget": 1}


@pytest.mark.parametrize("second_tool", ["route_work", "delegate_claude", "design_mode_save_plan"])
def test_two_requests_on_one_mcp_session_can_enter_dispatch_together(second_tool, monkeypatch):
    def save_plan(design, workflow_id, *, iteration, expected_version, plan):
        return design.c.route_work(TaskPacket.model_validate(task_packet(workflow_id)), plan)

    monkeypatch.setattr(DesignMode, "save_plan", save_plan)
    coordinator = ProbeCoordinator()

    async def exercise():
        async with create_connected_server_and_client_session(create_mcp(coordinator)) as client:
            async def submit(tool, label):
                if tool == "design_mode_save_plan":
                    args = {"workflow_id": label, "iteration": 1, "expected_version": 1,
                            "plan": "Concurrency probe"}
                else:
                    args = {"task": task_packet(label), "prompt": "Concurrency probe",
                            "requested_model": "sonnet", "reserve_tokens": 100}
                if tool == "route_work":
                    args["output_contract"] = "Short answer"
                result = await client.call_tool(tool, args)
                assert not result.isError, result.content
                return json.loads(result.content[0].text)

            return await asyncio.gather(submit("route_work", "first"), submit(second_tool, "second"))

    assert asyncio.run(exercise()) == [
        {"state": "overlapped", "work_item_id": "first"},
        {"state": "overlapped", "work_item_id": "second"},
    ]
    assert coordinator.policy == coordinator.ingress.policy == "initial"
    assert coordinator.owned_calls == {"first", "second"}


def batch_item(label):
    task = task_packet(label)
    task.update(task_id="batch-parent", token_budget=200, call_budget=2,
                context={"project_root": "/example/bridge-monitor"})
    return {"task": task, "prompt": f"Analyze supplied evidence for {label}",
            "requested_model": "sonnet", "reserve_tokens": 100, "output_contract": "Cite evidence"}


def submit_batch(coordinator, items, *, independent=True):
    async def exercise():
        async with create_connected_server_and_client_session(create_mcp(coordinator)) as client:
            return await client.call_tool("route_work_batch", {"items": items, "independent": independent})

    return asyncio.run(exercise())


@pytest.mark.parametrize("failing", [None, "second"])
def test_one_batch_request_overlaps_dispatches_and_preserves_each_outcome(failing):
    class BatchProbe(ProbeCoordinator):
        def route_work(self, task, prompt, **kwargs):
            assert kwargs["_require_codex_claude"] is True
            self.policy = self.ingress.policy = task.work_item_id
            self.owned_calls.add(task.work_item_id)
            self.barrier.wait()
            assert self.policy == self.ingress.policy == task.work_item_id
            if task.work_item_id == failing:
                raise RuntimeError("private diagnostic")
            return {"state": "completed", "result_id": f"result-{task.work_item_id}", "delivered": True}

    coordinator = BatchProbe()
    result = submit_batch(coordinator, [batch_item("first"), batch_item("second")])
    assert not result.isError, result.content
    response = json.loads(result.content[0].text)
    assert response["parent_verification_required"] is True
    first, second = response["results"]
    assert first == {"work_item_id": "first", "outcome": {
        "state": "completed", "result_id": "result-first", "delivered": True}}
    if failing:
        assert second == {"work_item_id": "second", "outcome": {
            "state": "uncertain", "reason": "batch_dispatch_error",
            "call_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "cc-call:second"))}}
    else:
        assert second == {"work_item_id": "second", "outcome": {
            "state": "completed", "result_id": "result-second", "delivered": True}}
    assert coordinator.owned_calls == {"first", "second"}
    assert coordinator.policy == coordinator.ingress.policy == "initial"


@pytest.mark.parametrize("case, message", [
    ("write", "supplied-context analysis"),
    ("source", "supplied-context analysis"),
    ("duplicate", "distinct work item"),
    ("mixed_parent", "share one task"),
    ("over_budget", "shared token budget"),
    ("too_many", "at most 2"),
    ("dependent", "independent"),
    ("design_review", "Design Mode"),
])
def test_invalid_batch_dispatches_neither_item(case, message):
    coordinator = ProbeCoordinator()
    first, second = batch_item("first"), batch_item("second")
    items = [first, second]
    if case == "write":
        second["task"]["scope"] = ["repository-execution"]
    elif case == "source":
        second["task"]["context"]["source_id"] = "shared-repository"
    elif case == "duplicate":
        second["task"]["work_item_id"] = "first"
    elif case == "mixed_parent":
        second["task"]["task_id"] = "another-parent"
    elif case == "over_budget":
        second["reserve_tokens"] += 1
    elif case == "too_many":
        items.append(batch_item("third"))
    elif case == "design_review":
        for item in items:
            item["task"]["task_id"] = DesignMode.TASK_PREFIX + "workflow"

    result = submit_batch(coordinator, items, independent=case != "dependent")
    assert result.isError and message in result.content[0].text
    assert coordinator.owned_calls == set()


def test_batch_is_parent_only_and_fails_closed_when_codex_claude_is_disabled(monkeypatch):
    coordinator = ProbeCoordinator()
    monkeypatch.setattr(ProbeCoordinator, "route_work", BridgeCoordinator.route_work)
    monkeypatch.setattr(ProbeCoordinator, "_refresh_policy", lambda self: None)

    result = submit_batch(coordinator, [batch_item("first")])
    assert not result.isError, result.content
    assert json.loads(result.content[0].text)["results"] == [
        {"work_item_id": "first", "outcome": {"state": "rejected", "reason": "codex_claude_disabled"}}]

    async def child_tools():
        async with create_connected_server_and_client_session(create_mcp(coordinator, audience="claude")) as client:
            return {tool.name for tool in (await client.list_tools()).tools}

    assert "route_work_batch" not in asyncio.run(child_tools())


def test_batch_uses_existing_admission_and_delivery_with_two_stub_workers(runtime_dsn, tmp_path):
    import psycopg

    from bridge.adapters.base import AdapterResult
    from bridge.runtime.policy_startup import activate_policy
    from bridge.telemetry.emitter import TelemetryEmitter
    from bridge.telemetry.spool import DurableSpool
    from contracts.codex_claude import CodexClaudePolicy, starter_profiles
    from contracts.policy import validate_policy
    from tests.adapters.test_bridge_service import Collector
    from tests.integration.test_admission import _policy

    base = _policy()
    profiles = starter_profiles()
    profiles["sonnet"] = profiles["sonnet"].model_copy(update={"enabled": True})
    proposed = base.model_copy(update={
        "limits": base.limits.model_copy(update={"claude_global_active": 2,
            "claude_per_root_active": 2, "claude_waiting": 2}),
        "codex_claude": CodexClaudePolicy(enabled=True, profiles=profiles, max_active_workers=2),
    }).model_dump()
    capabilities = {"claude-test": {"context_tokens": 10000}}
    validation = validate_policy(proposed, capabilities)
    assert validation.snapshot is not None, validation.errors
    policy = activate_policy(runtime_dsn, proposed, capabilities,
        approved_hash=validation.snapshot.source_hash, operator_id="batch-test")
    emitter = TelemetryEmitter(DurableSpool(tmp_path / "spool.sqlite", limit_bytes=100000),
        Collector(runtime_dsn), producer_id=f"batch-test-{uuid.uuid4()}", incarnation="test")
    barrier = threading.Barrier(2, timeout=10)

    class StubClaude:
        profile_models = (profiles["sonnet"].model_id,)

        def run_profile(self, lease, task, prompt, emitter, *, profile):
            barrier.wait()
            return AdapterResult(state="completed", answer=prompt, reported_model=profile.model_id,
                                 completion_claim={"local_results": [], "route_choice": "paid_direct"})

    coordinator = BridgeCoordinator(runtime_dsn, policy, policy.version, emitter, claude_adapter=StubClaude())
    task_id = f"batch-{uuid.uuid4()}"
    items = [batch_item(label) for label in (f"{task_id}-a", f"{task_id}-b")]
    for item in items:
        item["task"].update(task_id=task_id, context={"project_root": str(tmp_path),
            "complexity": "ordinary", "risk": "low"})
    result = submit_batch(coordinator, items)
    assert not result.isError, result.content
    outcomes = [row["outcome"] for row in json.loads(result.content[0].text)["results"]]
    assert all(row["state"] == "completed" and row["delivered"] and row["result_sha256"]
               for row in outcomes), outcomes
    assert [row["answer"] for row in outcomes] == [item["prompt"] for item in items]
    assert len({row["result_id"] for row in outcomes}) == 2
    assert coordinator.owned_calls == {row["call_id"] for row in outcomes}
    with psycopg.connect(runtime_dsn) as conn:
        assert conn.execute("SELECT calls_reserved, tokens_reserved FROM runtime_task_budgets WHERE task_id=%s",
                            (task_id,)).fetchone() == (2, 200)
        assert conn.execute("SELECT count(DISTINCT lease_id), count(*) FILTER (WHERE slot_held) "
                            "FROM runtime_jobs WHERE task_id=%s", (task_id,)).fetchone() == (2, 0)
