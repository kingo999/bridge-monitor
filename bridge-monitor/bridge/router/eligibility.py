"""Pure, deterministic and fail-closed Qwen shadow eligibility."""

import hashlib
import json

from contracts.router import CaptureFacts, RouterCase, RouterDecision, RouterPolicy
from contracts.work import TaskPacket


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def build_local_prompt(case: RouterCase, policy: RouterPolicy) -> str:
    """Never include host-held tests, expected outputs, or the paid answer."""
    return (policy.prompt_prefix + "Task: " + case.task_text +
            "\nCurrent file:\n" + case.baseline_source)


def decide_parent_route(task: TaskPacket, prompt: str, output_contract: str,
                        verifier_ref: str | None, policy: RouterPolicy,
                        capture: CaptureFacts) -> RouterDecision:
    case = capture.case
    manifest = {"rule_version": "parent-router-v1", "prompt_version": policy.prompt_version,
                "output_contract": output_contract, "verifier_ref": verifier_ref,
                "model_profile": capture.model_profile, "model_digest": capture.model_digest,
                "task_sha256": _sha(prompt.encode("utf-8")),
                "policy_sha256": _sha(json.dumps(policy.model_dump(mode="json"), sort_keys=True,
                    separators=(",", ":")).encode("utf-8"))}
    if case:
        manifest.update(baseline_sha256=_sha(case.baseline_source.encode("utf-8")),
                        tests_sha256=_sha(json.dumps(case.tests, sort_keys=True,
                            separators=(",", ":")).encode("utf-8")),
                        case_captured_at=case.captured_at.isoformat())

    reason = None
    if policy.mode != "shadow":
        reason = "router_disabled"
    elif not verifier_ref:
        reason = "verifier_missing"
    elif not capture.capture_healthy:
        reason = "capture_degraded"
    elif task.category not in policy.categories or case is None:
        reason = "unsupported_category"
    elif output_contract != policy.output_contract or case.output_contract != output_contract:
        reason = "unsupported_output_contract"
    elif not set(task.scope).issubset(set(policy.scopes)):
        reason = "scope_unapproved"
    elif case.task_id != task.task_id or case.task_text != prompt:
        reason = "task_snapshot_mismatch"
    elif not capture.case_digest_valid:
        reason = "case_digest_invalid"
    elif capture.baseline_failed is not True:
        reason = "baseline_unverified"
    elif not capture.verifier_active or verifier_ref in policy.withdrawn_refs:
        reason = "verifier_withdrawn"
    elif not capture.agent_ready or not capture.model_profile or not capture.model_digest:
        reason = "agent_unavailable"
    elif capture.remaining_calls is None or capture.remaining_calls < 2 or task.call_budget < 2:
        reason = "shadow_budget_exhausted"
    elif capture.remaining_tokens is None or capture.remaining_tokens < policy.shadow_reserve_tokens:
        reason = "shadow_budget_exhausted"
    elif capture.queue_slots is None or capture.queue_slots < 1:
        reason = "shadow_queue_full"
    elif capture.context_tokens is None or capture.context_tokens < policy.max_context_tokens:
        reason = "context_unverified"
    elif len(build_local_prompt(case, policy).encode("utf-8")) > policy.max_prompt_bytes:
        reason = "prompt_too_large"
    elif len(case.baseline_source.encode("utf-8")) > policy.max_source_bytes:
        reason = "source_too_large"
    elif (int(_sha(task.work_item_id.encode("utf-8"))[:8], 16) / 0xFFFFFFFF * 100
          >= policy.sample_percent):
        reason = "outside_shadow_sample"
    return RouterDecision(eligible=reason is None,
                          reason=reason or "shadow_candidate", manifest=manifest)
