import json
from dataclasses import asdict

import pytest

from bridge.code_fix_verifier import CaseValidation, VerificationResult
from bridge.local_first import ModelReply
from bridge.code_fix_shadow_runner import run_shadow_cases


CASES = [{"id": "real-fix-1", "task": "Make answer return 42.",
          "baseline_source": "def answer(): return 41\n",
          "reference_source": "def answer(): return 42\n",
          "tests": [{"function": "answer", "args": [], "expected": 42}]}]


class FakeVerifier:
    def validate_case(self, _baseline, _reference, _tests):
        good = VerificationResult("pass", "tests_passed", 0.1, "x")
        bad = VerificationResult("fail", "tests_failed", 0.1, "y")
        return CaseValidation("ready", "trusted_tests_distinguish_fix", good, bad)

    def verify(self, source, _tests):
        return VerificationResult("pass" if "42" in source else "fail",
            "tests_passed" if "42" in source else "tests_failed", 0.1, "z")


def reply(source, model):
    return ModelReply(state="completed", raw=json.dumps({"answer": source}),
        requested_model=model, reported_model=model,
        usage={"input_tokens": 10, "output_tokens": 4}, elapsed_seconds=1)


def test_shadow_receipt_captures_both_arms_parent_usage_and_resume(tmp_path):
    calls = []
    def local(prompt):
        calls.append("qwen")
        return reply(CASES[0]["reference_source"], "qwen")
    def paid(prompt):
        calls.append("sonnet")
        return reply(CASES[0]["reference_source"], "sonnet")
    def parent(prompt):
        calls.append("gpt")
        assert "def answer" not in prompt
        assert "Source SHA-256" in prompt
        return {"state": "completed", "requested_model": "gpt-test",
                "reported_model": None, "usage": {"input_tokens": 80},
                "answer": "done", "elapsed_seconds": 1}
    path = tmp_path / "receipt.json"
    settings = {"mode": "shadow", "parent_model": "gpt-test"}
    receipt = run_shadow_cases(CASES, settings, path, FakeVerifier(), local,
        paid, parent, run_metadata={"image": "pinned"})
    assert calls == ["sonnet", "qwen", "gpt", "gpt"]
    item = receipt["results"]["real-fix-1"]
    assert item["local_first"]["route"] == "local"
    assert item["direct_verification"]["state"] == "pass"
    assert item["parent"]["local_first"]["usage"]["input_tokens"] == 80
    assert len(receipt["contract_sha256"]) == 64
    assert json.loads(path.read_text(encoding="utf-8")) == receipt
    assert run_shadow_cases(CASES, settings, path, FakeVerifier(), local,
        paid, parent, run_metadata={"image": "pinned"}) == receipt
    assert len(calls) == 4
    with pytest.raises(ValueError, match="changed"):
        run_shadow_cases(CASES, settings, path, FakeVerifier(), local,
            paid, parent, run_metadata={"image": "different"})


def test_failed_qwen_patch_routes_to_sonnet_and_preserves_failed_test(tmp_path):
    calls = []
    receipt = run_shadow_cases(CASES, {"mode": "shadow"}, tmp_path / "f.json",
        FakeVerifier(), lambda prompt: calls.append("qwen") or reply("def answer(): return 41\n", "qwen"),
        lambda prompt: calls.append("sonnet") or reply(CASES[0]["reference_source"], "sonnet"),
        lambda prompt: {"state": "usage_unavailable", "usage": None})
    item = receipt["results"]["real-fix-1"]
    assert calls == ["sonnet", "qwen", "sonnet"]
    assert item["local_first"]["reason"] == "verification_failed"
    assert item["local_verification"]["state"] == "fail"
    assert item["local_first"]["paid"] is not None


def test_bad_reference_or_baseline_rejects_before_dispatch(tmp_path):
    class Rejected(FakeVerifier):
        def validate_case(self, *_args):
            result = VerificationResult("fail", "tests_failed", 0, "x")
            return CaseValidation("rejected", "reference_not_passing", result)
    with pytest.raises(ValueError, match="reference_not_passing"):
        run_shadow_cases(CASES, {"mode": "shadow"}, tmp_path / "bad.json",
            Rejected(), lambda _: pytest.fail("local called"),
            lambda _: pytest.fail("paid called"),
            lambda _: pytest.fail("parent called"))


def test_interrupted_case_is_not_replayed(tmp_path):
    path = tmp_path / "interrupted.json"
    def interrupted(_prompt):
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        run_shadow_cases(CASES, {"mode": "shadow"}, path, FakeVerifier(),
            interrupted, lambda _: reply(CASES[0]["reference_source"], "sonnet"),
            lambda _: {})
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["in_progress"]["direct"] is not None
    with pytest.raises(ValueError, match="interrupted"):
        run_shadow_cases(CASES, {"mode": "shadow"}, path, FakeVerifier(),
            lambda _: pytest.fail("local replay"),
            lambda _: pytest.fail("paid replay"), lambda _: pytest.fail("parent replay"))


def test_existing_lock_prevents_concurrent_run(tmp_path):
    path = tmp_path / "receipt.json"
    path.with_name("receipt.json.lock").write_text("held", encoding="utf-8")
    with pytest.raises(ValueError, match="locked"):
        run_shadow_cases(CASES, {"mode": "shadow"}, path, FakeVerifier(),
            lambda _: pytest.fail("local called"), lambda _: pytest.fail("paid called"),
            lambda _: pytest.fail("parent called"))


def test_completed_result_drift_is_rejected_on_resume(tmp_path):
    path = tmp_path / "receipt.json"
    settings = {"mode": "shadow"}
    run_shadow_cases(CASES, settings, path, FakeVerifier(),
        lambda _: reply(CASES[0]["reference_source"], "qwen"),
        lambda _: reply(CASES[0]["reference_source"], "sonnet"),
        lambda _: {"state": "completed", "usage": {"input_tokens": 1}})
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["results"]["real-fix-1"]["local_first"]["route"] = "paid"
    path.write_text(json.dumps(saved), encoding="utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        run_shadow_cases(CASES, settings, path, FakeVerifier(),
            lambda _: pytest.fail("local replay"),
            lambda _: pytest.fail("paid replay"),
            lambda _: pytest.fail("parent replay"))
