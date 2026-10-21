import asyncio
import importlib
import uuid

import psycopg
import pytest

from contracts.queries import CohortQuery
from database.migrate import migrate
from service.queries.history import list_work_items, read_work_item_detail
from service.queries.metrics import query_metrics
from service.queries.snapshot import read_snapshot


def report(**changes):
    return dict(record_id=str(uuid.uuid4()), execution_id=str(uuid.uuid4()),
        session_id="direct-test-session", project_root="C:/test/project", summary="Explain sorted",
        status="completed", bypass_reason="user_instruction", reason="Codex directly",
        requested_model="test-model", observed_at="2026-10-20T10:00:00Z", **changes)


def record(dsn, data, **kwargs):
    return importlib.import_module("bridge.direct_codex").record_direct_task(
        dsn, producer_id="test-direct-parent", report=data, **kwargs)


@pytest.fixture
def direct_dsn(test_dsn):
    migrate(test_dsn)
    with psycopg.connect(test_dsn) as conn:
        conn.execute("TRUNCATE tasks,events,publication_stream,producer_sequences RESTART IDENTITY CASCADE")
        conn.execute("UPDATE publication_counter SET next_position=1 WHERE singleton=true")
    return test_dsn


def test_direct_task_is_visible_without_fake_calls_or_capacity_effects(direct_dsn):
    q = CohortQuery(origin="all", as_of="2026-10-21T10:01:00Z")
    before = query_metrics(direct_dsn, q)
    with psycopg.connect(direct_dsn) as conn:
        capacity = conn.execute("SELECT * FROM dashboard_status").fetchall()
        jobs = conn.execute("SELECT count(*) FROM runtime_jobs").fetchone()
    receipt = record(direct_dsn, report())
    page = list_work_items(direct_dsn, q)
    row = next(r for r in page if r["work_item_id"] == receipt["work_item_id"])
    assert row["execution_path"] == "direct_codex"
    assert row["bypass_reason"] == "user_instruction"
    assert row["resolution"] == "completed"
    detail = read_work_item_detail(direct_dsn, row["work_item_id"])
    assert detail["calls"] == [] and detail["models"] == []
    assert detail["direct_execution"]["summary"] == "Explain sorted"
    assert detail["direct_usage"] == []
    snapshot = read_snapshot(direct_dsn, q)
    assert snapshot.work_items[0]["execution_path"] == "direct_codex"
    after = query_metrics(direct_dsn, q)
    assert after.metrics["routed"] == before.metrics["routed"]
    assert after.metrics["not_routed"] == before.metrics["not_routed"]
    assert after.metrics["pending"] == before.metrics["pending"]
    assert after.metrics["direct_execution"].numerator == 1
    with psycopg.connect(direct_dsn) as conn:
        assert conn.execute("SELECT * FROM dashboard_status").fetchall() == capacity
        assert conn.execute("SELECT count(*) FROM runtime_jobs").fetchone() == jobs


def test_replay_conflict_and_late_usage(direct_dsn):
    module = importlib.import_module("bridge.direct_codex")
    data = report()
    first = record(direct_dsn, data)
    assert record(direct_dsn, data) == first
    with pytest.raises(ValueError, match="conflict"):
        record(direct_dsn, {**data, "reason": "changed"})
    from tests.adapters.test_direct_codex import _stream
    usage = module.parse_codex_turn(_stream(usage={"input_tokens": 42, "output_tokens": 9}), turn_index=0)
    late = {**data, "record_id": str(uuid.uuid4()), "session_id": "session-1",
            "observed_at": "2026-10-20T10:02:00Z"}
    # Session identity is immutable even when a receipt arrives later.
    with pytest.raises(ValueError, match="identity"):
        record(direct_dsn, late, usage=usage, source_turn_id="turn-1")
    proper = report()
    proper["session_id"] = "session-1"
    registered = record(direct_dsn, proper)
    late = {**proper, "record_id": str(uuid.uuid4()), "observed_at": "2026-10-20T10:02:00Z"}
    record(direct_dsn, late, usage=usage, source_turn_id="turn-1")
    record(direct_dsn, {**late, "record_id": str(uuid.uuid4())}, usage=usage, source_turn_id="turn-1")
    detail = read_work_item_detail(direct_dsn, registered["work_item_id"])
    assert len(detail["direct_usage"]) == 1
    assert detail["direct_usage"][0]["input_tokens"] == 42
    assert detail["direct_usage"][0]["scope"] == "turn"
    assert detail["direct_usage"][0]["price_basis"] is None
    assert detail["direct_execution"]["status"] == "completed"
    with pytest.raises(ValueError, match="conflict"):
        record(direct_dsn, {**late, "record_id": str(uuid.uuid4())},
               usage=module.parse_codex_turn(_stream(usage={"input_tokens": 99}), turn_index=0),
               source_turn_id="turn-1")


def test_parent_only_recording_tool():
    from bridge.mcp_server import create_mcp
    async def names(audience):
        return {t.name for t in await create_mcp(None, audience=audience).list_tools()}
    assert "record_direct_task" in asyncio.run(names("parent"))
    assert "record_direct_task" not in asyncio.run(names("claude"))


def test_terminal_status_cannot_be_rewritten_with_a_backdated_report(direct_dsn):
    data = report()
    record(direct_dsn, data)
    with pytest.raises(ValueError, match="conflict"):
        record(direct_dsn, {**data, "record_id": str(uuid.uuid4()), "status": "failed",
                            "observed_at": "2026-10-19T10:00:00Z"})


