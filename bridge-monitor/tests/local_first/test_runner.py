import json
from importlib import import_module


def test_receipt_captures_both_paths_and_resumes_without_repeating_calls(tmp_path):
    m = import_module("bridge.local_first")
    runner = import_module("bridge.local_first_runner")
    settings = m.LocalFirstSettings(mode="evaluation", canary_percent=100,
        categories=["arithmetic"], max_local_seconds=20)
    cases = [{"id": "fresh-1", "category": "arithmetic",
              "question": "What is 6 times 7?", "answer": "42"}]
    calls = []
    def local(prompt):
        calls.append(("qwen", prompt))
        return m.ModelReply(state="completed", raw='```json\n{"answer":"42"}\n```',
                            requested_model="qwen-requested",
                            reported_model="qwen-pinned",
                            usage={"input_tokens": 12, "output_tokens": 8},
                            elapsed_seconds=0.4)
    def paid(prompt):
        calls.append(("sonnet", prompt))
        return m.ModelReply(state="completed", raw='{"answer":"42"}',
                            requested_model="sonnet",
                            reported_model="sonnet-pinned",
                            list_cost_usd=0.002, cost_basis="list",
                            usage={"input_tokens": 20, "output_tokens": 8},
                            elapsed_seconds=2.2)
    path = tmp_path / "receipt.json"
    metadata = {"qwen_digest": "a" * 64, "paid_model": "sonnet"}
    first = runner.run_cases(cases, settings, path, local, paid,
                             run_metadata=metadata)
    assert len(calls) == 2  # direct arm + local arm, without a fallback
    assert first["results"]["fresh-1"]["local_first"]["route"] == "local"
    assert first["results"]["fresh-1"]["direct"]["reported_model"] == "sonnet-pinned"
    assert first["results"]["fresh-1"]["direct"]["requested_model"] == "sonnet"
    assert first["results"]["fresh-1"]["direct"]["list_cost_usd"] == 0.002
    assert first["results"]["fresh-1"]["local_first"]["local"]["raw"].startswith("```json")
    assert first["results"]["fresh-1"]["prompt"].endswith("What is 6 times 7?")
    assert first["run_metadata"] == metadata
    assert len(first["contract_sha256"]) == 64
    assert json.loads(path.read_text(encoding="utf-8")) == first
    again = runner.run_cases(cases, settings, path, local, paid,
                             run_metadata=metadata)
    assert again == first and len(calls) == 2
    import pytest
    with pytest.raises(ValueError, match="holdout changed"):
        runner.run_cases(cases, settings, path, local, paid,
                         run_metadata={**metadata, "paid_model": "opus"})


def test_receipt_rejects_changed_holdout_and_records_fallback(tmp_path):
    m = import_module("bridge.local_first")
    runner = import_module("bridge.local_first_runner")
    settings = m.LocalFirstSettings(mode="evaluation", canary_percent=100,
        categories=["arithmetic"], max_local_seconds=20)
    cases = [{"id": "fresh-2", "category": "arithmetic",
              "question": "What is 9 times 9?", "answer": "81"}]
    calls = []
    def local(prompt):
        calls.append("qwen")
        return m.ModelReply(state="completed", raw='{"answer":"80"}',
                            reported_model="qwen-pinned")
    def paid(prompt):
        calls.append("sonnet")
        return m.ModelReply(state="completed", raw='{"answer":"81"}',
                            reported_model="sonnet-pinned")
    path = tmp_path / "receipt.json"
    receipt = runner.run_cases(cases, settings, path, local, paid)
    outcome = receipt["results"]["fresh-2"]["local_first"]
    assert calls == ["sonnet", "qwen", "sonnet"]
    assert outcome["route"] == "paid" and outcome["reason"] == "verification_failed"
    assert receipt["results"]["fresh-2"]["local_first_exact"] is True
    changed = [{**cases[0], "question": "What is 8 times 8?"}]
    import pytest
    with pytest.raises(ValueError, match="holdout changed"):
        runner.run_cases(changed, settings, path, local, paid)
    assert calls == ["sonnet", "qwen", "sonnet"]


def test_arm_order_alternates_across_cases(tmp_path):
    m = import_module("bridge.local_first")
    runner = import_module("bridge.local_first_runner")
    settings = m.LocalFirstSettings(mode="evaluation", canary_percent=100,
        categories=["arithmetic"])
    cases = [{"id": "first", "category": "arithmetic", "question": "A?", "answer": "42"},
             {"id": "second", "category": "arithmetic", "question": "B?", "answer": "42"}]
    calls = []
    def local(prompt):
        calls.append("local")
        return m.ModelReply(state="completed", raw='{"answer":"42"}')
    def paid(prompt):
        calls.append("paid")
        return m.ModelReply(state="completed", raw='{"answer":"42"}')
    runner.run_cases(cases, settings, tmp_path / "order.json", local, paid)
    assert calls == ["paid", "local", "local", "paid"]


def test_interrupted_case_is_not_replayed_after_unknown_dispatch(tmp_path):
    m = import_module("bridge.local_first")
    runner = import_module("bridge.local_first_runner")
    settings = m.LocalFirstSettings(mode="evaluation", canary_percent=100,
        categories=["arithmetic"])
    cases = [{"id": "possibly-sent", "category": "arithmetic",
              "question": "What is 1 + 1?", "answer": "2"}]
    path = tmp_path / "interrupted.json"
    def interrupted(_prompt):
        raise KeyboardInterrupt("process lost while sending")
    import pytest
    with pytest.raises(KeyboardInterrupt):
        runner.run_cases(cases, settings, path, interrupted, interrupted)
    calls = []
    with pytest.raises(ValueError, match="interrupted case"):
        runner.run_cases(cases, settings, path,
            lambda prompt: calls.append("local"),
            lambda prompt: calls.append("paid"))
    assert calls == []


def test_completed_direct_arm_is_saved_if_local_dispatch_is_interrupted(tmp_path):
    m = import_module("bridge.local_first")
    runner = import_module("bridge.local_first_runner")
    settings = m.LocalFirstSettings(mode="evaluation", canary_percent=100,
        categories=["arithmetic"])
    cases = [{"id": "partial", "category": "arithmetic",
              "question": "What is 4 + 5?", "answer": "9"}]
    path = tmp_path / "partial.json"
    def interrupted(_prompt):
        raise KeyboardInterrupt("unknown local dispatch")
    import pytest
    with pytest.raises(KeyboardInterrupt):
        runner.run_cases(cases, settings, path, interrupted,
            lambda prompt: m.ModelReply(state="completed", raw='{"answer":"9"}',
                                        reported_model="sonnet-pinned"))
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["in_progress"]["case_id"] == "partial"
    assert saved["in_progress"]["direct"]["raw"] == '{"answer":"9"}'
    assert saved["results"] == {}


def test_receipt_save_retries_a_transient_windows_replace_denial(tmp_path, monkeypatch):
    from pathlib import Path
    runner = import_module("bridge.local_first_runner")
    target = tmp_path / "receipt.json"
    original_replace = Path.replace
    attempts = []
    def transient_replace(source, destination):
        attempts.append(source)
        if len(attempts) == 1:
            raise PermissionError(5, "temporary destination lock")
        return original_replace(source, destination)
    monkeypatch.setattr(Path, "replace", transient_replace)
    runner._save(target, {"saved": True})
    assert len(attempts) == 2
    assert json.loads(target.read_text(encoding="utf-8")) == {"saved": True}
