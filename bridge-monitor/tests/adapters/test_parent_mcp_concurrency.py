import asyncio
import json
import threading
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
