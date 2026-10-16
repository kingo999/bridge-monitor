import pytest
from fastapi.testclient import TestClient

from database.migrate import migrate
from service.http.app import create_app


@pytest.fixture
def client(test_dsn):
    migrate(test_dsn)
    return TestClient(create_app(test_dsn, producer_tokens={"worker-a": "producer-secret"},
                                 operator_token="operator-secret", payload_limit_bytes=8))


def test_ingest_requires_bound_producer_token(client):
    response = client.post("/api/v1/events/batch", json={"events": [], "content": []})
    assert response.status_code == 401
    response = client.post("/api/v1/events/batch", headers={"Authorization": "Bearer operator-secret"},
                           json={"events": [], "content": []})
    assert response.status_code == 401


def test_content_requires_operator_token(client):
    response = client.get("/api/v1/content/unknown", headers={"Authorization": "Bearer producer-secret"})
    assert response.status_code == 401


def test_producer_cannot_assert_another_producer_identity(client):
    from datetime import datetime, timezone
    event = {"event_id": "forged-1", "event_type": "call.queued", "schema_version": 1,
             "producer_id": "worker-other", "producer_incarnation": "boot-1",
             "producer_sequence": 1, "occurred_at": datetime.now(timezone.utc).isoformat(),
             "origin": "synthetic", "observation_layer": "bridge",
             "capture_completeness": "complete", "payload": {}}
    response = client.post("/api/v1/events/batch", headers={"Authorization": "Bearer producer-secret"},
                           json={"events": [event], "content": []})
    assert response.status_code == 403


def test_oversized_content_is_rejected_without_silent_truncation(client):
    import base64
    body = {"events": [], "content": [{"content_id": "large", "raw_base64": base64.b64encode(b"123456789").decode(),
        "role": "user", "direction": "out", "content_type": "text/plain",
        "origin": "synthetic", "capture_layer": "bridge"}]}
    response = client.post("/api/v1/events/batch", headers={"Authorization": "Bearer producer-secret"},
                           json=body)
    assert response.status_code == 413
