import uuid
from datetime import datetime, timedelta, timezone

import psycopg

from bridge.adapters.codex_ingress import CodexIngress
from bridge.telemetry.emitter import TelemetryEmitter
from bridge.telemetry.spool import DurableSpool
from contracts.work import CallRequest, TaskPacket
from service.ingest.store import ingest_batch
from tests.integration.test_ownership import _setup


class Collector:
    def __init__(self, dsn): self.dsn = dsn
    def ingest_batch(self, producer, events, content):
        return ingest_batch(self.dsn, producer, events, content)


def test_ingress_persists_exact_prompt_before_durable_admission(runtime_dsn, tmp_path):
    dsn = runtime_dsn
    policy, version = _setup(dsn)
    emitter = TelemetryEmitter(DurableSpool(tmp_path / "spool.sqlite", limit_bytes=100000),
        Collector(dsn), producer_id=f"ingress-{uuid.uuid4()}", incarnation="boot-1")
    call = f"call-{uuid.uuid4()}"
    request = CallRequest(call_id=call, requested_model="claude-test",
        task=TaskPacket(task_id=f"task-{uuid.uuid4()}", work_item_id=f"work-{uuid.uuid4()}",
                        category="analysis", scope=["read"], token_budget=1000, call_budget=2))
    decision = CodexIngress(dsn, emitter, policy, version).submit(request,
        prompt="Exact GPT request with Ω", pool="claude", reserve_tokens=100)
    assert decision.state == "queued"
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT c.raw_bytes,j.prompt_content_id FROM runtime_jobs j "
                           "JOIN content_objects c ON c.content_id=j.prompt_content_id "
                           "WHERE j.call_id=%s", (call,)).fetchone()
        assert bytes(row[0]) == "Exact GPT request with Ω".encode("utf-8")
        assert conn.execute("SELECT count(*) FROM events WHERE call_id=%s AND event_type='call.queued'",
                            (call,)).fetchone()[0] == 1


def test_rejected_local_admission_retains_paid_route_and_single_prompt(runtime_dsn, tmp_path):
    dsn = runtime_dsn
    policy, version = _setup(dsn)
    emitter = TelemetryEmitter(DurableSpool(tmp_path / "spool.sqlite", limit_bytes=100000),
        Collector(dsn), producer_id=f"ingress-{uuid.uuid4()}", incarnation="boot-1")
    call = f"call-{uuid.uuid4()}"
    request = CallRequest(call_id=call, requested_model="qwen-test",
        task=TaskPacket(task_id=f"task-{uuid.uuid4()}", work_item_id=f"work-{uuid.uuid4()}",
                        category="analysis", scope=["read"], token_budget=1000, call_budget=2))
    ingress = CodexIngress(dsn, emitter, policy, version)
    decision = ingress.submit(request, prompt="Exact child request", pool="qwen",
        reserve_tokens=1200, local_cutoff_at=datetime.now(timezone.utc) + timedelta(seconds=20))
    assert decision.state == "rejected" and decision.reason == "root_budget_exhausted"
    assert ingress.record_paid_decision(request, reason=decision.reason,
        manifest={"state": "no_guidance", "version": 1}, gate_facts={"admission": "rejected"})
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT route,reason FROM delegation_decisions WHERE child_call_id=%s",
                            (call,)).fetchone() == ("paid", "root_budget_exhausted")
        assert conn.execute("SELECT count(*) FROM messages WHERE call_id=%s", (call,)).fetchone()[0] == 1
