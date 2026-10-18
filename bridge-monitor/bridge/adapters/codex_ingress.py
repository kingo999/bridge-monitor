"""Trusted GPT/Codex ingress: captures exact request before admission."""

import uuid
from typing import Literal

import psycopg

from contracts.events import EventType
from contracts.work import AdmissionDecision, CallRequest
from bridge.runtime.admission import admit_call
from bridge.runtime.policy_startup import active_policy


def runtime_status(dsn: str) -> dict:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute("SELECT state,slot_held,count(*) FROM runtime_jobs GROUP BY state,slot_held").fetchall()
    return {"active": sum(n for state, held, n in rows if state == "active" and held),
            "queued": sum(n for state, held, n in rows if state == "queued"),
            "held_uncertain": sum(n for state, held, n in rows if state in ("uncertain", "fallback") and held),
            "blocked": 0}


class CodexIngress:
    def __init__(self, dsn, emitter, policy, policy_version: str):
        self.dsn = dsn
        self.emitter = emitter
        self.policy = policy
        self.policy_version = policy_version

    def refresh_policy(self):
        current = active_policy(self.dsn)
        if current is None:
            raise ValueError("reviewed policy has not been activated")
        self.policy = current
        self.policy_version = current.version
        return current

    def submit(self, request: CallRequest, *, prompt: str, pool: str,
               reserve_tokens: int, local_cutoff_at=None,
               source_snapshot: str | None = None, source_mode: str | None = None,
               effective_prompt: str | None = None,
               guidance_manifest: dict | None = None, gate_facts: dict | None = None,
               guidance_decision_id: str | None = None,
               expected_policy_version: str | None = None,
               execution_mode: Literal["production", "shadow"] = "production",
               captured_content_id: str | None = None) -> AdmissionDecision:
        self.refresh_policy()
        route = "paid" if pool == "claude" else "local"
        task = request.task

        def rejected(reason):
            return AdmissionDecision(work_item_id=task.work_item_id, route=route,
                                     state="rejected", reason=reason,
                                     policy_version=self.policy_version)

        if not self.emitter.may_admit_new_work(self.policy.deployment.degraded_admission):
            return rejected("capture_degraded")
        content_id = captured_content_id or self._capture_request(
            request, prompt, effective_prompt=effective_prompt,
            ensure_caller=pool == "claude", execution_mode=execution_mode)
        if captured_content_id:
            with psycopg.connect(self.dsn) as conn:
                matched = conn.execute("SELECT 1 FROM messages m "
                    "JOIN content_objects c ON c.content_id=m.original_content_id "
                    "WHERE m.call_id=%s AND m.original_content_id=%s AND c.raw_bytes=%s LIMIT 1",
                    (request.call_id, captured_content_id, prompt.encode("utf-8"))).fetchone()
            if not matched:
                return rejected("captured_prompt_mismatch")
        if not content_id or not self.emitter.may_admit_new_work(self.policy.deployment.degraded_admission):
            return rejected("capture_degraded")
        decision = admit_call(self.dsn, request, self.policy, policy_version=self.policy_version,
                              pool=pool, reserve_tokens=reserve_tokens,
                              local_cutoff_at=local_cutoff_at,
                              source_snapshot=source_snapshot, source_mode=source_mode,
                              prompt_content_id=content_id,
                              expected_active_version=expected_policy_version or self.policy_version,
                              guidance_manifest=guidance_manifest, gate_facts=gate_facts,
                              guidance_decision_id=guidance_decision_id,
                              execution_mode=execution_mode)
        common = {"task_id": task.task_id, "work_item_id": task.work_item_id,
                  "config_version": decision.policy_version}
        health = self.emitter.health()
        payload = {**runtime_status(self.dsn), "capture_state": health["state"],
                   "known_gaps": health["known_gaps"],
                   "spool_pressure": health["spool_pressure"],
                   "execution_mode": execution_mode}
        if decision.state == "queued":
            self.emitter.emit(EventType.CALL_QUEUED, call_id=request.call_id,
                              requested_model=request.requested_model, payload=payload, **common)
        else:
            self.emitter.emit(EventType.ADMISSION_BLOCKED, call_id=request.call_id,
                              requested_model=request.requested_model,
                              payload={**payload, "reason": decision.reason}, **common)
        return decision

    def _capture_request(self, request: CallRequest, prompt: str,
                         *, effective_prompt: str | None = None,
                         ensure_caller: bool = False,
                         execution_mode: Literal["production", "shadow"] = "production") -> str | None:
        task = request.task
        common = {"task_id": task.task_id, "work_item_id": task.work_item_id,
                  "config_version": self.policy_version}
        self.emitter.emit(EventType.TASK_CREATED, task_id=task.task_id,
                          payload={"scope": task.scope}, config_version=self.policy_version)
        self.emitter.emit(EventType.WORK_ITEM_CREATED,
                          payload={"category": task.category}, **common)
        if ensure_caller and request.parent_call_id and request.parent_call_id == f"gpt:{task.task_id}":
            if not self.emitter.emit(EventType.CALL_RECEIVED,
                call_id=request.parent_call_id, requested_model=None,
                payload={"parent_call_id": None, "origin": "codex_caller"}, **common):
                return None
        self.emitter.emit(EventType.CALL_RECEIVED, call_id=request.call_id,
                          requested_model=request.requested_model,
                          payload={"parent_call_id": request.parent_call_id,
                                   "parent_attempt_id": request.parent_attempt_id,
                                   "execution_mode": execution_mode}, **common)
        effective_id = None
        if effective_prompt is not None and effective_prompt != prompt:
            effective_id = str(uuid.uuid4())
            if not self.emitter.emit(EventType.PROMPT_TRANSFORMED, call_id=request.call_id,
                body=effective_prompt.encode("utf-8"), content_id=effective_id,
                role="user", direction="out", payload={"transformation": "registered_guidance"},
                **common):
                return None
        content_id = str(uuid.uuid4())
        stored = self.emitter.emit(EventType.MESSAGE_SENT, call_id=request.call_id,
                                   body=prompt.encode("utf-8"), content_id=content_id,
                                   role="user", direction="in",
                                   payload={"effective_content_id": effective_id} if effective_id else None,
                                   **common)
        return (effective_id or content_id) if stored else None

    def record_paid_route(self, request: CallRequest, *, prompt: str, reason: str,
                          manifest: dict, gate_facts: dict) -> bool:
        if not self.emitter.may_admit_new_work(self.policy.deployment.degraded_admission):
            return False
        if not self._capture_request(request, prompt):
            return False
        return self.record_paid_decision(request, reason=reason,
                                         manifest=manifest, gate_facts=gate_facts)

    def record_paid_decision(self, request: CallRequest, *, reason: str,
                             manifest: dict, gate_facts: dict) -> bool:
        if not self.emitter.may_admit_new_work(self.policy.deployment.degraded_admission):
            return False
        stored = self.emitter.emit(EventType.DELEGATION_DECIDED,
            call_id=request.call_id, task_id=request.task.task_id,
            work_item_id=request.task.work_item_id, config_version=self.policy_version,
            payload={"decision_id": str(uuid.uuid5(uuid.NAMESPACE_URL,
                     f"bridge-monitor:{request.call_id}:paid-direct")), "route": "paid",
                     "eligibility": "ineligible", "reason": reason,
                     "guidance_manifest": manifest, "gate_facts": gate_facts})
        return stored and self.emitter.may_admit_new_work(self.policy.deployment.degraded_admission)
