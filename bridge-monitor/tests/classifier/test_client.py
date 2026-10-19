"""Transport behavior: bounded, single-attempt, no repair, no retry."""

import threading
import time

import pytest

from bridge.classifier.interpret import interpret
from contracts.classifier import ClassifierInput, TrustedFacts
from tests.classifier.fakes import DIGEST, MODEL, FakeOllama, reply_json
from bridge.classifier.rubric import RESPONSE_SCHEMA, RUBRIC

FACTS = TrustedFacts(general_available=True, code_available=True, paid_allowed=True)


def run(fake, **kwargs):
    client = fake.classifier(**kwargs.pop("client", {}))
    outcome = client.classify("{}", **kwargs)
    return outcome, interpret(outcome, expected_model=MODEL, facts=FACTS)


def test_wire_request_matches_the_measured_benchmark_profile():
    fake = FakeOllama()
    run(fake)
    body = fake.chat_bodies[0]
    assert body["model"] == MODEL and body["think"] is False and body["stream"] is True
    assert body["format"] == RESPONSE_SCHEMA
    assert body["messages"][0] == {"role": "system", "content": RUBRIC}
    assert body["options"] == {"num_ctx": 8192, "num_predict": 192, "temperature": 0.0,
        "seed": 42, "top_k": 40, "top_p": 0.9, "repeat_penalty": 1.0, "presence_penalty": 0.0}


def test_valid_stream_completes_with_terminal_evidence_and_usage():
    outcome, interp = run(FakeOllama(), seed=43)
    assert outcome.terminal_ok and outcome.usage == {"input_tokens": 700, "output_tokens": 40}
    assert outcome.durations_ms["load_ms"] == 40 and outcome.first_content_seconds is not None
    assert interp.state == "completed" and interp.recommendation.route == "paid"


def test_token_limit_stop_is_incomplete_not_success():
    outcome, interp = run(FakeOllama(mode="length"))
    assert outcome.status == "incomplete" and outcome.error_type == "token_limit"
    assert interp.state == "failed" and interp.recommendation is None


def test_partial_stream_without_terminal_is_incomplete():
    outcome, interp = run(FakeOllama(mode="no_terminal"))
    assert outcome.status == "incomplete" and outcome.error_type == "missing_terminal_frame"
    assert interp.state == "failed" and interp.recommendation is None


def test_wrong_reported_model_is_not_accepted():
    _, interp = run(FakeOllama(mode="wrong_model"))
    assert interp.state == "failed" and interp.error_type == "reported_model_mismatch"


def test_thinking_output_is_rejected():
    _, interp = run(FakeOllama(mode="thinking"))
    assert interp.error_type == "unexpected_thinking_output"


def test_oversized_response_is_bounded():
    outcome, interp = run(FakeOllama(text=reply_json(reason="r" * 300)),
                          client={"max_response_bytes": 60})
    assert outcome.status == "oversized" and interp.state == "failed"


@pytest.mark.parametrize("text,code", [
    ("not json", "not_json"),
    ('{"category":"code","route":"tools","reason":"x","extra":1}', "extra_field"),
    ('{"category":"code","route":"nowhere","reason":"x"}', "unknown_route"),
    ("```json\n" + reply_json() + "\n```", "markdown_wrapper"),
])
def test_contract_invalid_output_fails_without_repair_and_keeps_the_raw_text(text, code):
    outcome, interp = run(FakeOllama(text=text))
    assert interp.state == "failed" and interp.valid is False
    assert interp.validation_error == code and interp.recommendation is None
    assert outcome.raw_text == text


def test_http_error_and_error_frame_fail_once_without_retry():
    for mode in ("http500", "error_frame"):
        fake = FakeOllama(mode=mode)
        outcome, interp = run(fake)
        assert outcome.status == "http_error" and interp.state == "failed"
        assert fake.chat_calls == 1


def test_unavailable_endpoint_is_reported_without_a_send():
    fake = FakeOllama(mode="down")
    client = fake.classifier()
    assert client.check_identity().reason == "endpoint_unavailable"
    outcome = client.classify("{}")
    assert outcome.sent is False and outcome.status == "transport_error"
    assert interpret(outcome, expected_model=MODEL, facts=FACTS).state == "failed"


def test_timeout_mid_stream_is_uncertain_and_never_retried():
    fake = FakeOllama(mode="slow", pause=5.0)
    began = time.perf_counter()
    outcome, interp = run(fake, deadline_seconds=0.3)
    assert time.perf_counter() - began < 3
    assert outcome.status == "timeout" and interp.state == "uncertain"
    assert fake.chat_calls == 1


def test_cancellation_stops_the_stream_and_is_not_a_success():
    fake = FakeOllama(mode="slow", pause=5.0)
    cancel = threading.Event()
    threading.Timer(0.2, cancel.set).start()
    began = time.perf_counter()
    outcome, interp = run(fake, cancel=cancel)
    assert time.perf_counter() - began < 3
    assert outcome.status == "cancelled" and interp.state == "cancelled"
    assert interp.recommendation is None and fake.chat_calls == 1


def test_identity_checks_distinguish_wrong_digest_and_missing_model():
    assert FakeOllama().classifier().check_identity().ok
    wrong = FakeOllama(digest="0" * 64).classifier().check_identity()
    assert (wrong.ok, wrong.reason, wrong.observed_digest) == (False, "model_digest_mismatch", "0" * 64)
    missing = FakeOllama(model="other:1b").classifier().check_identity()
    assert missing.reason == "model_not_installed"


@pytest.mark.parametrize("kwargs,state", [
    (dict(), "gpu_resident"), (dict(vram=0), "cpu_only"), (dict(vram=4_000), "partial_offload"),
    (dict(resident=False), "not_resident")])
def test_residency_detects_cpu_fallback(kwargs, state):
    check = FakeOllama(**kwargs).classifier().residency()
    assert check.state == state and check.gpu_demonstrated == (state == "gpu_resident")


def test_endpoint_must_be_explicit_loopback():
    for endpoint in ("http://192.168.0.78:11434", "https://127.0.0.1:11435", "http://example.com:11435"):
        with pytest.raises(ValueError):
            FakeOllama().classifier(endpoint=endpoint)
    with pytest.raises(ValueError):
        FakeOllama().classifier(expected_digest="abc")