def test_usage_can_arrive_after_a_receipt_without_counters(direct_dsn):
    from tests.adapters.test_direct_codex import _stream
    module = importlib.import_module("bridge.direct_codex")
    data = report(); data["session_id"] = "session-1"
    first = record(direct_dsn, data, usage=module.parse_codex_turn(_stream()), source_turn_id="late-turn")
    assert read_work_item_detail(direct_dsn, first["work_item_id"])["direct_usage"] == []
    record(direct_dsn, {**data, "record_id": str(uuid.uuid4())},
        usage=module.parse_codex_turn(_stream(usage={"input_tokens": 12, "output_tokens": 0})),
        source_turn_id="late-turn")
    assert read_work_item_detail(direct_dsn, first["work_item_id"])["direct_usage"][0]["input_tokens"] == 12


def test_direct_work_cannot_acquire_fake_bridge_calls(direct_dsn):
    from contracts.events import EventEnvelope, EventType
    from service.ingest.store import ingest_batch
    data = report(); receipt = record(direct_dsn, data)
    event = EventEnvelope(event_id=str(uuid.uuid4()), event_type=EventType.CALL_RECEIVED,
        schema_version=1, producer_id="test-direct-parent", producer_incarnation=str(uuid.uuid4()),
        producer_sequence=1, occurred_at="2026-10-20T10:01:00Z", origin="observed",
        observation_layer="test", capture_completeness="complete", call_id="fake-call",
        work_item_id=receipt["work_item_id"])
    with pytest.raises(ValueError, match="direct"):
        ingest_batch(direct_dsn, event.producer_id, [event], [])


def test_desktop_hooks_replay_scoped_usage_once_after_a_transient_failure(direct_dsn, tmp_path, monkeypatch):
    import json
    from tests.adapters.test_desktop_capture import fixture
    from bridge.desktop_capture import capture_hook, flush_capture
    from bridge import direct_codex
    home, path, offset, raw = fixture(tmp_path)
    path.write_bytes(path.read_bytes()[:offset])
    state = tmp_path / 'state.sqlite'
    base = {'session_id': 'session-1', 'turn_id': 'turn-1', 'cwd': 'C:/test/project',
            'transcript_path': str(path), 'model': 'runtime-model'}
    capture_hook({**base, 'hook_event_name': 'UserPromptSubmit', 'prompt': 'PRIVATE'},
                 codex_home=home, state_path=state)
    with path.open('ab') as f: f.write(raw)
    capture_hook({**base, 'hook_event_name': 'Stop'}, codex_home=home, state_path=state)
    real = direct_codex.record_direct_task
    def unavailable(*args, **kwargs):
        raise ConnectionError('secret detail must not be retained')
    monkeypatch.setattr(direct_codex, 'record_direct_task', unavailable)
    first = flush_capture(dsn=direct_dsn, codex_home=home, state_path=state)
    assert first[0]['recorded'] is False
    assert b'secret detail' not in state.read_bytes()
    monkeypatch.setattr(direct_codex, 'record_direct_task', real)
    assert flush_capture(dsn=direct_dsn, codex_home=home, state_path=state)[0]['recorded'] is True
    assert flush_capture(dsn=direct_dsn, codex_home=home, state_path=state) == []
    with psycopg.connect(direct_dsn) as conn:
        work = conn.execute('SELECT work_item_id FROM direct_executions').fetchone()[0]
    detail = read_work_item_detail(direct_dsn, work)
    assert detail['direct_execution']['metadata_provenance'] == 'desktop_hook'
    assert detail['direct_execution']['bypass_reason'] == 'not_reported'
    assert len(detail['direct_usage']) == 1
    usage = detail['direct_usage'][0]
    assert usage['input_tokens'] == 12 and usage['output_tokens'] == 0
    assert usage['provenance'] == 'codex_desktop_usage_record'
    assert usage['evidence']['runtime_model'] == 'runtime-model'
    assert usage['evidence']['runtime_provider'] == 'openai'
    assert usage['evidence']['attribution'] == 'desktop_turn_capture'
    assert detail['calls'] == []


def test_desktop_capture_preserves_an_explicit_reason_on_the_same_execution(direct_dsn, tmp_path):
    from tests.adapters.test_desktop_capture import fixture
    from bridge.desktop_capture import capture_hook, flush_capture
    home, path, offset, raw = fixture(tmp_path)
    path.write_bytes(path.read_bytes()[:offset])
    state = tmp_path / 'state.sqlite'
    base = {'session_id': 'session-1', 'turn_id': 'turn-1', 'cwd': 'C:/test/project',
            'transcript_path': str(path), 'model': 'runtime-model'}
    metadata = {**report(), 'execution_id': 'codex-desktop:session-1:turn-1',
                'session_id': 'session-1', 'project_root': base['cwd'],
                'reason': 'User explicitly authorized direct execution for this turn.'}
    first = importlib.import_module('bridge.direct_codex').record_direct_task(
        direct_dsn, producer_id='bridge-monitor-parent', report=metadata)
    capture_hook({**base, 'hook_event_name': 'UserPromptSubmit'}, codex_home=home, state_path=state)
    with path.open('ab') as stream:
        stream.write(raw)
    capture_hook({**base, 'hook_event_name': 'Stop'}, codex_home=home, state_path=state)
    assert flush_capture(dsn=direct_dsn, codex_home=home, state_path=state)[0]['recorded']
    detail = read_work_item_detail(direct_dsn, first['work_item_id'])
    assert detail['direct_execution']['reason'] == metadata['reason']
    assert detail['direct_execution']['bypass_reason'] == metadata['bypass_reason']
    assert len(detail['direct_usage']) == 1
    with psycopg.connect(direct_dsn) as conn:
        assert conn.execute('SELECT count(*) FROM direct_executions').fetchone()[0] == 1
