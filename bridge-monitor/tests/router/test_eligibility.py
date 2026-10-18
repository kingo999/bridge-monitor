import hashlib
from datetime import datetime, timedelta, timezone

from bridge.router.eligibility import build_local_prompt, decide_parent_route
from contracts.router import CaptureFacts, RouterCase, RouterPolicy
from contracts.work import TaskPacket


def _case():
    now = datetime.now(timezone.utc)
    return RouterCase(origin_kind="natural", origin_ref="codex-task:case-1",
        task_id="case-1", task_text="Fix square for n=2", task_arrived_at=now - timedelta(minutes=2),
        captured_at=now - timedelta(minutes=1), baseline_source="def square(n):\n    return n + 1\n",
        tests=[{"function": "square", "args": [2], "expected": 4}],
        output_contract="complete_python_module_v1")


def _policy():
    return RouterPolicy.model_validate({"mode": "shadow", "verifier": {
        "image": "python@sha256:" + "a" * 64,
        "daemon_endpoint": "npipe:////./pipe/dockerDesktopLinuxEngine"}})


def _task():
    return TaskPacket(task_id="case-1", work_item_id="work-1",
        category="single_file_python_fix", scope=["analysis"],
        token_budget=5000, call_budget=2)


def _facts(case):
    return CaptureFacts(case=case, case_digest_valid=True, baseline_failed=True,
        capture_healthy=True, agent_ready=True, model_profile="qwen2.5-coder:14b-instruct-q4_K_M",
        model_digest="b" * 64, remaining_calls=2, remaining_tokens=5000,
        context_tokens=8192, queue_slots=1, verifier_active=True)


def test_eligible_case_is_pinned_and_prompt_hides_expected_outputs():
    case = _case()
    decision = decide_parent_route(_task(), case.task_text, case.output_contract,
        "c" * 64, _policy(), _facts(case))
    assert decision.eligible is True
    assert decision.reason == "shadow_candidate"
    assert decision.manifest["baseline_sha256"] == hashlib.sha256(case.baseline_source.encode()).hexdigest()
    prompt = build_local_prompt(case, _policy())
    assert case.baseline_source in prompt
    assert "expected" not in prompt and "\"tests\"" not in prompt
    assert "4" not in prompt


def test_missing_or_uncertain_facts_fail_closed_to_paid():
    case = _case()
    scenarios = [
        (None, _facts(case), "verifier_missing"),
        ("c" * 64, _facts(case).model_copy(update={"capture_healthy": False}), "capture_degraded"),
        ("c" * 64, _facts(case).model_copy(update={"baseline_failed": None}), "baseline_unverified"),
        ("c" * 64, _facts(case).model_copy(update={"remaining_calls": 1}), "shadow_budget_exhausted"),
        ("c" * 64, _facts(case).model_copy(update={"verifier_active": False}), "verifier_withdrawn"),
        ("c" * 64, _facts(case).model_copy(update={"agent_ready": False}), "agent_unavailable"),
    ]
    for verifier_ref, facts, reason in scenarios:
        decision = decide_parent_route(_task(), case.task_text, case.output_contract,
            verifier_ref, _policy(), facts)
        assert decision.eligible is False
        assert decision.reason == reason
    assert decide_parent_route(_task().model_copy(update={"category": "architecture"}),
        case.task_text, case.output_contract, "c" * 64, _policy(), _facts(case)).eligible is False
    assert decide_parent_route(_task(), "different prompt", case.output_contract,
        "c" * 64, _policy(), _facts(case)).reason == "task_snapshot_mismatch"
