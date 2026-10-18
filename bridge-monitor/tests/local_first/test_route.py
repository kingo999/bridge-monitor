"""The exact-key verifier here models an offline holdout, never a live category gate."""

from importlib import import_module

import pytest


def subject():
    return import_module("bridge.local_first")


def settings(**overrides):
    data = {"mode": "evaluation", "canary_percent": 100,
            "categories": ["code_reasoning"], "max_local_seconds": 30}
    return subject().LocalFirstSettings.model_validate({**data, **overrides})


def reply(raw='{"answer":"43"}', *, state="completed", uncertainty=None):
    return subject().ModelReply(state=state, raw=raw, reported_model="qwen-test",
                                usage={"input_tokens": 10, "output_tokens": 5},
                                elapsed_seconds=0.5, uncertainty=uncertainty)


def test_disabled_route_uses_only_paid_worker():
    m = subject()
    calls = []
    result = m.route("item-1", "code_reasoning", "question",
        settings(mode="disabled"), lambda answer: answer == "43",
        lambda prompt: calls.append("local") or reply(),
        lambda prompt: calls.append("paid") or reply('{"answer":"43"}'))
    assert calls == ["paid"]
    assert result.route == "paid" and result.reason == "disabled"
    assert result.answer == "43" and result.local is None


def test_unregistered_verifier_and_category_do_not_dispatch_local():
    m = subject()
    for category, verifier, expected_reason in [
        ("code_reasoning", None, "verifier_unavailable"),
        ("open_ended", lambda value: True, "category_ineligible"),
    ]:
        calls = []
        result = m.route("item-2", category, "question", settings(), verifier,
            lambda prompt: calls.append("local") or reply(),
            lambda prompt: calls.append("paid") or reply())
        assert calls == ["paid"] and result.reason == expected_reason


def test_exact_verified_local_answer_skips_paid_worker_and_preserves_raw():
    m = subject()
    calls = []
    result = m.route("item-3", "code_reasoning", "question", settings(),
        lambda answer: answer == "43",
        lambda prompt: calls.append("local") or reply('```json\n{"answer":"43"}\n```'),
        lambda prompt: calls.append("paid") or reply())
    assert calls == ["local"]
    assert result.route == "local" and result.reason == "verified"
    assert result.answer == "43" and result.paid is None
    assert result.local.raw.startswith("```json")


def test_verified_value_is_the_value_delivered_after_normalization():
    m = subject()
    result = m.route("item-spaced", "code_reasoning", "question", settings(),
        lambda answer: answer.strip() == "42",
        lambda prompt: reply('{"answer":" 42\\n"}'),
        lambda prompt: reply('{"answer":"paid"}'))
    assert result.route == "local" and result.answer == "42"
    assert result.local_answer == "42"
    assert result.local.raw == '{"answer":" 42\\n"}'


def test_ambiguous_duplicate_json_answer_keys_are_rejected():
    m = subject()
    assert m.parse_answer('{"answer":"wrong","answer":"42"}') is None


@pytest.mark.parametrize("local,reason", [
    ('{"answer":"85"}', "verification_failed"),
    ('{"answer":43}', "invalid_local_format"),
    ('prefix {"answer":"43"}', "invalid_local_format"),
    ('{"answer":"43","extra":true}', "invalid_local_format"),
])
def test_wrong_or_malformed_local_answer_falls_back_once(local, reason):
    m = subject()
    calls = []
    result = m.route("item-4", "code_reasoning", "question", settings(),
        lambda answer: answer == "43",
        lambda prompt: calls.append("local") or reply(local),
        lambda prompt: calls.append("paid") or reply('{"answer":"43"}'))
    assert calls == ["local", "paid"]
    assert result.route == "paid" and result.reason == reason
    assert result.answer == "43" and result.local.raw == local


@pytest.mark.parametrize("state", ["uncertain", "failed", "denied"])
def test_nonterminal_local_result_is_not_retried_and_uses_paid_fallback(state):
    m = subject()
    calls = []
    result = m.route("item-5", "code_reasoning", "question", settings(),
        lambda answer: answer == "43",
        lambda prompt: calls.append("local") or reply("partial", state=state,
                                                    uncertainty="deadline"),
        lambda prompt: calls.append("paid") or reply())
    assert calls == ["local", "paid"]
    assert result.route == "paid" and result.reason == "local_" + state


def test_verifier_exception_fails_closed_to_paid():
    m = subject()
    calls = []
    def broken(_answer):
        raise RuntimeError("checker unavailable")
    result = m.route("item-6", "code_reasoning", "question", settings(), broken,
        lambda prompt: calls.append("local") or reply(),
        lambda prompt: calls.append("paid") or reply())
    assert calls == ["local", "paid"] and result.reason == "verification_unavailable"


@pytest.mark.parametrize("paid", [
    ("failed", "transport error"),
    ("completed", "Here is an explanation instead of JSON"),
])
def test_unparseable_or_failed_paid_fallback_is_not_exposed_as_answer(paid):
    m = subject()
    result = m.route("item-paid-error", "code_reasoning", "question", settings(),
        lambda answer: answer == "42",
        lambda prompt: reply('{"answer":"wrong"}'),
        lambda prompt: reply(paid[1], state=paid[0]))
    assert result.route == "paid" and result.answer is None
    assert result.paid.raw == paid[1] and result.paid_format_valid is False


def test_sampling_is_stable_and_zero_percent_cannot_dispatch_local():
    m = subject()
    assert m.sampled("same-item", 37) == m.sampled("same-item", 37)
    assert not m.sampled("same-item", 0)
    assert m.sampled("same-item", 100)
    calls = []
    result = m.route("same-item", "code_reasoning", "question",
        settings(canary_percent=0), lambda value: True,
        lambda prompt: calls.append("local") or reply(),
        lambda prompt: calls.append("paid") or reply())
    assert calls == ["paid"] and result.reason == "not_sampled"


def test_config_rejects_unknown_keys_and_unsafe_sampling(tmp_path):
    m = subject()
    config = tmp_path / "trial.toml"
    config.write_text("mode = 'evaluation'\ncanary_percent = 101\n", encoding="utf-8")
    with pytest.raises(ValueError):
        m.load_settings(config)
    config.write_text("mode = 'evaluation'\ncanary_percent = 100\nunknown = true\n",
                      encoding="utf-8")
    with pytest.raises(ValueError):
        m.load_settings(config)


def test_runtime_limits_are_configurable_and_positive():
    m = subject()
    configured = m.LocalFirstSettings.model_validate({"paid_timeout_seconds": 75,
        "paid_max_budget_usd": 0.12, "local_output_tokens": 320})
    assert (configured.paid_timeout_seconds, configured.paid_max_budget_usd,
            configured.local_output_tokens) == (75, 0.12, 320)
    with pytest.raises(ValueError):
        m.LocalFirstSettings.model_validate({"local_output_tokens": 0})
