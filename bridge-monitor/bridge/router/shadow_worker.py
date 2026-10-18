"""Single-attempt, undelivered execution of reviewed parent-router shadow jobs."""

import hashlib
import json
import uuid
from datetime import datetime, timezone

import psycopg
from psycopg.types.json import Jsonb

from bridge.router.cases import load_case
from bridge.router.eligibility import build_local_prompt
from bridge.runtime.completions import record_completion
from bridge.runtime.ownership import (abort_unlaunched, claim_dispatch, claim_launch,
                                      mark_execution_uncertain)
from contracts.content import ContentObject
from contracts.events import EventType
from contracts.outcomes import CompletionEnvelope
from contracts.policy import PolicySnapshot
from contracts.work import ExecutionBinding


def _id(kind: str, call_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"bridge-monitor:shadow:{kind}:{call_id}"))


def _strict_source(raw: str) -> str:
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    parsed = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(parsed, dict) or set(parsed) != {"answer"}:
        raise ValueError("invalid answer wrapper")
    answer = parsed["answer"]
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("answer must contain complete source")
    return answer


def _shadow_manifest(dsn: str, call_id: str):
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT gate_facts,policy_version FROM delegation_decisions "
            "WHERE child_call_id=%s AND decision_stage='parent_router' "
            "AND execution_mode='shadow' AND route='local' "
            "ORDER BY decided_at DESC,decision_id DESC LIMIT 1", (call_id,)).fetchone()


def _pinned_policy(dsn: str, version: str) -> PolicySnapshot:
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT policy FROM config_versions WHERE version=%s",
                           (version,)).fetchone()
    if not row:
        raise ValueError("pinned_policy_missing")
    return PolicySnapshot.model_validate(row[0])


def _record_evaluation(dsn: str, result_id: str, call_id: str, *, source: str | None,
                       state: str, reason: str, verifier_result, manifest: dict,
                       verifier_image: str) -> None:
    evidence = {"state": state, "reason": reason, "source": "independent_shadow_verifier",
                "verifier_image": verifier_image, "verifier_ref": manifest.get("verifier_ref"),
                "policy_sha256": manifest.get("policy_sha256"),
                "model_profile": manifest.get("model_profile"),
                "model_digest": manifest.get("model_digest"),
                "prompt_version": manifest.get("prompt_version"),
                "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest()
                    if source is not None else None,
                "elapsed_seconds": verifier_result.elapsed_seconds if verifier_result else None,
                "exit_code": verifier_result.exit_code if verifier_result else None,
                "recorded_at_utc": datetime.now(timezone.utc).isoformat()}
    with psycopg.connect(dsn) as conn, conn.transaction():
        if source is not None:
            content_id = _id("parsed-content", call_id)
            content = ContentObject.from_bytes(content_id, source.encode("utf-8"),
                role="assistant", direction="in", content_type="text/x-python",
                origin="observed", capture_layer="bridge_router_verifier",
                completeness="complete")
            conn.execute("INSERT INTO content_objects(content_id,raw_bytes,byte_length,sha256,"
                "role,direction,content_type,encoding,origin,capture_layer,completeness,"
                "message_id,block_order) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,0) "
                "ON CONFLICT(content_id) DO NOTHING",
                (content.content_id, content.raw_bytes, content.byte_length, content.sha256,
                 content.role, content.direction, content.content_type, content.encoding,
                 content.origin, content.capture_layer, content.completeness))
            conn.execute("INSERT INTO result_parts(part_id,result_id,content_id) "
                "VALUES(%s,%s,%s) ON CONFLICT(part_id) DO NOTHING",
                (_id("parsed-part", call_id), result_id, content_id))
        conn.execute("INSERT INTO evaluations(evaluation_id,result_id,evidence,recorded_at) "
            "VALUES(%s,%s,%s,now()) ON CONFLICT(evaluation_id) DO NOTHING",
            (_id("evaluation", call_id), result_id, Jsonb(evidence)))


