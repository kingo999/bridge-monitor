import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from bridge.code_fix_verifier import VerificationResult
from bridge.router.cases import load_case, register_case
from contracts.router import RouterCase, RouterPolicy


def _policy():
    return RouterPolicy.model_validate({"mode": "shadow", "verifier": {
        "image": "python@sha256:" + "a" * 64,
        "daemon_endpoint": "npipe:////./pipe/dockerDesktopLinuxEngine"}})


def _case():
    arrived = datetime.now(timezone.utc) - timedelta(minutes=2)
    return RouterCase(origin_kind="natural", origin_ref="codex-task:case-1",
        task_id="case-1", task_text="Fix square for n=2", task_arrived_at=arrived,
        captured_at=arrived + timedelta(seconds=10),
        baseline_source="def square(n):\n    return n + 1\n",
        tests=[{"function": "square", "args": [2], "expected": 4}],
        output_contract="complete_python_module_v1")


def test_register_case_is_pre_solution_content_addressed_and_detects_drift(tmp_path, monkeypatch):
    def failed_baseline(self, source, tests):
        return VerificationResult("fail", "tests_failed", 0.1,
            hashlib.sha256(source.encode()).hexdigest())
    monkeypatch.setattr("bridge.router.cases.DockerVerifier.verify", failed_baseline)
    case = _case()
    assert "reference_source" not in type(case).model_fields
    receipt = register_case(tmp_path, case, _policy())
    assert len(receipt.verifier_ref) == 64
    assert register_case(tmp_path, case, _policy()).verifier_ref == receipt.verifier_ref
    assert load_case(tmp_path, receipt.verifier_ref) == case
    snapshot = tmp_path / "var" / "router" / "cases" / f"{receipt.verifier_ref}.json"
    body = json.loads(snapshot.read_text(encoding="utf-8"))
    body["baseline_source"] = "def square(n):\n    return 4\n"
    snapshot.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError, match="digest"):
        load_case(tmp_path, receipt.verifier_ref)


def test_register_case_rejects_passing_baseline_and_late_capture(tmp_path, monkeypatch):
    monkeypatch.setattr("bridge.router.cases.DockerVerifier.verify",
        lambda self, source, tests: VerificationResult("pass", "tests_passed", 0.1,
            hashlib.sha256(source.encode()).hexdigest()))
    with pytest.raises(ValueError, match="baseline"):
        register_case(tmp_path, _case(), _policy())
    original = _case()
    late = original.model_copy(update={"solution_available_at": original.captured_at -
                                      timedelta(seconds=1)})
    with pytest.raises(ValueError, match="solution"):
        register_case(tmp_path, late, _policy())


def test_register_case_rejects_future_capture(tmp_path, monkeypatch):
    monkeypatch.setattr("bridge.router.cases.DockerVerifier.verify",
        lambda self, source, tests: VerificationResult("fail", "tests_failed", 0.1,
            hashlib.sha256(source.encode()).hexdigest()))
    original = _case()
    future = original.model_copy(update={"captured_at": datetime.now(timezone.utc) +
                                         timedelta(days=1)})
    with pytest.raises(ValueError, match="future"):
        register_case(tmp_path, future, _policy())
