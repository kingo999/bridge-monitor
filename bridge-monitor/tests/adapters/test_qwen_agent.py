import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx

from bridge.adapters.qwen_agent import QwenAgentAdapter
from contracts.events import EventType


DIGEST = "a" * 64


def ack_for(request):
    wire = json.loads(request.content)
    canonical = json.dumps({key: value for key, value in wire.items() if key != "job_id"},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {"job_id": wire["job_id"], "state": "accepted",
            "request_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
            "agent_incarnation": "inc-1"}


def status_for(ack, **values):
    return {"job_id": ack["job_id"], "request_sha256": ack["request_sha256"],
            "agent_incarnation": ack["agent_incarnation"], **values}


class Sink:
    def __init__(self):
        self.events = []

    def emit(self, kind, **kwargs):
        self.events.append((kind, kwargs))


def lease():
    return SimpleNamespace(call_id="child-1", lease_id="lease-1", fencing_token=1,
                           policy_version="policy-1")


def adapter(transport, **overrides):
    values = dict(endpoint="http://agent", token="test-token", model="qwen",
                  expected_digest=DIGEST, context_tokens=1000, output_tokens=100,
                  timeout_seconds=1, first_output_seconds=0.2,
                  idle_output_seconds=0.2, ack_seconds=0.1,
                  poll_seconds=0.001, poll_timeout_seconds=0.1,
                  poll_failures=2, keep_alive="10m", temperature=0.0)
    values.update(overrides)
    return QwenAgentAdapter(httpx.Client(transport=httpx.MockTransport(transport)),
                            **values)


def test_polling_delivers_only_contiguous_model_output_and_terminal_usage():
    status_calls = 0
    ack = None

    def agent(request):
        nonlocal status_calls, ack
        if request.url.path == "/v1/health":
            return httpx.Response(200, json={"model_ready": True, "model": "qwen",
                                             "model_digest": DIGEST,
                                             "observed_digest": DIGEST})
        if request.url.path == "/v1/jobs" and request.method == "POST":
            assert request.headers["authorization"] == "Bearer test-token"
            assert request.read().decode().find("Exact prompt") >= 0
            ack = ack_for(request)
            return httpx.Response(202, json=ack)
        if request.url.path == "/v1/jobs/child-1":
            status_calls += 1
            if status_calls == 1:
                return httpx.Response(200, json=status_for(ack, state="streaming",
                    sequence=1, gap=False, deltas=[{"sequence": 1, "text": "hel"}],
                    terminal=None))
            return httpx.Response(200, json=status_for(ack, state="completed",
                sequence=2, gap=False, deltas=[{"sequence": 2, "text": "lo"}],
                terminal={"model": "qwen", "done": True,
                          "prompt_eval_count": 4, "eval_count": 2}))
        raise AssertionError(request.url)

    sink = Sink()
    qwen = adapter(agent)
    assert qwen.preflight().ready is True
    result = qwen.run(lease(), "Exact prompt", sink)
    assert result.state == "completed" and result.answer == "hello"
    assert result.reported_model == "qwen"
    assert result.usage == {"input_tokens": 4, "output_tokens": 2}
    assert [kind for kind, _ in sink.events].count(EventType.RESPONSE_DELTA) == 2
    started = next(kwargs for kind, kwargs in sink.events if kind == EventType.RESPONSE_STARTED)
    assert started["payload"]["first_output_latency_ms"] >= 0


def test_healthy_poll_replies_without_output_cannot_extend_first_output_deadline():
    status_calls = 0
    cancellations = 0
    ack = None

    def agent(request):
        nonlocal status_calls, cancellations, ack
        if request.url.path == "/v1/jobs" and request.method == "POST":
            ack = ack_for(request)
            return httpx.Response(202, json=ack)
        if request.url.path == "/v1/jobs/child-1" and request.method == "GET":
            status_calls += 1
            return httpx.Response(200, json=status_for(ack, state="accepted",
                sequence=0, gap=False, deltas=[], terminal=None,
                updated_at=str(time.time())))
        if request.url.path == "/v1/jobs/child-1/cancel":
            cancellations += 1
            return httpx.Response(200, json={"job_id": "child-1", "state": "unknown"})
        raise AssertionError(request.url)

    sink = Sink()
    started = time.monotonic()
    result = adapter(agent, first_output_seconds=0.04).run(lease(), "prompt", sink)
    assert result.state == "uncertain"
    assert result.uncertainty == "first_output_deadline"
    assert 1 < status_calls < 100
    assert cancellations == 1
    assert time.monotonic() - started < 0.2
    assert EventType.RESPONSE_COMPLETED not in [kind for kind, _ in sink.events]


def test_sequence_gap_never_becomes_completed_answer():
    ack = None
    def agent(request):
        nonlocal ack
        if request.url.path == "/v1/jobs" and request.method == "POST":
            ack = ack_for(request)
            return httpx.Response(202, json=ack)
        if request.url.path == "/v1/jobs/child-1" and request.method == "GET":
            return httpx.Response(200, json=status_for(ack, state="completed",
                sequence=2, gap=True, deltas=[{"sequence": 2, "text": "late"}],
                terminal={"model": "qwen", "done": True}))
        if request.url.path == "/v1/jobs/child-1/cancel":
            return httpx.Response(200, json={"job_id": "child-1", "state": "completed"})
        raise AssertionError(request.url)

    sink = Sink()
    result = adapter(agent).run(lease(), "prompt", sink)
    assert result.state == "uncertain" and result.answer is None
    assert EventType.RESPONSE_COMPLETED not in [kind for kind, _ in sink.events]


def test_model_digest_mismatch_blocks_preflight():
    def agent(request):
        return httpx.Response(200, json={"model_ready": False, "model": "qwen",
                                         "model_digest": DIGEST, "observed_digest": "c" * 64})

    assert adapter(agent).preflight().ready is False


def test_idle_output_deadline_is_not_extended_by_healthy_status():
    ack = None
    polls = 0

    def agent(request):
        nonlocal ack, polls
        if request.url.path == "/v1/jobs":
            ack = ack_for(request)
            return httpx.Response(202, json=ack)
        if request.url.path == "/v1/jobs/child-1/cancel":
            return httpx.Response(200, json={"job_id": "child-1", "state": "unknown"})
        polls += 1
        return httpx.Response(200, json=status_for(ack, state="streaming", sequence=1,
            gap=False, deltas=[{"sequence": 1, "text": "first"}] if polls == 1 else [],
            terminal=None))

    result = adapter(agent, idle_output_seconds=0.025).run(lease(), "prompt", Sink())
    assert result.state == "uncertain" and result.uncertainty == "idle_output_deadline"


def test_total_deadline_survives_continuous_output():
    ack = None
    polls = 0

    def agent(request):
        nonlocal ack, polls
        if request.url.path == "/v1/jobs":
            ack = ack_for(request)
            return httpx.Response(202, json=ack)
        if request.url.path == "/v1/jobs/child-1/cancel":
            return httpx.Response(200, json={"job_id": "child-1", "state": "unknown"})
        polls += 1
        return httpx.Response(200, json=status_for(ack, state="streaming", sequence=polls,
            gap=False, deltas=[{"sequence": polls, "text": "x"}], terminal=None))

    result = adapter(agent, timeout_seconds=0.025).run(lease(), "prompt", Sink())
    assert result.state == "uncertain" and result.uncertainty == "local_execution_deadline"


def test_two_failed_status_polls_fall_back_without_retrying_submit():
    submissions = 0
    cancellations = 0

    def agent(request):
        nonlocal submissions, cancellations
        if request.url.path == "/v1/jobs":
            submissions += 1
            return httpx.Response(202, json=ack_for(request))
        if request.url.path == "/v1/jobs/child-1/cancel":
            cancellations += 1
            return httpx.Response(200)
        return httpx.Response(503)

    result = adapter(agent).run(lease(), "prompt", Sink())
    assert result.state == "uncertain" and result.uncertainty == "agent_status_unavailable"
    assert submissions == 1 and cancellations == 1


def test_terminal_reply_arriving_after_local_deadline_is_not_delivered():
    ack = None

    def agent(request):
        nonlocal ack
        if request.url.path == "/v1/jobs":
            ack = ack_for(request)
            return httpx.Response(202, json=ack)
        if request.url.path == "/v1/jobs/child-1/cancel":
            return httpx.Response(200, json={"job_id": "child-1", "state": "completed"})
        time.sleep(0.03)
        return httpx.Response(200, json=status_for(ack, state="completed", sequence=1,
            gap=False, deltas=[{"sequence": 1, "text": "too late"}],
            terminal={"model": "qwen", "done": True}))

    sink = Sink()
    result = adapter(agent, timeout_seconds=0.02).run(lease(), "prompt", sink)
    assert result.state == "uncertain" and result.uncertainty == "local_execution_deadline"
    assert EventType.RESPONSE_COMPLETED not in [kind for kind, _ in sink.events]


def test_slow_telemetry_write_cannot_turn_expired_result_into_completed():
    ack = None

    def agent(request):
        nonlocal ack
        if request.url.path == "/v1/jobs":
            ack = ack_for(request)
            return httpx.Response(202, json=ack)
        if request.url.path == "/v1/jobs/child-1/cancel":
            return httpx.Response(200, json={"job_id": "child-1", "state": "unknown"})
        return httpx.Response(200, json=status_for(ack, state="completed", sequence=1,
            gap=False, deltas=[{"sequence": 1, "text": "late"}],
            terminal={"model": "qwen", "done": True}))

    class SlowSink(Sink):
        def emit(self, kind, **kwargs):
            if kind == EventType.RESPONSE_DELTA:
                time.sleep(0.03)
            super().emit(kind, **kwargs)

    sink = SlowSink()
    result = adapter(agent, timeout_seconds=0.02).run(lease(), "prompt", sink)
    assert result.state == "uncertain" and result.uncertainty == "local_execution_deadline"
    assert EventType.RESPONSE_COMPLETED not in [kind for kind, _ in sink.events]


def test_shared_local_cutoff_is_checked_after_slow_status_page():
    ack = None

    def agent(request):
        nonlocal ack
        if request.url.path == "/v1/jobs":
            ack = ack_for(request)
            return httpx.Response(202, json=ack)
        if request.url.path == "/v1/jobs/child-1/cancel":
            return httpx.Response(200)
        time.sleep(0.03)
        return httpx.Response(200, json=status_for(ack, state="completed", sequence=1,
            gap=False, deltas=[{"sequence": 1, "text": "late"}],
            terminal={"model": "qwen", "done": True}))

    cutoff = datetime.now(timezone.utc) + timedelta(milliseconds=20)
    result = adapter(agent).run(lease(), "prompt", Sink(), deadline_at=cutoff)
    assert result.state == "uncertain" and result.uncertainty == "local_execution_deadline"


def test_server_error_after_submit_attempts_cancel_without_resubmitting():
    submissions = 0
    cancellations = 0

    def agent(request):
        nonlocal submissions, cancellations
        if request.url.path == "/v1/jobs":
            submissions += 1
            return httpx.Response(503)
        if request.url.path == "/v1/jobs/child-1/cancel":
            cancellations += 1
            return httpx.Response(404)
        raise AssertionError(request.url)

    result = adapter(agent).run(lease(), "prompt", Sink())
    assert result.state == "uncertain" and result.uncertainty == "agent_submission_uncertain"
    assert submissions == 1 and cancellations == 1


def test_duplicate_status_page_does_not_duplicate_output():
    ack = None
    polls = 0

    def agent(request):
        nonlocal ack, polls
        if request.url.path == "/v1/jobs":
            ack = ack_for(request)
            return httpx.Response(202, json=ack)
        polls += 1
        if polls == 1:
            return httpx.Response(200, json=status_for(ack, state="streaming", sequence=1,
                gap=False, deltas=[{"sequence": 1, "text": "hel"}], terminal=None))
        if polls == 2:
            return httpx.Response(200, json=status_for(ack, state="streaming", sequence=1,
                gap=False, deltas=[], terminal=None))
        return httpx.Response(200, json=status_for(ack, state="completed", sequence=2,
            gap=False, deltas=[{"sequence": 2, "text": "lo"}],
            terminal={"model": "qwen", "done": True}))

    sink = Sink()
    result = adapter(agent).run(lease(), "prompt", sink)
    assert result.state == "completed" and result.answer == "hello"
    assert [kind for kind, _ in sink.events].count(EventType.RESPONSE_DELTA) == 2


def test_agent_restart_unknown_state_never_completes_old_job():
    ack = None

    def agent(request):
        nonlocal ack
        if request.url.path == "/v1/jobs":
            ack = ack_for(request)
            return httpx.Response(202, json=ack)
        if request.url.path.endswith("/cancel"):
            return httpx.Response(200, json={"job_id": "child-1", "state": "unknown"})
        return httpx.Response(200, json=status_for(ack, state="unknown", sequence=0,
            gap=False, deltas=[], terminal={"reason": "agent_restart"}))

    result = adapter(agent).run(lease(), "prompt", Sink())
    assert result.state == "uncertain" and result.uncertainty == "agent_unknown"


def test_slow_pre_submit_capture_does_not_send_expired_job():
    submissions = 0

    def agent(request):
        nonlocal submissions
        submissions += 1
        raise AssertionError("expired job was submitted")

    class SlowSink(Sink):
        def emit(self, kind, **kwargs):
            if kind == EventType.MESSAGE_SENT:
                time.sleep(0.03)
            super().emit(kind, **kwargs)

    result = adapter(agent, timeout_seconds=0.02).run(lease(), "prompt", SlowSink())
    assert result.state == "denied" and result.uncertainty == "local_cutoff_passed"
    assert submissions == 0


def test_poll_sleep_is_clipped_to_first_output_deadline():
    ack = None

    def agent(request):
        nonlocal ack
        if request.url.path == "/v1/jobs":
            ack = ack_for(request)
            return httpx.Response(202, json=ack)
        if request.url.path.endswith("/cancel"):
            return httpx.Response(200)
        return httpx.Response(200, json=status_for(ack, state="accepted", sequence=0,
            gap=False, deltas=[], terminal=None))

    started = time.monotonic()
    result = adapter(agent, first_output_seconds=0.04, poll_seconds=0.2).run(
        lease(), "prompt", Sink())
    assert result.uncertainty == "first_output_deadline"
    assert time.monotonic() - started < 0.17
