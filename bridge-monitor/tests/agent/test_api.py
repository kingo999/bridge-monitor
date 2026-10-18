import json
import time
import asyncio
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from skynet_agent.app import create_app
from skynet_agent.config import AgentSettings


DIGEST = "a" * 64
REQUEST = {"job_id": "child-1", "model": "qwen", "model_digest": DIGEST,
           "prompt": "Exact sensitive prompt", "options": {"num_ctx": 1000,
           "num_predict": 100, "temperature": 0.0}, "keep_alive": "10m"}
AUTH = {"Authorization": "Bearer local-test-token"}


def test_status_api_returns_ack_then_exact_ordered_output_without_storing_prompt(tmp_path):
    posts = []

    def ollama(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen", "digest": DIGEST}]})
        posts.append(request)
        body = "\n".join(json.dumps(item) for item in [
            {"model": "qwen", "response": "hel", "done": False},
            {"model": "qwen", "response": "lo", "done": False},
            {"model": "qwen", "response": "", "done": True,
             "prompt_eval_count": 4, "eval_count": 2}]) + "\n"
        return httpx.Response(200, text=body)

    settings = AgentSettings(journal_path=tmp_path / "jobs.sqlite", token="local-test-token",
        ollama_endpoint="http://ollama", model="qwen", model_digest=DIGEST,
        max_buffer_bytes=1024)
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(ollama))
    with TestClient(create_app(settings, ollama_client=upstream)) as client:
        assert client.get("/v1/health").status_code == 401
        assert client.get("/v1/health", headers={"Authorization": b"Bearer caf\xe9"}).status_code == 401
        assert client.post("/v1/jobs", content="{", headers={
            "Content-Type": "application/json"}).status_code == 401
        health = client.get("/v1/health", headers=AUTH).json()
        assert health["model_ready"] is True
        assert health["observed_digest"] == DIGEST
        ack = client.post("/v1/jobs", json=REQUEST, headers=AUTH)
        assert ack.status_code == 202
        assert ack.json()["job_id"] == "child-1"
        for _ in range(100):
            status = client.get("/v1/jobs/child-1?after_sequence=0", headers=AUTH)
            if status.json()["state"] == "completed":
                break
            time.sleep(0.01)
        assert status.status_code == 200
        assert status.json()["deltas"] == [
            {"sequence": 1, "text": "hel"}, {"sequence": 2, "text": "lo"}]
        assert status.json()["terminal"]["eval_count"] == 2
        assert len(posts) == 1
        assert client.post("/v1/jobs", json=REQUEST, headers=AUTH).status_code == 202
        assert len(posts) == 1
    assert b"Exact sensitive prompt" not in (tmp_path / "jobs.sqlite").read_bytes()
    assert b"hel" not in (tmp_path / "jobs.sqlite").read_bytes()