def run_one_shadow(coordinator, verifier) -> dict | None:
    """Claim at most one queued shadow job; never retry a claimed remote send."""
    coordinator._refresh_policy()
    if coordinator.policy.router.mode != "shadow":
        return None
    lease = claim_dispatch(coordinator.dsn, pool="qwen",
        incarnation_id=coordinator.incarnations["qwen"], execution_mode="shadow")
    if lease is None:
        return None
    call_id = lease.call_id
    try:
        request, prompt, pool, work_id, model, cutoff, _, _ = coordinator._load_job(call_id)
        manifest_row = _shadow_manifest(coordinator.dsn, call_id)
        if not manifest_row:
            raise ValueError("shadow_decision_missing")
        manifest, pinned_version = manifest_row
        pinned = _pinned_policy(coordinator.dsn, lease.policy_version)
        ref = manifest.get("verifier_ref")
        if not ref or ref in coordinator.policy.router.withdrawn_refs:
            raise ValueError("verifier_withdrawn")
        case = load_case(coordinator.project_root, ref)
        router = coordinator.policy.router
        canonical_tests = json.dumps(case.tests, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")
        pinned_policy_hash = hashlib.sha256(json.dumps(
            pinned.router.model_dump(mode="json"), sort_keys=True,
            separators=(",", ":")).encode("utf-8")).hexdigest()
        if (pinned_version != lease.policy_version or pool != "qwen" or
                model != coordinator.policy.deployment.qwen_model_profile or
                manifest.get("model_digest") != coordinator.policy.deployment.qwen_model_digest or
                manifest.get("model_profile") != model or
                router.verifier is None or
                pinned.router.verifier is None or
                pinned.router.verifier.image != router.verifier.image or
                getattr(getattr(verifier, "settings", None), "image", router.verifier.image)
                    != router.verifier.image or
                manifest.get("policy_sha256") != pinned_policy_hash or
                manifest.get("task_sha256") != hashlib.sha256(
                    case.task_text.encode("utf-8")).hexdigest() or
                manifest.get("baseline_sha256") != hashlib.sha256(
                    case.baseline_source.encode("utf-8")).hexdigest() or
                manifest.get("tests_sha256") != hashlib.sha256(canonical_tests).hexdigest() or
                prompt != build_local_prompt(case, pinned.router) or
                case.task_id != request.task.task_id):
            raise ValueError("shadow_manifest_changed")
        if not coordinator.emitter.may_admit_new_work(
                coordinator.policy.deployment.degraded_admission):
            raise ValueError("capture_degraded")
        adapter = coordinator.qwen_adapter
        if adapter is None:
            raise ValueError("agent_unavailable")
        preflight = adapter.preflight()
        if not preflight.ready or preflight.observed_digest != manifest.get("model_digest"):
            raise ValueError("agent_attestation_failed")
    except (OSError, ValueError, KeyError, AttributeError) as exc:
        abort_unlaunched(coordinator.dsn, lease)
        coordinator.emitter.emit(EventType.ADMISSION_BLOCKED, call_id=call_id,
            payload={"reason": str(exc), "execution_mode": "shadow"},
            config_version=lease.policy_version)
        return {"state": "cancelled", "call_id": call_id, "reason": str(exc)}
    if not claim_launch(coordinator.dsn, lease):
        return {"state": "uncertain", "call_id": call_id,
                "reason": "launch_already_claimed"}
    common = {"call_id": call_id, "task_id": request.task.task_id,
              "work_item_id": work_id, "config_version": lease.policy_version,
              "worker_instance_id": coordinator.workers["qwen"]}
    attempt_id = f"{call_id}:attempt:1"
    dispatched = coordinator.emitter.emit(EventType.CALL_DISPATCHED, requested_model=model,
        payload={"destination": "skynet", "execution_mode": "shadow"}, **common)
    started = coordinator.emitter.emit(EventType.ATTEMPT_STARTED, attempt_id=attempt_id,
        payload={"attempt_number": 1, "execution_mode": "shadow"}, **common)
    if not dispatched or not started:
        mark_execution_uncertain(coordinator.dsn, call_id, reason="capture_gap_before_send")
        return {"state": "uncertain", "call_id": call_id,
                "reason": "capture_gap_before_send"}
    try:
        result = adapter.run(lease, prompt, coordinator.emitter,
                             task=request.task, deadline_at=cutoff)
    except Exception as exc:
        mark_execution_uncertain(coordinator.dsn, call_id,
                                 reason=f"agent_exception:{type(exc).__name__}")
        return {"state": "uncertain", "call_id": call_id,
                "reason": "agent_exception"}
    if result.state != "completed" or result.answer is None:
        coordinator.emitter.emit(EventType.ATTEMPT_UNCERTAIN, attempt_id=attempt_id,
            payload={"reason": result.uncertainty or result.state,
                     "execution_mode": "shadow"}, **common)
        mark_execution_uncertain(coordinator.dsn, call_id,
                                 reason=result.uncertainty or result.state)
        return {"state": "uncertain", "call_id": call_id,
                "reason": result.uncertainty or result.state}
    raw = result.raw_answer if result.raw_answer is not None else result.answer
    result_id = _id("result", call_id)
    content_id = _id("raw-content", call_id)
    envelope = CompletionEnvelope(call_id=call_id, attempt_id=attempt_id,
        result={"result_id": result_id, "content_id": content_id, "delivered": False})
    binding = ExecutionBinding(worker_instance_id=coordinator.workers["qwen"],
        producer_id=coordinator.emitter.producer_id,
        producer_incarnation=coordinator.incarnations["qwen"],
        call_id=call_id, attempt_id=attempt_id)
    record_completion(coordinator.dsn, envelope, binding, answer_bytes=raw.encode("utf-8"))
    source = None
    verification = None
    try:
        source = _strict_source(raw)
        if len(source.encode("utf-8")) > router.max_source_bytes:
            raise ValueError("output_too_large")
        verification = verifier.verify(source, case.tests)
        state, reason = verification.state, verification.reason
    except ValueError as exc:
        state, reason = "fail", ("output_too_large" if str(exc) == "output_too_large"
                                  else "output_format_invalid")
        source = None
    except Exception as exc:
        state, reason = "uncertain", f"verifier_unavailable:{type(exc).__name__}"
    _record_evaluation(coordinator.dsn, result_id, call_id, source=source,
                       state=state, reason=reason, verifier_result=verification,
                       manifest=manifest, verifier_image=router.verifier.image)
    coordinator.emitter.emit(EventType.CALL_COMPLETED, attempt_id=attempt_id,
        payload={"result_id": result_id, "execution_mode": "shadow"}, **common)
    return {"state": "completed", "call_id": call_id, "result_id": result_id,
            "evaluation_state": state, "reason": reason, "delivered": False}
