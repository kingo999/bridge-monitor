import asyncio
import uuid

import psycopg
import pytest

from bridge.mcp_server import BridgeCoordinator, create_mcp
from bridge.telemetry.emitter import TelemetryEmitter
from bridge.telemetry.spool import DurableSpool
from contracts.events import EventType
from service.ingest.store import ingest_batch
from tests.integration.test_admission import _policy
from tests.integration.test_dispositions import _seed_chain
from tests.integration.test_parent_acknowledgements import _paid_result
from tests.integration.test_ownership import _setup


class Collector:
    def __init__(self, dsn):
        self.dsn = dsn

    def ingest_batch(self, producer, events, content):
        return ingest_batch(self.dsn, producer, events, content)


def _coordinator(dsn, tmp_path):
    with psycopg.connect(dsn) as conn:
        version = conn.execute("SELECT version FROM runtime_active_policy "
                               "WHERE singleton=true").fetchone()[0]
    emitter = TelemetryEmitter(DurableSpool(tmp_path / f"{uuid.uuid4()}.sqlite",
        limit_bytes=100000), Collector(dsn), producer_id=f"parent-{uuid.uuid4()}",
        incarnation="test-parent")
    return BridgeCoordinator(dsn, _policy(), version, emitter, claude_adapter=None)


def test_parent_mcp_reports_owned_result_and_publishes_once(runtime_dsn, tmp_path):
    claude, _, bindings = _seed_chain(runtime_dsn, recipient_call_id="gpt")
    paid = _paid_result(runtime_dsn, claude, bindings[claude])
    service = _coordinator(runtime_dsn, tmp_path)
    service.owned_calls.add(claude)
    report = {"acknowledgement_id": str(uuid.uuid4()),
              "result_id": paid.answer.result_id, "input_sha256": paid.answer.sha256,
              "judgment": "accepted", "answer_text": "518", "reason": "kept arithmetic"}
    first = service.acknowledge_claude_result(claude, report)
    second = service.acknowledge_claude_result(claude, report)
    assert first == second
    assert first["judgment"] == "accepted"
    with psycopg.connect(runtime_dsn) as conn:
        rows = conn.execute("SELECT payload->>'disposition_id' FROM events WHERE "
            "event_type=%s AND payload->>'disposition_id'=%s",
            (EventType.DISPOSITION_RECORDED.value, report["acknowledgement_id"])).fetchall()
    assert rows == [(report["acknowledgement_id"],)]

    unrelated = _coordinator(runtime_dsn, tmp_path)
    assert service.get_execution_status(claude)["result_sha256"] == paid.answer.sha256
    assert unrelated.get_execution_status(claude)["result_sha256"] is None
    with pytest.raises(PermissionError):
        unrelated.acknowledge_claude_result(claude, report)
    invalid = {**report, "acknowledgement_id": str(uuid.uuid4()),
               "input_sha256": "0" * 64}
    with pytest.raises(ValueError):
        service.acknowledge_claude_result(claude, invalid)
    with psycopg.connect(runtime_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM dispositions WHERE disposition_id=%s",
                            (invalid["acknowledgement_id"],)).fetchone()[0] == 0


def test_parent_report_event_commits_with_disposition_without_emitter(runtime_dsn, tmp_path):
    claude, _, bindings = _seed_chain(runtime_dsn, recipient_call_id="gpt")
    paid = _paid_result(runtime_dsn, claude, bindings[claude])
    service = _coordinator(runtime_dsn, tmp_path)
    service.owned_calls.add(claude)
    report = {"acknowledgement_id": str(uuid.uuid4()),
              "result_id": paid.answer.result_id, "input_sha256": paid.answer.sha256,
              "judgment": "accepted", "answer_text": "518"}

    def unavailable(*args, **kwargs):
        raise RuntimeError("emitter unavailable after DB commit")

    service.emitter.emit = unavailable
    receipt = service.acknowledge_claude_result(claude, report)
    assert receipt["acknowledgement_id"] == report["acknowledgement_id"]
    with psycopg.connect(runtime_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM events WHERE event_type=%s "
            "AND payload->>'disposition_id'=%s", (EventType.DISPOSITION_RECORDED.value,
            report["acknowledgement_id"])).fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM publication_stream p JOIN events e "
            "ON e.event_id=p.event_id WHERE e.payload->>'disposition_id'=%s",
            (report["acknowledgement_id"],)).fetchone()[0] == 1


def test_acknowledgement_tool_exists_only_for_parent_audience(runtime_dsn, tmp_path):
    _setup(runtime_dsn)
    service = _coordinator(runtime_dsn, tmp_path)

    async def names(audience):
        return {item.name for item in await create_mcp(service, audience=audience).list_tools()}

    assert "acknowledge_claude_result" in asyncio.run(names("parent"))
    assert "acknowledge_claude_result" not in asyncio.run(names("claude"))
