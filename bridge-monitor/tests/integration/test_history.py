from pathlib import Path

import psycopg
import pytest

from contracts.queries import CohortQuery
from database.migrate import migrate
from scripts.replay_fixtures import replay_fixture
from service.queries.history import list_work_items, inspect_exchange, read_work_item_detail


FIXTURE = Path(__file__).parents[1] / "fixtures" / "demonstration.json"


@pytest.fixture
def seeded(test_dsn):
    migrate(test_dsn)
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE publication_stream, events, content_objects, producer_sequences, tasks RESTART IDENTITY CASCADE")
        conn.execute("UPDATE publication_counter SET next_position=1 WHERE singleton=true")
    replay_fixture(test_dsn, FIXTURE)
    return test_dsn


def test_history_is_bounded_and_inspector_returns_exact_observed_bodies(seeded):
    page = list_work_items(seeded, CohortQuery(origin="synthetic"), limit=10, offset=0)
    assert len(page) == 10
    assert "raw_bytes" not in page[0]
    selected = inspect_exchange(seeded, "demo-work-061", role="operator")
    assert selected["work_item_id"] == "demo-work-061"
    assert selected["original_prompt"] == "What is 2 + 2?\n"
    assert selected["local_response"] == "4\n"


def test_detail_uses_confirmed_resolution_for_paid_direct_work(seeded):
    detail = read_work_item_detail(seeded, "demo-work-060")
    assert detail is not None
    assert detail["work_item"]["resolution"] == "paid_direct"
    assert detail["calls"] == []
    assert detail["results"] == []


def test_detail_retains_per_execution_usage_and_unknown_cache_counts(seeded):
    with psycopg.connect(seeded) as conn:
        model = conn.execute("""SELECT me.model_execution_id FROM model_executions me
            JOIN attempts a ON a.attempt_id=me.attempt_id
            JOIN calls c ON c.call_id=a.call_id WHERE c.work_item_id=%s LIMIT 1""",
            ("demo-work-061",)).fetchone()[0]
        conn.execute("""INSERT INTO usage_observations
            (observation_id,model_execution_id,scope,is_cumulative,input_tokens,
             output_tokens,cache_read_input_tokens,cache_creation_input_tokens,
             provenance,price_basis) VALUES
            ('usage-complete-061',%s,'execution',true,4,80,2440,0,
             'synthetic_usage',NULL),
            ('usage-partial-061',%s,'execution',false,0,3,NULL,NULL,
             'synthetic_usage',NULL)""", (model, model))

    detail = read_work_item_detail(seeded, "demo-work-061")
    assert detail["usage"] == [
        {"observation_id": "usage-complete-061", "model_execution_id": model,
         "scope": "execution", "is_cumulative": True, "input_tokens": 4,
         "output_tokens": 80, "cache_read_input_tokens": 2440,
         "cache_creation_input_tokens": 0, "provenance": "synthetic_usage",
         "price_basis": None},
        {"observation_id": "usage-partial-061", "model_execution_id": model,
         "scope": "execution", "is_cumulative": False, "input_tokens": 0,
         "output_tokens": 3, "cache_read_input_tokens": None,
         "cache_creation_input_tokens": None, "provenance": "synthetic_usage",
         "price_basis": None},
    ]


def test_history_list_uses_recorded_resolution_timestamp(seeded):
    from datetime import timedelta

    with psycopg.connect(seeded) as conn:
        recorded_at = conn.execute("SELECT max(recorded_at) FROM work_resolutions "
                                   "WHERE work_item_id=%s", ("demo-work-060",)).fetchone()[0]
    assert recorded_at is not None
    page = list_work_items(seeded, CohortQuery(origin="synthetic"), limit=100)
    row = next(item for item in page if item["work_item_id"] == "demo-work-060")
    assert row["resolution"] == "paid_direct"
    assert row["resolved_at"] == recorded_at
    earlier = list_work_items(seeded, CohortQuery(origin="synthetic",
                              as_of=recorded_at - timedelta(microseconds=1)), limit=100)
    earlier_row = next(item for item in earlier if item["work_item_id"] == "demo-work-060")
    assert earlier_row["resolution"] is None
    assert earlier_row["resolved_at"] is None


