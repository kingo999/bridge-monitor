import pytest
from fastapi.testclient import TestClient

from service.http.app import create_app


ORIGIN = "http://127.0.0.1:8873"


def dashboard(*, enabled=False, peer="127.0.0.1"):
    options = {"local_test_login_origin": ORIGIN} if enabled else {}
    app = create_app("unused", producer_tokens={"test": "producer-secret"},
                     operator_token="operator-secret", payload_limit_bytes=1024, **options)
    return TestClient(app, base_url=ORIGIN, client=(peer, 12345))


def test_local_password_starts_a_browser_session_only_when_enabled():
    headers = {"Origin": ORIGIN}
    with_password = {"operator_token": "password"}
    assert dashboard().post("/api/v1/session", json=with_password, headers=headers).status_code == 401
    client = dashboard(enabled=True)
    assert client.post("/api/v1/session", json=with_password, headers=headers).status_code == 200
    assert client.get("/api/v1/session").json()["authenticated"] is True
    assert client.get("/api/v1/services", headers={"Authorization": "Bearer password"}).status_code == 401
    assert client.post("/api/v1/session/logout").status_code == 204
    assert client.get("/api/v1/session").json()["authenticated"] is False
    assert client.post("/api/v1/session", json={"operator_token": "operator-secret"}).status_code == 200


@pytest.mark.parametrize("peer,headers", [
    ("192.0.2.1", {"Origin": ORIGIN}),
    ("127.0.0.1", {}),
    ("127.0.0.1", {"Origin": "https://other.example"}),
    ("127.0.0.1", {"Origin": ORIGIN, "Host": "other.example"}),
    ("127.0.0.1", {"Origin": ORIGIN, "X-Forwarded-For": "192.0.2.1"}),
    ("127.0.0.1", {"Origin": ORIGIN, "Forwarded": "for=192.0.2.1"}),
])
def test_local_password_rejects_remote_cross_origin_or_proxied_login(peer, headers):
    client = dashboard(enabled=True, peer=peer)
    response = client.post("/api/v1/session", json={"operator_token": "password"}, headers=headers)
    assert response.status_code == 401
    assert "set-cookie" not in response.headers