def test_conflicting_job_request_is_rejected_before_second_dispatch(tmp_path):
    calls = []

    def ollama(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen", "digest": DIGEST}]})
        calls.append(request)
        return httpx.Response(200, text=json.dumps(
            {"model": "qwen", "response": "ok", "done": True}) + "\n")

    settings = AgentSettings(journal_path=tmp_path / "jobs.sqlite", token="local-test-token",
        ollama_endpoint="http://ollama", model="qwen", model_digest=DIGEST,
        max_buffer_bytes=1024)
    with TestClient(create_app(settings,
            ollama_client=httpx.AsyncClient(transport=httpx.MockTransport(ollama)))) as client:
        assert client.post("/v1/jobs", json=REQUEST, headers=AUTH).status_code == 202
        changed = {**REQUEST, "prompt": "Different prompt"}
        assert client.post("/v1/jobs", json=changed, headers=AUTH).status_code == 409
        for _ in range(100):
            if client.get("/v1/jobs/child-1", headers=AUTH).json()["state"] == "completed":
                break
            time.sleep(0.01)
        assert len(calls) == 1


def test_restarted_agent_reports_gap_when_output_buffer_was_lost(tmp_path):
    def ollama(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen", "digest": DIGEST}]})
        return httpx.Response(200, text=json.dumps(
            {"model": "qwen", "response": "answer", "done": True}) + "\n")

    settings = AgentSettings(journal_path=tmp_path / "jobs.sqlite", token="local-test-token",
        ollama_endpoint="http://ollama", model="qwen", model_digest=DIGEST,
        max_buffer_bytes=1024)
    with TestClient(create_app(settings,
            ollama_client=httpx.AsyncClient(transport=httpx.MockTransport(ollama)))) as client:
        assert client.post("/v1/jobs", json=REQUEST, headers=AUTH).status_code == 202
        for _ in range(100):
            status = client.get("/v1/jobs/child-1", headers=AUTH).json()
            if status["state"] == "completed":
                break
            time.sleep(0.01)
        assert status["sequence"] == 1

    with TestClient(create_app(settings,
            ollama_client=httpx.AsyncClient(transport=httpx.MockTransport(ollama)))) as client:
        status = client.get("/v1/jobs/child-1?after_sequence=0", headers=AUTH).json()
        assert status["state"] == "completed"
        assert status["sequence"] == 1
        assert status["gap"] is True
        assert status["deltas"] == []


def test_terminal_frame_response_is_not_persisted(tmp_path):
    def ollama(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen", "digest": DIGEST}]})
        return httpx.Response(200, text=json.dumps(
            {"model": "qwen", "response": "private answer", "done": True,
             "prompt_eval_count": 4, "eval_count": 2}) + "\n")

    settings = AgentSettings(journal_path=tmp_path / "jobs.sqlite", token="local-test-token",
        ollama_endpoint="http://ollama", model="qwen", model_digest=DIGEST,
        max_buffer_bytes=1024)
    with TestClient(create_app(settings,
            ollama_client=httpx.AsyncClient(transport=httpx.MockTransport(ollama)))) as client:
        assert client.post("/v1/jobs", json=REQUEST, headers=AUTH).status_code == 202
        for _ in range(100):
            status = client.get("/v1/jobs/child-1", headers=AUTH).json()
            if status["state"] == "completed":
                break
            time.sleep(0.01)
        assert status["deltas"] == [{"sequence": 1, "text": "private answer"}]
        assert "response" not in status["terminal"]
    assert b"private answer" not in (tmp_path / "jobs.sqlite").read_bytes()


def test_lan_listener_restricts_clients_before_auth(tmp_path):
    settings = AgentSettings(journal_path=tmp_path / "jobs.sqlite", token="local-test-token",
        ollama_endpoint="http://ollama", model="qwen", model_digest=DIGEST,
        max_buffer_bytes=1024, listen_host="192.168.0.78",
        allowed_client_ips=("192.168.0.24",))
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
        httpx.Response(200, json={"models": [{"name": "qwen", "digest": DIGEST}]})))
    with TestClient(create_app(settings, ollama_client=upstream),
                    client=("192.168.0.25", 50000)) as blocked:
        assert blocked.get("/v1/health", headers=AUTH).status_code == 403
    with TestClient(create_app(replace(settings, journal_path=tmp_path / "allowed.sqlite"),
                               ollama_client=httpx.AsyncClient(transport=httpx.MockTransport(
                                   lambda request: httpx.Response(200, json={"models": [
                                       {"name": "qwen", "digest": DIGEST}]})))),
                    client=("192.168.0.24", 50000)) as allowed:
        assert allowed.get("/v1/health", headers=AUTH).status_code == 200


def test_expired_output_buffer_reports_gap_but_keeps_terminal_receipt(tmp_path):
    def ollama(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen", "digest": DIGEST}]})
        return httpx.Response(200, text=json.dumps(
            {"model": "qwen", "response": "brief answer", "done": True}) + "\n")

    settings = AgentSettings(journal_path=tmp_path / "jobs.sqlite", token="local-test-token",
        ollama_endpoint="http://ollama", model="qwen", model_digest=DIGEST,
        max_buffer_bytes=1024, output_retention_seconds=0.02,
        cleanup_seconds=0.01)
    with TestClient(create_app(settings,
            ollama_client=httpx.AsyncClient(transport=httpx.MockTransport(ollama)))) as client:
        assert client.post("/v1/jobs", json=REQUEST, headers=AUTH).status_code == 202
        for _ in range(100):
            status = client.get("/v1/jobs/child-1", headers=AUTH).json()
            if status["state"] == "completed":
                break
            time.sleep(0.01)
        time.sleep(0.08)
        after = client.get("/v1/jobs/child-1?after_sequence=0", headers=AUTH).json()
        assert after["state"] == "completed"
        assert after["sequence"] == 1
        assert after["gap"] is True
        assert after["deltas"] == []


def test_cancel_before_background_task_starts_becomes_unknown(tmp_path):
    calls = []

    def ollama(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"models": [{"name": "qwen", "digest": DIGEST}]})

    def cancel_before_start(coro):
        task = asyncio.create_task(coro)
        task.cancel()
        return task

    settings = AgentSettings(journal_path=tmp_path / "jobs.sqlite", token="local-test-token",
        ollama_endpoint="http://ollama", model="qwen", model_digest=DIGEST,
        max_buffer_bytes=1024)
    with TestClient(create_app(settings,
            ollama_client=httpx.AsyncClient(transport=httpx.MockTransport(ollama)),
            job_task_factory=cancel_before_start)) as client:
        assert client.post("/v1/jobs", json=REQUEST, headers=AUTH).status_code == 202
        for _ in range(100):
            state = client.get("/v1/jobs/child-1", headers=AUTH).json()["state"]
            if state == "unknown":
                break
            time.sleep(0.01)
        assert state == "unknown"
        assert "/api/generate" not in calls


def test_agent_rejects_oversized_prompt_and_unbounded_options_before_dispatch(tmp_path):
    calls = []

    def ollama(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"models": [{"name": "qwen", "digest": DIGEST}]})

    settings = AgentSettings(journal_path=tmp_path / "jobs.sqlite", token="local-test-token",
        ollama_endpoint="http://ollama", model="qwen", model_digest=DIGEST,
        max_buffer_bytes=1024, max_prompt_bytes=8,
        max_context_tokens=1000, max_output_tokens=100)
    with TestClient(create_app(settings,
            ollama_client=httpx.AsyncClient(transport=httpx.MockTransport(ollama)))) as client:
        assert client.post("/v1/jobs", json=REQUEST, headers=AUTH).status_code == 413
        bad = {**REQUEST, "prompt": "short", "options": {
            "num_ctx": 1000, "num_predict": 10000, "temperature": 0.0}}
        assert client.post("/v1/jobs", json=bad, headers=AUTH).status_code == 422
        bad["options"] = {"num_ctx": 1000, "num_predict": 100,
                          "temperature": 0.0, "hidden": "unsupported"}
        assert client.post("/v1/jobs", json=bad, headers=AUTH).status_code == 422
        bad = {**REQUEST, "prompt": "short", "keep_alive": "-1"}
        assert client.post("/v1/jobs", json=bad, headers=AUTH).status_code == 422
        assert calls == []


