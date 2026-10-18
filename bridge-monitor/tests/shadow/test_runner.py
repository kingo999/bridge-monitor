import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bridge.code_fix_verifier import CaseValidation, VerificationResult
from bridge.local_first import ModelReply
from bridge.code_fix_shadow_runner import run_shadow_cases
from bridge.shadow_cohort import summarize_receipts


CASES = [{"id": "real-fix-1", "task": "Make answer return 42.",
          "baseline_source": "def answer(): return 41\n",
          "reference_source": "def answer(): return 42\n",
          "tests": [{"function": "answer", "args": [], "expected": 42}]}]

NATURAL_SETTINGS = {"mode": "shadow", "cohort": "natural_shadow",
    "cohort_start_at": "2026-10-18T13:46:18+00:00",
    "excluded_case_ids": ["historical-answer-normalization", "historical-paid-fallback-format"],
    "excluded_baseline_sha256": ["e3b6d0d022590318e5103c0e1f767cb916daab7c430cea794fe4e69b88a77e4b",
        "639679fff40fd3737039a190f4931217d60a60bf1095587dce55f4be5ea74483"],
    "excluded_task_sha256": ["7c433a588a3aed602ce0633f1e9a47a3b3b9ff3a44d3f242459e7b05c74b5d54",
        "17dd55b6336069aa74a89f911c6ea04d9d166a10ddad1f373d64d1f26e58b596"]}


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


def test_configured_prompt_is_shared_by_both_workers_and_pinned(tmp_path):
    prompts = []
    path = tmp_path / "prompt.json"
    def worker(prompt, model):
        prompts.append(prompt)
        return reply(CASES[0]["reference_source"], model)
    run_shadow_cases(CASES, {"mode": "shadow"}, path, FakeVerifier(),
        lambda prompt: worker(prompt, "qwen"),
        lambda prompt: worker(prompt, "sonnet"), lambda _: {},
        prompt_prefix="Return the complete replacement module as JSON.\n")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert len(prompts) == 2 and prompts[0] == prompts[1]
    assert prompts[0].startswith("Return the complete replacement module")
    assert saved["prompt_prefix"] == "Return the complete replacement module as JSON.\n"
    with pytest.raises(ValueError, match="changed"):
        run_shadow_cases(CASES, {"mode": "shadow"}, path, FakeVerifier(),
            lambda _: pytest.fail("local replay"), lambda _: pytest.fail("paid replay"),
            lambda _: pytest.fail("parent replay"), prompt_prefix="different")


def test_natural_case_requires_provenance_before_dispatch(tmp_path):
    settings = NATURAL_SETTINGS
    with pytest.raises(ValueError, match="provenance"):
        run_shadow_cases(CASES, settings, tmp_path / "bad.json", FakeVerifier(),
            lambda _: pytest.fail("local called"), lambda _: pytest.fail("paid called"),
            lambda _: pytest.fail("parent called"))
    natural = {**CASES[0], "provenance": {
        "origin_ref": "codex-task-2026-10-19-001",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "baseline_ref": "git:abc123", "test_ref": "git:def456"}}
    receipt = run_shadow_cases([natural], settings, tmp_path / "good.json",
        FakeVerifier(), lambda _: reply(natural["reference_source"], "qwen"),
        lambda _: reply(natural["reference_source"], "sonnet"), lambda _: {})
    assert receipt["cohort"] == "natural_shadow"
    assert receipt["cases"][0]["provenance"]["origin_ref"] == "codex-task-2026-10-19-001"
    assert receipt["results"][natural["id"]]["arm_elapsed_seconds"]["direct"] >= 0
    assert natural["task"] not in receipt["results"][natural["id"]]["parent_prompts"]["direct"]
    assert "Task SHA-256" in receipt["results"][natural["id"]]["parent_prompts"]["direct"]
    assert summarize_receipts([tmp_path / "good.json"], target_cases=1)["eligible_cases"] == 1
    with pytest.raises(ValueError, match="one case"):
        run_shadow_cases([natural, {**natural, "id": "another"}], settings,
            tmp_path / "many.json", FakeVerifier(), lambda _: pytest.fail("local"),
            lambda _: pytest.fail("paid"), lambda _: pytest.fail("parent"))


def test_retro_baseline_and_future_capture_are_rejected_before_dispatch(tmp_path):
    historical = json.loads((Path(__file__).resolve().parents[1] / "fixtures" /
        "code-fix-shadow-20261018.json").read_text(encoding="utf-8"))[0]
    historical["id"] = "new-looking-id"
    historical["provenance"] = {"origin_ref": "new-looking-origin",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "baseline_ref": "git:claimed", "test_ref": "git:claimed"}
    with pytest.raises(ValueError, match="excluded retrospective"):
        run_shadow_cases([historical], NATURAL_SETTINGS, tmp_path / "historical.json",
            FakeVerifier(), lambda _: pytest.fail("qwen dispatched"),
            lambda _: pytest.fail("sonnet dispatched"), lambda _: {})
    fresh = {**CASES[0], "provenance": {**historical["provenance"],
        "captured_at": "2099-01-01T00:00:00+00:00"}}
    with pytest.raises(ValueError, match="capture time"):
        run_shadow_cases([fresh], NATURAL_SETTINGS, tmp_path / "future.json",
            FakeVerifier(), lambda _: pytest.fail("qwen dispatched"),
            lambda _: pytest.fail("sonnet dispatched"), lambda _: {})
