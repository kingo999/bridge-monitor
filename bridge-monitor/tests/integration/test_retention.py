"""Configured retention removes bytes while preserving auditable identities."""

from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from contracts.content import ContentObject
from contracts.events import EventEnvelope, EventType
from database.migrate import migrate
from service.ingest.store import ingest_batch, read_content
from service.maintenance.retention import apply_retention
from service.queries.snapshot import CursorExpired, read_publications
from bridge.runtime.reconciliation import reconcile_execution
from tests.integration.test_dispositions import _seed_chain, _local_result


def test_content_tombstones_and_replay_boundary_are_durable(test_dsn):
    migrate(test_dsn)
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE publication_stream, events, content_objects, "
                     "producer_sequences RESTART IDENTITY CASCADE")
        conn.execute("UPDATE publication_counter SET next_position=1 WHERE singleton=true")
    now = datetime.now(timezone.utc)
    old = ContentObject.from_bytes("retention-old", "old π".encode(),
        role="user", direction="out", content_type="text/plain", origin="synthetic",
        capture_layer="test")
    fresh = ContentObject.from_bytes("retention-fresh", b"fresh",
        role="assistant", direction="in", content_type="text/plain", origin="synthetic",
        capture_layer="test")

    def event(number, content_id):
        return EventEnvelope(event_id=f"retention-event-{number}",
            event_type=EventType.MESSAGE_SENT, schema_version=1,
            producer_id="retention-producer", producer_incarnation="boot-1",
            producer_sequence=number, occurred_at=now,
            origin="synthetic", observation_layer="bridge",
            capture_completeness="complete", payload={
                "message_id": f"retention-message-{number}", "role": "user",
                "direction": "out", "original_content_id": content_id,
                "effective_content_id": content_id})

    events = [event(1, old.content_id), event(2, fresh.content_id)]
    assert ingest_batch(test_dsn, "retention-producer", events, [old, fresh]) == [
        "committed", "committed"]
    with psycopg.connect(test_dsn) as conn:
        conn.execute("UPDATE content_objects SET created_at=%s WHERE content_id=%s",
                     (now - timedelta(days=31), old.content_id))
        conn.execute("UPDATE publication_stream SET published_at=%s WHERE position=1",
                     (now - timedelta(days=31),))
    receipt = apply_retention(test_dsn, retention_days=30, now=now)
    assert receipt["content_tombstones"] == 1
    assert receipt["publications_removed"] == 1
    assert receipt["earliest_position"] == 2
    with psycopg.connect(test_dsn) as conn:
        tombstone = conn.execute("SELECT raw_bytes,completeness,byte_length,sha256 "
            "FROM content_objects WHERE content_id=%s", (old.content_id,)).fetchone()
        assert tombstone == (None, "removed", old.byte_length, old.sha256)
        assert conn.execute("SELECT count(*) FROM retention_runs WHERE run_id=%s",
                            (receipt["run_id"],)).fetchone()[0] == 1
    assert read_content(test_dsn, old.content_id, role="operator") is None
    assert read_content(test_dsn, fresh.content_id, role="operator") == b"fresh"
    with pytest.raises(CursorExpired) as expired:
        read_publications(test_dsn, 0)
    assert expired.value.earliest_position == 2
    assert [row["position"] for row in read_publications(test_dsn, 1)] == [2]
    assert ingest_batch(test_dsn, "retention-producer", [events[0]], [old]) == ["duplicate"]
    assert read_content(test_dsn, old.content_id, role="operator") is None
    assert ingest_batch(test_dsn, "retention-producer", [event(3, old.content_id)],
                        [old]) == ["committed"]
    assert read_content(test_dsn, old.content_id, role="operator") is None


def test_live_prompt_and_registered_guidance_survive_expiry(runtime_dsn, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_OPERATOR_TOKEN", "retention-test-token")
    _, child, _ = _seed_chain(dsn)
    prompt_id = f"prompt-{child}"
    old = datetime.now(timezone.utc) - timedelta(days=31)
    guidance = ContentObject.from_bytes(f"guidance-{child}", b"approved rule",
        role="guidance", direction="in", content_type="text/plain",
        origin="imported", capture_layer="registry")
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE content_objects SET created_at=%s WHERE content_id=%s",
                     (old, prompt_id))
        conn.execute("INSERT INTO content_objects(content_id,raw_bytes,byte_length,sha256,"
            "role,direction,content_type,encoding,origin,capture_layer,completeness,"
            "block_order,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s)",
            (guidance.content_id, guidance.raw_bytes, guidance.byte_length,
             guidance.sha256, guidance.role, guidance.direction, guidance.content_type,
             guidance.encoding, guidance.origin, guidance.capture_layer,
             guidance.completeness, old))
        lease, incarnation = conn.execute("SELECT lease_id,owner_incarnation "
            "FROM runtime_jobs WHERE call_id=%s", (child,)).fetchone()
    apply_retention(dsn, retention_days=30)
    assert read_content(dsn, prompt_id, role="operator") == b"scoped child prompt"
    assert read_content(dsn, guidance.content_id, role="operator") == b"approved rule"
    assert reconcile_execution(dsn, child, {"kind": "process_exit_with_call_correlation",
        "call_id": child, "lease_id": lease, "owner_incarnation": incarnation,
        "terminated": True}, actor_kind="operator", actor_id="reviewer",
        auth_token="retention-test-token") == "terminated"
    apply_retention(dsn, retention_days=30)
    assert read_content(dsn, prompt_id, role="operator") is None
    assert read_content(dsn, guidance.content_id, role="operator") == b"approved rule"


def test_unresolved_disposition_and_approved_example_keep_review_bytes(runtime_dsn):
    dsn = runtime_dsn
    _, child, bindings = _seed_chain(dsn)
    local = _local_result(dsn, child, bindings[child])
    result_content_id = local.answer.content_id
    example = ContentObject.from_bytes(f"example-{child}", b"approved example",
        role="guidance", direction="in", content_type="text/plain",
        origin="imported", capture_layer="example")
    old = datetime.now(timezone.utc) - timedelta(days=31)
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE content_objects SET created_at=%s WHERE content_id=%s",
                     (old, result_content_id))
        conn.execute("INSERT INTO content_objects(content_id,raw_bytes,byte_length,sha256,"
            "role,direction,content_type,encoding,origin,capture_layer,completeness,"
            "block_order,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s)",
            (example.content_id, example.raw_bytes, example.byte_length,
             example.sha256, example.role, example.direction, example.content_type,
             example.encoding, example.origin, example.capture_layer,
             example.completeness, old))
        conn.execute("INSERT INTO guidance_pack_versions(pack_version_id,pack_id,"
            "version_label,sha256,items,registered_by) VALUES(%s,%s,'v1',%s,'[]','test')",
            (f"pack-{child}", f"pack-{child}", example.sha256))
        conn.execute("INSERT INTO example_reviews(review_id,pack_version_id,example_id,"
            "content_id,reviewer_id,status,permitted_use,exposure) "
            "VALUES(%s,%s,'example',%s,'reviewer','approved','prompt_example','unseen')",
            (f"review-{child}", f"pack-{child}", example.content_id))
    apply_retention(dsn, retention_days=30)
    assert read_content(dsn, result_content_id, role="operator") == b"local draft"
    assert read_content(dsn, example.content_id, role="operator") == b"approved example"