def test_cancel_during_streaming_becomes_unknown_not_completed(tmp_path):
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"model":"qwen","response":"partial","done":false}\n'
            await asyncio.sleep(10)

    def ollama(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen", "digest": DIGEST}]})
        return httpx.Response(200, stream=SlowStream())

    settings = AgentSettings(journal_path=tmp_path / "jobs.sqlite", token="local-test-token",
        ollama_endpoint="http://ollama", model="qwen", model_digest=DIGEST,
        max_buffer_bytes=1024)
    with TestClient(create_app(settings,
            ollama_client=httpx.AsyncClient(transport=httpx.MockTransport(ollama)))) as client:
        assert client.post("/v1/jobs", json=REQUEST, headers=AUTH).status_code == 202
        for _ in range(100):
            status = client.get("/v1/jobs/child-1", headers=AUTH).json()
            if status["state"] == "streaming":
                break
            time.sleep(0.01)
        assert status["deltas"] == [{"sequence": 1, "text": "partial"}]
        assert client.post("/v1/jobs/child-1/cancel", headers=AUTH).status_code == 200
        for _ in range(100):
            status = client.get("/v1/jobs/child-1", headers=AUTH).json()
            if status["state"] == "unknown":
                break
            time.sleep(0.01)
        assert status["state"] == "unknown"
        assert status["terminal"]["reason"] == "cancelled_without_server_termination_evidence"


@pytest.mark.parametrize("mismatch,reason", [
    (True, "model_digest_mismatch"), (False, "ollama_http_error")])
def test_agent_preflight_and_upstream_http_failure_are_terminal(tmp_path, mismatch, reason):
    def ollama(request):
        if request.url.path == "/api/tags":
            digest = "b" * 64 if mismatch else DIGEST
            return httpx.Response(200, json={"models": [{"name": "qwen", "digest": digest}]})
        return httpx.Response(503)

    settings = AgentSettings(journal_path=tmp_path / "jobs.sqlite", token="local-test-token",
        ollama_endpoint="http://ollama", model="qwen", model_digest=DIGEST,
        max_buffer_bytes=1024)
    with TestClient(create_app(settings,
            ollama_client=httpx.AsyncClient(transport=httpx.MockTransport(ollama)))) as client:
        assert client.post("/v1/jobs", json=REQUEST, headers=AUTH).status_code == 202
        for _ in range(100):
            status = client.get("/v1/jobs/child-1", headers=AUTH).json()
            if status["state"] == "failed":
                break
            time.sleep(0.01)
        assert status["state"] == "failed"
        assert status["terminal"]["reason"] == reason
        assert status["deltas"] == []