def test_inspection_rejects_non_operator_and_unknown_item(seeded):
    with pytest.raises(PermissionError):
        inspect_exchange(seeded, "demo-work-061", role="producer")
    assert inspect_exchange(seeded, "missing", role="operator") is None


def test_capacity_and_capture_fixtures_project_through_milestone_one_status(seeded):
    migration = (Path(__file__).parents[2] / "database" / "migrations" /
                 "001_event_store.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS dashboard_status" in migration
    with psycopg.connect(seeded) as conn:
        assert conn.execute("SELECT 1 FROM schema_migrations WHERE version='001_event_store'").fetchone()
        assert conn.execute("""SELECT active,queued,held_uncertain,blocked,
            capture_state,known_gaps,spool_pressure FROM dashboard_status WHERE singleton=true""").fetchone() == (
            1, 2, 1, 3, "healthy", 1, 0.8)


def test_each_delegated_demonstration_item_has_visible_prompt_and_response(seeded):
    selected = inspect_exchange(seeded, "demo-work-100", role="operator")
    assert selected["original_prompt"] == "What is 2 + 2?\n"
    assert selected["local_response"] == "4\n"


def test_synthetic_delegations_retain_requested_and_reported_models(seeded):
    with psycopg.connect(seeded) as conn:
        assert conn.execute("""SELECT count(*) FROM model_executions
            WHERE requested_model='qwen-seed' AND reported_model='qwen-seed'""").fetchone()[0] == 40
        assert conn.execute("SELECT count(*) FROM attempts").fetchone()[0] == 40


def test_inspector_keeps_paid_exchange_out_of_local_prompt_and_response(seeded):
    from datetime import datetime, timezone
    from contracts.content import ContentObject
    from contracts.events import EventEnvelope, EventType
    from service.ingest.store import ingest_batch

    early = datetime(2026, 10, 16, 10, 1, tzinfo=timezone.utc)
    def paid_event(identifier, sequence, kind, payload):
        return EventEnvelope(event_id=identifier, event_type=kind, schema_version=1,
            producer_id="paid-inspection", producer_incarnation="first",
            producer_sequence=sequence, occurred_at=early, origin="synthetic",
            observation_layer="test", capture_completeness="complete",
            task_id="demo-task-001", work_item_id="demo-work-061",
            call_id="paid-call-061", payload=payload)

    paid_content = [
        ContentObject.from_bytes("paid-prompt-061", b"Paid prompt\n", role="user",
            direction="out", content_type="text/plain", origin="synthetic", capture_layer="test"),
        ContentObject.from_bytes("paid-response-061", b"Paid answer\n", role="assistant",
            direction="in", content_type="text/plain", origin="synthetic", capture_layer="test"),
    ]
    ingest_batch(seeded, "paid-inspection", [
        paid_event("paid-call-061", 1, EventType.CALL_RECEIVED, {}),
        paid_event("paid-message-061", 2, EventType.MESSAGE_SENT,
                   {"message_id": "paid-message-061", "original_content_id": "paid-prompt-061"}),
        paid_event("paid-result-061", 3, EventType.RESULT_DELIVERED,
                   {"result_id": "paid-result-061", "delivered_content_id": "paid-response-061"}),
    ], paid_content)
    selected = inspect_exchange(seeded, "demo-work-061", role="operator")
    assert selected["original_prompt_content_id"] == "demo-prompt-061"
    assert selected["local_response_content_id"] == "demo-response-061"


def test_agent_status_events_persist_separate_from_captured_content(seeded):
    from datetime import datetime, timezone
    from contracts.content import ContentObject
    from contracts.events import EventEnvelope, EventType
    from service.ingest.store import ingest_batch, read_content

    with psycopg.connect(seeded) as conn:
        call_id = conn.execute("SELECT call_id FROM calls WHERE work_item_id=%s LIMIT 1",
                               ("demo-work-061",)).fetchone()[0]
    instant = datetime.now(timezone.utc)

    def event(sequence, kind, payload):
        return EventEnvelope(event_id=f"agent-status-{sequence}", event_type=kind,
            schema_version=1, producer_id="agent-status-test", producer_incarnation="first",
            producer_sequence=sequence, occurred_at=instant, origin="synthetic",
            observation_layer="bridge", capture_completeness="complete",
            task_id="demo-task-001", work_item_id="demo-work-061",
            call_id=call_id, attempt_id=f"{call_id}:attempt:1", payload=payload)

    text = b"Captured Qwen delta"
    content = ContentObject.from_bytes("agent-delta-061", text, role="assistant",
        direction="in", content_type="text/plain", origin="synthetic",
        capture_layer="bridge", message_id="agent-msg-061")
    ingest_batch(seeded, "agent-status-test", [
        event(1, EventType.ATTEMPT_STATUS,
              {"agent_state": "streaming", "agent_reachable": True}),
        event(2, EventType.RESPONSE_DELTA,
              {"content_id": "agent-delta-061", "message_id": "agent-msg-061",
               "chunk_sequence": 1}),
        event(3, EventType.ATTEMPT_STATUS,
              {"agent_state": "streaming", "agent_reachable": False}),
        event(4, EventType.ATTEMPT_UNCERTAIN, {"reason": "idle_output_deadline"}),
    ], [content])
    detail = read_work_item_detail(seeded, "demo-work-061")
    status = next(s for s in detail["agent_statuses"] if s["call_id"] == call_id)
    assert status["agent_state"] == "streaming"
    assert status["agent_reachable"] is False
    assert status["last_model_output_at"] == instant
    assert status["timeout_reason"] == "idle_output_deadline"
    assert read_content(seeded, "agent-delta-061", role="operator") == text


def test_synthetic_agent_adapter_captures_prompt_and_answer_in_postgres(seeded, tmp_path):
    import hashlib
    import json
    from types import SimpleNamespace

    import httpx

    from bridge.adapters.qwen_agent import QwenAgentAdapter
    from bridge.startup import DirectCollector
    from bridge.telemetry.emitter import TelemetryEmitter
    from bridge.telemetry.spool import DurableSpool
    from service.ingest.store import read_content

    with psycopg.connect(seeded) as conn:
        call_id = conn.execute("SELECT call_id FROM calls WHERE work_item_id=%s LIMIT 1",
                               ("demo-work-061",)).fetchone()[0]
    ack = None

    def agent(request):
        nonlocal ack
        if request.method == "POST" and request.url.path == "/v1/jobs":
            wire = json.loads(request.content)
            canonical = json.dumps({key: value for key, value in wire.items()
                                    if key != "job_id"}, sort_keys=True,
                                   separators=(",", ":"), ensure_ascii=False)
            ack = {"job_id": call_id, "request_sha256": hashlib.sha256(
                canonical.encode()).hexdigest(), "agent_incarnation": "synthetic-agent",
                "state": "accepted"}
            return httpx.Response(202, json=ack)
        if request.method == "GET":
            return httpx.Response(200, json={**ack, "state": "completed", "gap": False,
                "sequence": 1, "deltas": [{"sequence": 1, "text": "Synthetic answer"}],
                "terminal": {"model": "qwen", "done": True,
                             "prompt_eval_count": 3, "eval_count": 2}})
        raise AssertionError(request.url)

    adapter = QwenAgentAdapter(httpx.Client(transport=httpx.MockTransport(agent)),
        endpoint="http://agent", token="test-token", model="qwen",
        expected_digest="a" * 64, context_tokens=1000, output_tokens=100,
        timeout_seconds=2, first_output_seconds=1, idle_output_seconds=1,
        ack_seconds=1, poll_seconds=0.01, poll_timeout_seconds=1,
        poll_failures=2, keep_alive="10m", temperature=0.0)
    emitter = TelemetryEmitter(DurableSpool(tmp_path / "spool.sqlite", limit_bytes=1000000),
        DirectCollector(seeded), producer_id="synthetic-agent-adapter",
        incarnation="first")
    lease = SimpleNamespace(call_id=call_id, lease_id="synthetic-lease", fencing_token=1,
                            policy_version="synthetic-policy")
    task = SimpleNamespace(task_id="demo-task-001", work_item_id="demo-work-061")
    result = adapter.run(lease, "Synthetic prompt", emitter, task=task)
    assert result.state == "completed" and result.answer == "Synthetic answer"
    with psycopg.connect(seeded) as conn:
        captured = conn.execute("""SELECT event_type,payload FROM events
            WHERE producer_id='synthetic-agent-adapter' ORDER BY producer_sequence""").fetchall()
    types = [row[0] for row in captured]
    assert "attempt.status" in types and "response.delta" in types
    prompt_id = next(p["original_content_id"] for kind, p in captured if kind == "message.sent")
    answer_id = next(p["content_id"] for kind, p in captured if kind == "response.completed")
    assert read_content(seeded, prompt_id, role="operator") == b"Synthetic prompt"
    assert read_content(seeded, answer_id, role="operator") == b"Synthetic answer"
    with psycopg.connect(seeded) as conn:
        hashes = conn.execute("SELECT content_id,sha256 FROM content_objects "
                              "WHERE content_id=ANY(%s)", ([prompt_id, answer_id],)).fetchall()
    assert dict(hashes) == {
        prompt_id: hashlib.sha256(b"Synthetic prompt").hexdigest(),
        answer_id: hashlib.sha256(b"Synthetic answer").hexdigest(),
    }


def test_agent_status_survives_more_than_a_thousand_later_deltas(seeded):
    from datetime import datetime, timezone
    from contracts.events import EventEnvelope, EventType
    from service.ingest.store import ingest_batch

    with psycopg.connect(seeded) as conn:
        call_id = conn.execute("SELECT call_id FROM calls WHERE work_item_id=%s LIMIT 1",
                               ("demo-work-061",)).fetchone()[0]
    status = EventEnvelope(event_id="window-agent-status", event_type=EventType.ATTEMPT_STATUS,
        schema_version=1, producer_id="window-test", producer_incarnation="first",
        producer_sequence=1, occurred_at=datetime.now(timezone.utc), origin="synthetic",
        observation_layer="bridge", capture_completeness="complete",
        task_id="demo-task-001", work_item_id="demo-work-061", call_id=call_id,
        payload={"agent_state": "streaming", "agent_reachable": True})
    ingest_batch(seeded, "window-test", [status], [])
    with psycopg.connect(seeded) as conn:
        conn.execute("""INSERT INTO events
            (event_id,event_type,schema_version,producer_id,producer_incarnation,
             producer_sequence,occurred_at,received_at,origin,observation_layer,
             capture_completeness,task_id,work_item_id,call_id,payload,payload_hash)
            SELECT 'window-noise-' || n,'response.delta',1,'window-noise','first',n,
                now() + n * interval '1 microsecond',now(),'synthetic','test','complete',
                'demo-task-001','demo-work-061','paid-noise','{}'::jsonb,'test'
            FROM generate_series(1,1100) AS n""")
    detail = read_work_item_detail(seeded, "demo-work-061")
    assert next(s for s in detail["agent_statuses"] if s["call_id"] == call_id)[
        "agent_state"] == "streaming"


def test_long_timeline_keeps_first_events_and_latest_activity(seeded):
    with psycopg.connect(seeded) as conn:
        first_id = conn.execute("""SELECT event_id FROM events WHERE work_item_id=%s
            ORDER BY occurred_at,event_id LIMIT 1""", ("demo-work-061",)).fetchone()[0]
        conn.execute("""INSERT INTO events
            (event_id,event_type,schema_version,producer_id,producer_incarnation,
             producer_sequence,occurred_at,received_at,origin,observation_layer,
             capture_completeness,task_id,work_item_id,call_id,payload,payload_hash)
            SELECT 'timeline-noise-' || n,'response.delta',1,'timeline-noise','first',n,
                now() + n * interval '1 microsecond',now(),'synthetic','test','complete',
                'demo-task-001','demo-work-061','timeline-call','{}'::jsonb,'test'
            FROM generate_series(1,700) AS n""")

    detail = read_work_item_detail(seeded, "demo-work-061")
    assert detail["events_truncated"] is True
    assert len(detail["events"]) == 500
    assert detail["events"][0]["event_id"] == first_id
    assert detail["events"][-1]["event_id"] == "timeline-noise-700"
    assert detail["events"][99]["occurred_at"] < detail["events"][100]["occurred_at"]
    assert detail["events_gap_after"] == 100
    smaller = read_work_item_detail(seeded, "demo-work-061", head_events=2, tail_events=3)
    assert len(smaller["events"]) == 5
    assert smaller["events_gap_after"] == 2
    assert smaller["events"][0]["event_id"] == first_id
    assert smaller["events"][-1]["event_id"] == "timeline-noise-700"


def test_selected_call_output_preview_is_operator_only_and_ordered(seeded):
    from datetime import datetime, timedelta, timezone
    from fastapi.testclient import TestClient
    from contracts.content import ContentObject
    from contracts.events import EventEnvelope, EventType
    from service.http.app import create_app
    from service.ingest.store import ingest_batch

    detail = read_work_item_detail(seeded, "demo-work-061")
    call_id = next(call["call_id"] for call in detail["calls"] if call["local_dispatch"])
    instant = datetime.now(timezone.utc)
    chunks = ["Alpha ", "β ", "Gamma"]
    content = [ContentObject.from_bytes(f"preview-body-{index}", text.encode("utf-8"),
        role="assistant", direction="in", content_type="text/plain", origin="synthetic",
        capture_layer="test", message_id="preview-message")
        for index, text in enumerate(chunks, start=1)]
    events = [EventEnvelope(event_id=f"preview-event-{index}",
        event_type=EventType.RESPONSE_DELTA, schema_version=1,
        producer_id="preview-test", producer_incarnation="first", producer_sequence=index,
        occurred_at=instant + timedelta(microseconds=4 - index), origin="synthetic",
        observation_layer="bridge", capture_completeness="complete",
        task_id="demo-task-001", work_item_id="demo-work-061", call_id=call_id,
        payload={"message_id": "preview-message", "chunk_sequence": index,
                 "content_id": f"preview-body-{index}"})
        for index in range(1, 4)]
    assert ingest_batch(seeded, "preview-test", events, content) == ["committed"] * 3

    from service.queries.history import read_output_preview
    with pytest.raises(PermissionError, match="operator"):
        read_output_preview(seeded, "demo-work-061", call_id, role="producer")

    client = TestClient(create_app(seeded, producer_tokens={"preview-test": "producer-secret"},
        operator_token="operator-secret", payload_limit_bytes=1048576))
    path = f"/api/v1/work-items/demo-work-061/calls/{call_id}/output-preview"
    assert client.get(path, headers={"Authorization": "Bearer producer-secret"}).status_code == 401
    wrong_work = f"/api/v1/work-items/demo-work-060/calls/{call_id}/output-preview"
    assert client.get(wrong_work, headers={"Authorization": "Bearer operator-secret"}).status_code == 404
    response = client.get(path, headers={"Authorization": "Bearer operator-secret"})
    assert response.status_code == 200
    assert response.json()["text"] == "Alpha β Gamma"
    assert response.json()["chunks_shown"] == 3
    assert response.json()["truncated"] is False
    bounded = TestClient(create_app(seeded, producer_tokens={"preview-test": "producer-secret"},
        operator_token="operator-secret", payload_limit_bytes=1048576,
        preview_max_chunks=2, preview_max_bytes=8))
    limited = bounded.get(path, headers={"Authorization": "Bearer operator-secret"})
    assert limited.json()["text"] == "β Gamma"
    assert limited.json()["chunks_shown"] == 2
    assert limited.json()["truncated"] is True
