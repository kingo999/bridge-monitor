import json
import time
from datetime import datetime, timedelta, timezone

from contracts.events import EventType
from contracts.work import DispatchLease
from bridge.adapters.qwen_runtime import QwenAdapter


class EventSink:
    def __init__(self):
        self.events = []

    def emit(self, kind, **kwargs):
        self.events.append((kind, kwargs))
        return True


class FakeResponse:
    def __init__(self, chunks, *, status_code=200, fail_after=None):
        self.chunks = chunks
        self.status_code = status_code
        self.fail_after = fail_after

    def __enter__(self): return self
    def __exit__(self, *args): return False
    def iter_lines(self):
        for index, chunk in enumerate(self.chunks):
            if self.fail_after == index:
                raise ConnectionError("connection dropped after acceptance")
            yield json.dumps(chunk)
        if self.fail_after == len(self.chunks):
            raise ConnectionError("connection dropped after acceptance")


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def stream(self, method, url, *, json, timeout):
        self.calls.append((method, url, json, timeout))
        return self.response

    def get(self, url, *, timeout):
        self.calls.append(("GET", url, None, timeout))
        class Tags:
            status_code = 200
            def json(self):
                return {"models": [{"name": "qwen2.5-coder:14b", "digest": "a" * 64}]}
        return Tags()


def _lease():
    return DispatchLease(lease_id="lease-1", call_id="call-1", owner_incarnation="boot-1",
                         fencing_token=1, policy_version="v1")


def test_qwen_stream_captures_chunks_and_model_choice():
    sink = EventSink()
    client = FakeClient(FakeResponse([
        {"model": "qwen2.5-coder:14b", "response": "one ", "done": False},
        {"model": "qwen2.5-coder:14b", "response": "two", "done": True,
         "prompt_eval_count": 10, "eval_count": 2}]))
    result = QwenAdapter(client, endpoint="http://skynet.local:11434",
                         model="qwen2.5-coder:14b", context_tokens=16384,
                         output_tokens=2048, timeout_seconds=30).run(_lease(), "Exact prompt", sink)
    assert result.state == "completed" and result.answer == "one two"
    assert result.reported_model == "qwen2.5-coder:14b"
    assert result.usage == {"input_tokens": 10, "output_tokens": 2}
    assert client.calls[0][2]["stream"] is True
    assert [kind for kind, _ in sink.events].count(EventType.RESPONSE_DELTA) == 2
    assert any(kind == EventType.MESSAGE_SENT and kw["body"] == b"Exact prompt" for kind, kw in sink.events)


def test_qwen_missing_model_is_unknown_and_connection_loss_is_uncertain():
    sink = EventSink()
    client = FakeClient(FakeResponse([{"response": "partial", "done": False}], fail_after=1))
    result = QwenAdapter(client, endpoint="http://skynet.local:11434", model="qwen",
                         context_tokens=1000, output_tokens=100, timeout_seconds=30).run(
                             _lease(), "prompt", sink)
    assert result.state == "uncertain" and result.reported_model is None
    assert result.answer is None
    assert any(kind == EventType.ATTEMPT_UNCERTAIN for kind, _ in sink.events)


def test_qwen_capability_denial_does_not_send_request():
    sink = EventSink()
    client = FakeClient(FakeResponse([]))
    adapter = QwenAdapter(client, endpoint="http://skynet.local:11434", model="qwen",
                           context_tokens=1000, output_tokens=100, timeout_seconds=30,
                           allowed=False)
    result = adapter.run(_lease(), "prompt", sink)
    assert result.state == "denied" and not client.calls


def test_pinned_model_preflight_accepts_matching_digest_and_rejects_drift():
    client = FakeClient(FakeResponse([]))
    adapter = QwenAdapter(client, endpoint="http://skynet.local:11434",
        model="qwen2.5-coder:14b", expected_digest="a" * 64,
        context_tokens=16384, output_tokens=2048, timeout_seconds=30)
    assert adapter.preflight().ready
    assert client.calls == [("GET", "http://skynet.local:11434/api/tags", None, 5)]
    adapter.expected_digest = "b" * 64
    result = adapter.preflight()
    assert not result.ready and result.reason == "model_digest_mismatch"
    assert all(call[0] != "POST" for call in client.calls)


def test_qwen_request_timeout_respects_shared_local_cutoff():
    client = FakeClient(FakeResponse([
        {"model": "qwen", "response": "done", "done": True}]))
    adapter = QwenAdapter(client, endpoint="http://skynet.local:11434", model="qwen",
        context_tokens=1000, output_tokens=100, timeout_seconds=180)
    cutoff = datetime.now(timezone.utc) + timedelta(seconds=2)
    result = adapter.run(_lease(), "prompt", EventSink(), deadline_at=cutoff)
    assert result.state == "completed"
    assert 0 < client.calls[0][3] <= 2


def test_qwen_stream_cannot_complete_after_shared_cutoff():
    class SlowResponse(FakeResponse):
        def iter_lines(self):
            time.sleep(0.03)
            yield json.dumps({"model": "qwen", "response": "late", "done": True})

    sink = EventSink()
    client = FakeClient(SlowResponse([]))
    adapter = QwenAdapter(client, endpoint="http://skynet.local:11434", model="qwen",
        context_tokens=1000, output_tokens=100, timeout_seconds=30)
    cutoff = datetime.now(timezone.utc) + timedelta(milliseconds=10)
    result = adapter.run(_lease(), "prompt", sink, deadline_at=cutoff)
    assert result.state == "uncertain"
    assert EventType.RESPONSE_COMPLETED not in [kind for kind, _ in sink.events]


def test_qwen_stream_cannot_complete_after_execution_stage_deadline():
    class DribblingResponse(FakeResponse):
        def iter_lines(self):
            yield json.dumps({"model": "qwen", "response": "first", "done": False})
            time.sleep(1.05)
            yield json.dumps({"model": "qwen", "response": "late", "done": True})

    sink = EventSink()
    client = FakeClient(DribblingResponse([]))
    adapter = QwenAdapter(client, endpoint="http://skynet.local:11434", model="qwen",
        context_tokens=1000, output_tokens=100, timeout_seconds=1)
    result = adapter.run(_lease(), "prompt", sink,
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=10))
    assert result.state == "uncertain"
    assert EventType.RESPONSE_COMPLETED not in [kind for kind, _ in sink.events]
