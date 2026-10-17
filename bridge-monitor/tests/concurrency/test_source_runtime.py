"""Source-read launch behavior through the coordinator and durable admission."""

import uuid

import psycopg

from bridge.adapters.base import AdapterResult
from bridge.mcp_server import BridgeCoordinator
from bridge.runtime.workspaces import fingerprint_source
from bridge.telemetry.emitter import TelemetryEmitter
from bridge.telemetry.spool import DurableSpool
from contracts.work import CallRequest, TaskPacket
from service.ingest.store import ingest_batch
from tests.integration.test_ownership import _setup


def _service(dsn, tmp_path, source, adapter):
    policy, version = _setup(dsn)

    class Collector:
        def ingest_batch(self, producer, events, content):
            return ingest_batch(dsn, producer, events, content)

    emitter = TelemetryEmitter(DurableSpool(tmp_path / "spool.sqlite", limit_bytes=100000),
        Collector(), producer_id=f"bridge-{uuid.uuid4()}", incarnation="boot-1")
    return BridgeCoordinator(dsn, policy, version, emitter, claude_adapter=adapter,
        source_roots={"repo": str(source)}, workspace_base=tmp_path / "workspaces",
        workspace_max_bytes=1024)


def _task():
    return TaskPacket(task_id=f"task-{uuid.uuid4()}", work_item_id=f"work-{uuid.uuid4()}",
        category="analysis", scope=["source-read"], token_budget=1000, call_budget=2)


def test_source_read_call_launches_from_private_copy(runtime_dsn, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "main.py").write_text("original", encoding="utf-8")
    observed = []

    class SourceClaude:
        def run(self, lease, task, prompt, sink, *, workspace):
            observed.append((workspace, (workspace / "main.py").read_text(encoding="utf-8")))
            assert workspace != source
            (workspace / "main.py").write_text("worker copy", encoding="utf-8")
            return AdapterResult(state="completed", answer="analysis complete",
                                 reported_model="claude-test")

    service = _service(runtime_dsn, tmp_path, source, SourceClaude())
    result = service.delegate_claude(_task(), "inspect main.py", requested_model="claude-test",
                                     reserve_tokens=100, source_id="repo")
    assert result["state"] == "completed"
    assert len(observed) == 1 and observed[0][1] == "original"
    assert (source / "main.py").read_text(encoding="utf-8") == "original"
    with psycopg.connect(runtime_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM runtime_source_locks WHERE holder_call_id=%s",
                            (result["call_id"],)).fetchone()[0] == 0


def test_source_change_after_admission_aborts_before_adapter_launch(runtime_dsn, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    path = source / "main.py"
    path.write_text("before", encoding="utf-8")

    class NeverLaunch:
        def run(self, *args, **kwargs):
            raise AssertionError("changed source must not launch Claude")

    service = _service(runtime_dsn, tmp_path, source, NeverLaunch())
    task = _task().model_copy(update={"context": {"source_id": "repo"}})
    call = CallRequest(call_id=str(uuid.uuid4()), task=task, requested_model="claude-test",
                       parent_call_id=f"gpt:{task.task_id}")
    snapshot = fingerprint_source(source, max_bytes=1024)
    decision = service.ingress.submit(call, prompt="inspect main.py", pool="claude",
        reserve_tokens=100, source_snapshot=snapshot, source_mode="read")
    assert decision.state == "queued"
    path.write_text("after", encoding="utf-8")
    result = service.run_one("claude", call_id=call.call_id)
    assert result == {"state": "rejected", "call_id": call.call_id,
                      "reason": "source_snapshot_changed_or_unavailable"}
    with psycopg.connect(runtime_dsn) as conn:
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
                            (call.call_id,)).fetchone() == ("cancelled", False)
        assert conn.execute("SELECT count(*) FROM runtime_source_locks WHERE holder_call_id=%s",
                            (call.call_id,)).fetchone()[0] == 0
