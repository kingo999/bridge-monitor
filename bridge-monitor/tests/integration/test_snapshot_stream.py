from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from contracts.queries import CohortQuery
from database.migrate import migrate
from scripts.replay_fixtures import replay_fixture
from service.http.app import create_app
from service.queries.snapshot import read_publications, read_snapshot


@pytest.fixture
def seeded(test_dsn):
    migrate(test_dsn)
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE publication_stream, events, content_objects, producer_sequences, tasks RESTART IDENTITY CASCADE")
        conn.execute("UPDATE publication_counter SET next_position=1 WHERE singleton=true")
        conn.execute("UPDATE dashboard_status SET active=0,queued=0,held_uncertain=0,blocked=0,capture_state='unknown',known_gaps=0,spool_pressure=NULL WHERE singleton=true")
    replay_fixture(test_dsn, Path(__file__).parents[1] / "fixtures" / "demonstration.json")
    return test_dsn


def test_snapshot_cursor_and_status_are_from_same_committed_view(seeded):
    snapshot = read_snapshot(seeded, CohortQuery(origin="synthetic"))
    assert snapshot.cursor.position > 0
    assert snapshot.status.held_uncertain >= 1
    assert snapshot.status.known_gaps >= 1
    assert read_publications(seeded, snapshot.cursor.position, limit=10) == []


def test_browser_queries_require_operator_and_bodies_are_on_demand(seeded):
    client = TestClient(create_app(seeded, producer_tokens={"seed": "producer-secret"},
                                   operator_token="operator-secret", payload_limit_bytes=1024))
    assert client.get("/api/v1/snapshot").status_code == 401
    assert client.get("/api/v1/work-items").status_code == 401
    assert client.get("/api/v1/metrics").status_code == 401
    assert client.get("/api/v1/stream").status_code == 401
    headers = {"Authorization": "Bearer operator-secret"}
    snapshot = client.get("/api/v1/snapshot?origin=synthetic", headers=headers)
    assert snapshot.status_code == 200
    assert "cursor" in snapshot.json()
    assert "original_prompt" not in snapshot.text
    assert client.get("/api/v1/work-items?origin=synthetic&limit=2", headers=headers).status_code == 200
    assert client.get("/api/v1/metrics?origin=synthetic", headers=headers).status_code == 200


def test_operator_cookie_is_http_only_and_expiring(seeded):
    client = TestClient(create_app(seeded, producer_tokens={"seed": "producer-secret"},
                                   operator_token="operator-secret", payload_limit_bytes=1024,
                                   session_ttl_seconds=1))
    response = client.post("/api/v1/session", json={"operator_token": "operator-secret"})
    assert response.status_code == 200
    assert "httponly" in response.headers["set-cookie"].lower()
    assert client.get("/api/v1/snapshot").status_code == 200
    assert client.post("/api/v1/session/logout").status_code == 204
    assert client.get("/api/v1/snapshot").status_code == 401
