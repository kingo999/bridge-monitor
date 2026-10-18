"""New project-local bridge operations and restricted MCP entry point."""

import argparse
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import psycopg

from contracts.events import EventType
from contracts.outcomes import (CompletionEnvelope, ContributionLink, DispositionRecord,
                                ParentAcknowledgement)
from contracts.work import CallRequest, ExecutionBinding, TaskPacket
from bridge.adapters.codex_ingress import CodexIngress, runtime_status
from bridge.runtime.completions import record_completion
from bridge.runtime.parent_acknowledgements import record_parent_acknowledgement
from bridge.runtime.ownership import (claim_dispatch, claim_launch,
                                      mark_execution_uncertain, register_incarnation,
                                      abort_unlaunched)
from bridge.runtime.workspaces import (fingerprint_source, materialize_source,
                                       release_source, isolated_workspace_target)
from bridge.guidance.gates import check_readiness
from bridge.guidance.selection import resolve_guidance
from bridge.runtime.fallback import commit_fallback
from bridge.runtime.scheduler import cancel_queued
from bridge.runtime.scheduler import request_cancel_tree
from bridge.runtime.recovery import reconcile_owner_and_recover


class BridgeCoordinator:
    def __init__(self, dsn, policy, policy_version, emitter, *, claude_adapter,
                 qwen_adapter=None, parent_call_id: str | None = None,
                 parent_lease_id: str | None = None,
                 source_roots: dict[str, str] | None = None,
                 workspace_base: str | Path | None = None,
                 workspace_max_bytes: int = 104857600,
                 local_capability: dict | None = None, paid_capability: dict | None = None):
        self.dsn = dsn
        self.policy = policy
        self.policy_version = policy_version
        self.emitter = emitter
        self.claude_adapter = claude_adapter
        self.qwen_adapter = qwen_adapter
        self.parent_call_id = parent_call_id
        self.parent_lease_id = parent_lease_id
        self.local_capability = local_capability or {}
        self.paid_capability = paid_capability or {}
        self.source_roots = source_roots or {}
        self.workspace_base = Path(workspace_base) if workspace_base else None
        self.workspace_max_bytes = workspace_max_bytes
        self.ingress = CodexIngress(dsn, emitter, policy, policy_version)
        self.incarnations = {pool: str(uuid.uuid4()) for pool in ("claude", "qwen")}
        self.workers = {pool: f"bridge-{pool}-{uuid.uuid4()}" for pool in ("claude", "qwen")}
        self.owned_calls = set()
        self._recovery_threads = {}
        self._recovery_threads_lock = threading.Lock()
        for pool in ("claude", "qwen"):
            register_incarnation(dsn, self.incarnations[pool], self.workers[pool])

    def _refresh_policy(self):
        self.policy = self.ingress.refresh_policy()
        self.policy_version = self.policy.version

    def delegate_claude(self, task: TaskPacket, prompt: str, *, requested_model: str,
                        reserve_tokens: int, source_id: str | None = None) -> dict:
        self._refresh_policy()
        if requested_model != self.policy.fallback.paid_model_profile:
            return {"state": "rejected", "reason": "unvalidated_paid_model_choice"}
        if task.context.get("source_id") and source_id != task.context["source_id"]:
            return {"state": "rejected", "reason": "source_binding_required"}
        source_snapshot = None
        if source_id:
            if source_id not in self.source_roots or "source-read" not in task.scope:
                return {"state": "rejected", "reason": "source_scope_not_approved"}
            if self.workspace_base is None:
                return {"state": "rejected", "reason": "source_workspace_unavailable"}
            source_snapshot = fingerprint_source(self.source_roots[source_id],
                max_bytes=self.workspace_max_bytes)
            task = task.model_copy(update={"context": {**task.context, "source_id": source_id}})
        call_id = str(uuid.uuid4())
        request = CallRequest(call_id=call_id, task=task, requested_model=requested_model,
                              parent_call_id=f"gpt:{task.task_id}")
        decision = self.ingress.submit(request, prompt=prompt, pool="claude",
                                       reserve_tokens=reserve_tokens,
                                       source_snapshot=source_snapshot,
                                       source_mode="read" if source_id else None)
        if decision.state != "queued":
            return {"state": decision.state, "reason": decision.reason, "call_id": call_id}
        self.owned_calls.add(call_id)
        poll_seconds = self.policy.deployment.queue_poll_ms / 1000
        with psycopg.connect(self.dsn) as conn:
            queue_deadline = conn.execute("SELECT queue_deadline_at FROM runtime_jobs "
                                          "WHERE call_id=%s", (call_id,)).fetchone()[0]
        while datetime.now(timezone.utc) < queue_deadline:
            outcome = self.run_one("claude", call_id=call_id)
            if outcome:
                return outcome
            time.sleep(min(poll_seconds,
                max(0, (queue_deadline - datetime.now(timezone.utc)).total_seconds())))
        if cancel_queued(self.dsn, call_id):
            return {"state": "queue_expired", "call_id": call_id,
                    "reason": "paid_queue_deadline_exceeded"}
        return self.get_execution_status(call_id)

    def delegate_local_analysis(self, prompt: str) -> dict:
        self._refresh_policy()
        if not self.parent_call_id or not self.parent_lease_id:
            raise PermissionError("trusted parent execution binding required")
        with psycopg.connect(self.dsn) as conn:
            parent = conn.execute("SELECT request,state,slot_held,lease_id,reserved_tokens,"
                                  "execution_deadline_at,admitted_at FROM runtime_jobs WHERE call_id=%s "
                                  "AND pool='claude'", (self.parent_call_id,)).fetchone()
            observed = conn.execute("SELECT m.reported_model,m.model_execution_id FROM model_executions m "
                                    "JOIN attempts a ON a.attempt_id=m.attempt_id WHERE a.call_id=%s "
                                    "AND m.reported_model IS NOT NULL ORDER BY a.attempt_number DESC LIMIT 1",
                                    (self.parent_call_id,)).fetchone()
        if not parent or parent[1] != "active" or not parent[2] or parent[3] != self.parent_lease_id:
            raise PermissionError("parent execution is not the live owner")
        parent_request = CallRequest.model_validate(parent[0])
        task = parent_request.task
        child_id = str(uuid.uuid4())
        local_model = self.policy.deployment.qwen_model_profile
        child = CallRequest(call_id=child_id, task=task, requested_model=local_model,
                            parent_call_id=self.parent_call_id,
                            parent_attempt_id=f"{self.parent_call_id}:attempt:1")
        now = datetime.now(timezone.utc)
        required = {key: task.context.get(key, "") for key in task.required_context_keys}
        guidance = resolve_guidance(self.dsn, self.policy, task, prompt)
        effective_prompt = guidance.effective_prompt
        local_ctx = self.local_capability.get("context_tokens", 0)
        paid_ctx = self.paid_capability.get("context_tokens", 0)
        local_output = self.policy.deployment.qwen_output_tokens
        paid_output = self.policy.deployment.fallback_allowance_tokens
        byte_bound = (len(effective_prompt.encode("utf-8")) +
                      self.local_capability.get("template_overhead_tokens", 128)
                      if self.local_capability.get("byte_upper_bound_validated") is True else None)
        compatible = self.paid_capability.get("reported_models", [])
        if (not self.local_capability.get("client_timeout_verified") or
                not self.paid_capability.get("client_timeout_verified")):
            compatible = []
        readiness = check_readiness(task=task, prompt=effective_prompt,
            classification_source="ingress_verified", parent_scope=task.scope,
            required_context=required, measured_input_tokens=byte_bound,
            local_context_tokens=local_ctx, local_reserved_output_tokens=local_output,
            paid_context_tokens=paid_ctx, paid_reserved_output_tokens=paid_output,
            observed_owner_model=observed[0] if observed else None,
            compatible_owner_models=compatible, now=now,
            parent_deadline_at=parent[5] or now,
            client_deadline_at=parent[6] + timedelta(
                seconds=self.policy.deployment.client_timeout_seconds),
            fallback_seconds=self.policy.deployment.fallback_allowance_seconds,
            delivery_margin_seconds=self.policy.deployment.delivery_margin_seconds)
        guidance_facts = {**readiness.gate_facts,
                          "guidance_gate": "pass" if guidance.state == "ready" else "fail",
                          "guidance_reason": guidance.reason,
                          "guidance_bytes": guidance.manifest.get("byte_length", 0)}
        readiness = readiness.__class__(
            "paid" if guidance.state == "blocked" else readiness.route,
            guidance.reason if guidance.state == "blocked" else readiness.reason,
            guidance.manifest, guidance_facts,
            None if guidance.state == "blocked" else readiness.local_cutoff_at)
        if (not local_model or "REQUIRED" in local_model.upper() or self.qwen_adapter is None
                or parent[4] < paid_output):
            readiness = readiness.__class__("paid", "local_profile_or_fallback_allowance_unavailable",
                                            readiness.manifest, readiness.gate_facts, None)
        elif readiness.route == "local":
            expected_digest = self.policy.deployment.qwen_model_digest
            if (getattr(self.qwen_adapter, "model", None) != local_model or
                    getattr(self.qwen_adapter, "expected_digest", None) != expected_digest or
                    not callable(getattr(self.qwen_adapter, "preflight", None))):
                readiness = readiness.__class__("paid", "local_adapter_policy_mismatch",
                    readiness.manifest, readiness.gate_facts, None)
            else:
                profile = self.qwen_adapter.preflight()
                facts = {**readiness.gate_facts,
                         "expected_model_digest": expected_digest,
                         "observed_model_digest": profile.observed_digest}
                readiness = readiness.__class__(readiness.route if profile.ready else "paid",
                    readiness.reason if profile.ready else profile.reason,
                    readiness.manifest, facts,
                    readiness.local_cutoff_at if profile.ready else None)
        if readiness.route != "local":
            captured = self.ingress.record_paid_route(child, prompt=prompt, reason=readiness.reason,
                manifest=readiness.manifest, gate_facts=readiness.gate_facts)
            return {"state": "paid_continuation_required" if captured else "capture_degraded",
                    "child_call_id": child_id, "reason": readiness.reason,
                    "route": "paid", "not_routed": True}
        guidance_decision_id = str(uuid.uuid4())
        decision = self.ingress.submit(child, prompt=prompt, pool="qwen",
            reserve_tokens=byte_bound + local_output,
            local_cutoff_at=readiness.local_cutoff_at,
            effective_prompt=effective_prompt, guidance_manifest=readiness.manifest,
            gate_facts=readiness.gate_facts, guidance_decision_id=guidance_decision_id,
            expected_policy_version=self.policy_version)
        if decision.state != "queued":
            if decision.reason == "capture_degraded":
                return {"state": "capture_degraded", "child_call_id": child_id,
                        "reason": decision.reason}
            captured = self.ingress.record_paid_decision(child, reason=decision.reason,
                manifest=readiness.manifest,
                gate_facts={**readiness.gate_facts, "admission_state": decision.state})
            return {"state": "paid_continuation_required" if captured else "capture_degraded",
                    "child_call_id": child_id, "reason": decision.reason,
                    "route": "paid", "not_routed": True}
        self.emitter.emit(EventType.DELEGATION_DECIDED, call_id=child_id,
            task_id=task.task_id, work_item_id=task.work_item_id,
            config_version=self.policy_version,
            payload={"decision_id": guidance_decision_id, "route": "local",
                     "eligibility": "eligible", "reason": "baseline_ready",
                     "guidance_manifest": readiness.manifest,
                     "gate_facts": readiness.gate_facts})
        cutoff = readiness.local_cutoff_at
        with psycopg.connect(self.dsn) as conn:
            queue_deadline = conn.execute("SELECT queue_deadline_at FROM runtime_jobs "
                                          "WHERE call_id=%s", (child_id,)).fetchone()[0]
        until = min(cutoff, queue_deadline)
        outcome = None
        while datetime.now(timezone.utc) < until:
            outcome = self.run_one("qwen")
            if outcome and outcome.get("call_id") == child_id:
                break
            if outcome is None:
                time.sleep(0.2)
        if outcome and outcome.get("call_id") == child_id and outcome["state"] == "paid_continuation_required":
            return outcome
        if outcome and outcome.get("call_id") == child_id and outcome["state"] == "completed":
            if not outcome.get("delivered", True):
                return {**outcome, "state": "late_only", "route": "paid",
                        "reason": "local_result_not_delivered"}
            with psycopg.connect(self.dsn) as conn:
                row = conn.execute("SELECT c.sha256 FROM results r JOIN content_objects c "
                                   "ON c.content_id=r.delivered_content_id WHERE r.result_id=%s",
                                   (outcome["result_id"],)).fetchone()
            return {**outcome, "content_sha256": row[0],
                    "input_sha256": row[0], "route": "local"}
        stage = "queue" if not outcome or outcome.get("call_id") != child_id else "execution"
        if stage == "queue":
            threshold = queue_deadline
        else:
            with psycopg.connect(self.dsn) as conn:
                execution_deadline = conn.execute(
                    "SELECT execution_deadline_at FROM runtime_jobs WHERE call_id=%s",
                    (child_id,)).fetchone()[0]
            threshold = min(cutoff, execution_deadline or cutoff)
        transition = commit_fallback(self.dsn, child_id, timeout_stage=stage,
            reason="local_deadline_or_uncertain_result", threshold_at=threshold,
            elapsed_ms=max(0, int((datetime.now(timezone.utc) - now).total_seconds() * 1000)),
            recipient_call_id=self.parent_call_id,
            owner_execution_id=observed[1] if observed else None,
            selected_model=observed[0] if observed else None)
        return {"state": "paid_continuation_required", "child_call_id": child_id,
                "fallback_state": transition.state, "route": "paid",
                "not_routed": stage == "queue", "reason": transition.reason}

    def _load_job(self, call_id: str):
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute("SELECT j.request,c.raw_bytes,j.pool,j.work_item_id,"
                               "j.requested_model,j.local_cutoff_at,j.source_snapshot,j.source_mode "
                               "FROM runtime_jobs j LEFT JOIN content_objects c "
                               "ON c.content_id=j.prompt_content_id WHERE j.call_id=%s",
                               (call_id,)).fetchone()
        if not row or row[1] is None:
            raise ValueError("admitted execution has no exact captured prompt")
        return (CallRequest.model_validate(row[0]), bytes(row[1]).decode("utf-8"),
                row[2], row[3], row[4], row[5], row[6], row[7])

    def _source_for_call(self, call_id: str, source_id: str) -> Path:
        source = Path(self.source_roots[source_id])
        with psycopg.connect(self.dsn) as conn:
            prior = conn.execute("SELECT c.parent_call_id FROM runtime_recovery_links l "
                "JOIN calls c ON c.call_id=l.child_call_id "
                "WHERE l.recovery_call_id=%s", (call_id,)).fetchone()
        if prior and self.workspace_base is not None:
            preserved = isolated_workspace_target(self.workspace_base, prior[0], "source")
            if preserved.is_dir():
                return preserved
        return source

    def _claimed_completion(self, lease, attempt_id: str, result_id: str,
                            content_id: str, claim: dict | None,
                            raw_content_id: str | None = None) -> tuple[CompletionEnvelope, list[str]]:
        dispositions = []
        contributions = []
        diagnostics = []
        fallback_child = None
        work_resolution = None
        if isinstance(claim, dict):
            claimed_results = claim.get("local_results", [])
            if not isinstance(claimed_results, list):
                diagnostics.append("local_results must be an array")
                claimed_results = []
            for index, item in enumerate(claimed_results):
                if not isinstance(item, dict):
                    diagnostics.append(f"local_results[{index}] is not an object")
                    continue
                judgment = item.get("judgment")
                data = {"disposition_id": str(uuid.uuid4()),
                    "result_id": item.get("result_id"),
                    "actor_id": self.workers["claude"], "stage": "claude",
                    "judgment": judgment,
                    "input_sha256": item.get("input_sha256"),
                    "output_result_id": result_id if judgment != "unused" else None}
                for optional in ("reason", "reason_code", "substantive_change",
                                 "repair_effort", "failure_category", "evidence_refs",
                                 "guidance_ids", "correction_execution_ids", "part_id"):
                    if optional in item:
                        data[optional] = item[optional]
                try:
                    disposition = DispositionRecord.model_validate(data)
                except ValueError as exc:
                    diagnostics.append(f"local_results[{index}] invalid: {exc}")
                    continue
                dispositions.append(disposition)
                if judgment in ("accepted", "revised", "replaced"):
                    with psycopg.connect(self.dsn) as conn:
                        obligation = conn.execute("SELECT content_sha256 FROM "
                            "runtime_disposition_obligations WHERE result_id=%s "
                            "AND consumer_call_id=%s AND stage='claude' "
                            "AND part_id IS NOT DISTINCT FROM %s",
                            (disposition.result_id, lease.call_id,
                             disposition.part_id)).fetchone()
                    if obligation and obligation[0] == disposition.input_sha256:
                        relation = {"accepted": "accepted_contribution",
                                    "revised": "revised_contribution",
                                    "replaced": "replacement"}[judgment]
                        contributions.append(ContributionLink(
                            from_result_id=disposition.result_id,
                            to_result_id=result_id, relation=relation))
            if isinstance(claim.get("fallback_child_call_id"), str):
                fallback_child = claim["fallback_child_call_id"]
            elif claim.get("fallback_child_call_id") is not None:
                diagnostics.append("fallback_child_call_id must be a string or null")
            if claim.get("work_resolution") in ("resolved", "unresolved", "unknown"):
                work_resolution = claim["work_resolution"]
            elif claim.get("work_resolution") is not None:
                diagnostics.append("invalid work_resolution")
        envelope = CompletionEnvelope(call_id=lease.call_id, attempt_id=attempt_id,
            result={"result_id": result_id, "content_id": content_id,
                    "raw_content_id": raw_content_id, "delivered": True},
            dispositions=dispositions, contributions=contributions,
            fallback_child_call_id=fallback_child, work_resolution=work_resolution)
        return envelope, diagnostics

    def _commit_predispatch_fallback(self, lease, reason: str, *,
                                     confirmed_not_sent: bool = False) -> dict:
        with psycopg.connect(self.dsn) as conn:
            parent_id = conn.execute("SELECT parent_call_id FROM calls WHERE call_id=%s",
                                     (lease.call_id,)).fetchone()[0]
            observed = conn.execute("SELECT m.reported_model,m.model_execution_id "
                "FROM model_executions m JOIN attempts a ON a.attempt_id=m.attempt_id "
                "WHERE a.call_id=%s AND m.reported_model IS NOT NULL "
                "ORDER BY a.attempt_number DESC LIMIT 1", (parent_id,)).fetchone()
        transition = commit_fallback(self.dsn, lease.call_id,
            timeout_stage="predispatch", reason=reason,
            threshold_at=datetime.now(timezone.utc), elapsed_ms=0,
            recipient_call_id=parent_id,
            owner_execution_id=observed[1] if observed else None,
            selected_model=observed[0] if observed else None,
            confirmed_not_sent=confirmed_not_sent)
        return {"state": "paid_continuation_required", "call_id": lease.call_id,
                "child_call_id": lease.call_id, "fallback_state": transition.state,
                "route": "paid", "not_routed": True, "reason": reason}

    def run_one(self, pool: str, *, call_id: str | None = None) -> dict | None:
        lease = claim_dispatch(self.dsn, pool=pool,
                               incarnation_id=self.incarnations[pool], call_id=call_id)
        if not lease:
            return None
        request, prompt, job_pool, work_item_id, model, local_cutoff_at, source_snapshot, source_mode = self._load_job(lease.call_id)
        if job_pool != pool:
            raise ValueError("dispatch pool mismatch")
        task = request.task
        adapter = self.claude_adapter if pool == "claude" else self.qwen_adapter
        if pool == "qwen":
            reason = None
            if not self.emitter.may_admit_new_work(self.policy.deployment.degraded_admission):
                reason = "capture_degraded_before_launch"
            elif adapter is None:
                reason = "local_adapter_unavailable"
            else:
                profile = adapter.preflight()
                if not profile.ready:
                    reason = profile.reason
            if reason is None and local_cutoff_at <= datetime.now(timezone.utc):
                reason = "local_cutoff_passed_before_launch"
            if reason:
                return self._commit_predispatch_fallback(lease, reason)
        workspace = None
        if source_mode:
            source_id = task.context.get("source_id")
            if (pool != "claude" or source_mode != "read" or not source_id
                    or source_id not in self.source_roots or self.workspace_base is None):
                abort_unlaunched(self.dsn, lease)
                return {"state": "rejected", "call_id": lease.call_id,
                        "reason": "source_binding_unavailable"}
            try:
                workspace = materialize_source(self._source_for_call(lease.call_id, source_id),
                    self.workspace_base, lease.call_id, source_snapshot,
                    max_bytes=self.workspace_max_bytes)
            except (OSError, ValueError):
                abort_unlaunched(self.dsn, lease)
                return {"state": "rejected", "call_id": lease.call_id,
                        "reason": "source_snapshot_changed_or_unavailable"}
        if not claim_launch(self.dsn, lease):
            return {"state": "uncertain", "call_id": lease.call_id,
                    "reason": "launch_already_claimed"}
        common = {"task_id": task.task_id, "work_item_id": task.work_item_id,
                  "call_id": lease.call_id, "config_version": lease.policy_version,
                  "worker_instance_id": self.workers[pool]}
        health = self.emitter.health()
        self.emitter.emit(EventType.CALL_DISPATCHED, requested_model=model,
                          payload={"destination": "skynet" if pool == "qwen" else "claude",
                                   **runtime_status(self.dsn), "capture_state": health["state"],
                                   "known_gaps": health["known_gaps"]}, **common)
        attempt_id = f"{lease.call_id}:attempt:1"
        self.emitter.emit(EventType.ATTEMPT_STARTED, attempt_id=attempt_id,
                          payload={"attempt_number": 1}, **common)
        if not self.emitter.may_admit_new_work(self.policy.deployment.degraded_admission):
            mark_execution_uncertain(self.dsn, lease.call_id, reason="capture_degraded_before_launch")
            return {"state": "uncertain", "call_id": lease.call_id,
                    "reason": "capture_degraded_before_launch"}
        if adapter is None:
            mark_execution_uncertain(self.dsn, lease.call_id, reason="adapter_unavailable")
            return {"state": "uncertain", "call_id": lease.call_id,
                    "reason": "adapter_unavailable"}
        self.owned_calls.add(lease.call_id)
        if pool == "claude":
            result = (adapter.run(lease, task, prompt, self.emitter)
                      if workspace is None else
                      adapter.run(lease, task, prompt, self.emitter, workspace=workspace))
        else:
            result = adapter.run(lease, prompt, self.emitter, task=task,
                                 deadline_at=local_cutoff_at)
        if (pool == "qwen" and result.state == "denied" and
                result.uncertainty == "local_cutoff_passed"):
            return self._commit_predispatch_fallback(lease,
                "local_cutoff_passed_before_request", confirmed_not_sent=True)
        if result.state != "completed" or result.answer is None:
            response = {"state": result.state, "call_id": lease.call_id,
                        "reason": result.uncertainty}
            token = os.environ.get("BRIDGE_MONITOR_SYSTEM_TOKEN")
            if pool == "claude" and result.terminated and token:
                recovery = reconcile_owner_and_recover(self.dsn, lease.call_id,
                    {"kind": "process_exit_with_call_correlation",
                     "call_id": lease.call_id, "lease_id": lease.lease_id,
                     "owner_incarnation": lease.owner_incarnation, "terminated": True},
                    actor_kind="system", actor_id="coordinator", auth_token=token)
                response["state"] = recovery["state"]
                response["recovery"] = recovery
                for item in recovery["recoveries"]:
                    if item["state"] == "queued":
                        threading.Thread(target=self.run_recovery_until_terminal,
                            args=(item["recovery_call_id"],), daemon=True).start()
            else:
                mark_execution_uncertain(self.dsn, lease.call_id,
                                         reason=result.uncertainty or result.state)
            return response
        result_id, content_id = str(uuid.uuid4()), str(uuid.uuid4())
        if pool == "claude":
            envelope, claim_diagnostics = self._claimed_completion(
                lease, attempt_id, result_id, content_id, result.completion_claim,
                result.raw_content_id)
        else:
            envelope, claim_diagnostics = (CompletionEnvelope(call_id=lease.call_id,
                    attempt_id=attempt_id,
                        result={"result_id": result_id, "content_id": content_id,
                                "delivered": True}), [])
        binding = ExecutionBinding(worker_instance_id=self.workers[pool],
            producer_id=self.emitter.producer_id, producer_incarnation=self.incarnations[pool],
            call_id=lease.call_id, attempt_id=attempt_id)
        completion = record_completion(self.dsn, envelope, binding,
            answer_bytes=result.answer.encode("utf-8"),
            raw_answer_bytes=(result.raw_answer.encode("utf-8")
                              if result.raw_answer is not None else None),
            claim_diagnostics=claim_diagnostics,
            consumer_stage="claude" if pool == "qwen" else None)
        if completion.answer.delivered:
            self.emitter.emit(EventType.RESULT_DELIVERED, attempt_id=attempt_id,
                              payload={"result_id": result_id,
                                       "raw_content_id": result.raw_content_id or content_id,
                                       "delivered_content_id": content_id}, **common)
        self.emitter.emit(EventType.CALL_COMPLETED, attempt_id=attempt_id,
                          payload={"result_id": result_id}, **common)
        if source_mode:
            release_source(self.dsn, lease.call_id, task.context["source_id"])
        return {"state": "completed", "call_id": lease.call_id, "result_id": result_id,
                "answer": result.answer if completion.answer.delivered else None,
                "delivered": completion.answer.delivered,
                "result_sha256": (completion.answer.sha256
                                  if completion.answer.delivered else None),
                "reported_model": result.reported_model,
                "accounting_complete": completion.accounting_complete}

    def run_recovery_until_terminal(self, call_id: str) -> dict:
        """Keep a paid recovery eligible while another paid call holds capacity."""
        poll_seconds = self.policy.deployment.queue_poll_ms / 1000
        while True:
            with psycopg.connect(self.dsn) as conn:
                row = conn.execute("SELECT state,queue_deadline_at FROM runtime_jobs "
                                   "WHERE call_id=%s AND pool='claude'", (call_id,)).fetchone()
            if not row:
                return {"state": "unknown", "call_id": call_id}
            state, queue_deadline = row
            if state != "queued":
                return self.get_execution_status(call_id)
            remaining = (queue_deadline - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                if cancel_queued(self.dsn, call_id):
                    return {"state": "queue_expired", "call_id": call_id,
                            "reason": "paid_recovery_queue_deadline_exceeded"}
                return self.get_execution_status(call_id)
            outcome = self.run_one("claude", call_id=call_id)
            if outcome:
                return outcome
            time.sleep(min(poll_seconds, remaining))

    def resume_queued_recoveries(self) -> list[str]:
        """Resume only durable paid replacements after a parent MCP restart."""
        if self.claude_adapter is None:
            return []
        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute("SELECT l.recovery_call_id FROM runtime_recovery_links l "
                "JOIN runtime_jobs j ON j.call_id=l.recovery_call_id "
                "WHERE j.pool='claude' AND j.state='queued' "
                "ORDER BY j.admitted_at,l.recovery_call_id").fetchall()
        started = []
        with self._recovery_threads_lock:
            for (call_id,) in rows:
                prior = self._recovery_threads.get(call_id)
                if prior and prior.is_alive():
                    continue
                worker = threading.Thread(target=self.run_recovery_until_terminal,
                                          args=(call_id,), daemon=True)
                self._recovery_threads[call_id] = worker
                worker.start()
                started.append(call_id)
        return started

    def get_execution_status(self, call_id: str) -> dict:
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute("SELECT state,pool,slot_held,policy_version,lease_id FROM runtime_jobs "
                               "WHERE call_id=%s", (call_id,)).fetchone()
            result = conn.execute("SELECT r.result_id,r.delivered_at,content.sha256 "
                "FROM results r LEFT JOIN content_objects content "
                "ON content.content_id=r.delivered_content_id WHERE r.call_id=%s "
                "ORDER BY r.delivered_at DESC NULLS LAST LIMIT 1", (call_id,)).fetchone()
        if not row:
            return {"state": "unknown", "call_id": call_id, "capture": self.emitter.health()}
        return {"state": row[0], "pool": row[1], "slot_held": row[2],
                "policy_version": row[3], "lease_id": row[4], "call_id": call_id,
                "result_id": result[0] if result else None,
                "result_sha256": (result[2] if result and result[1] and
                                  call_id in self.owned_calls else None),
                "delivered": bool(result and result[1]), "capture": self.emitter.health()}

    def cancel_work(self, call_id: str, *, reason: str) -> dict:
        if call_id not in self.owned_calls:
            raise PermissionError("cancellation is outside this bridge client's owned calls")
        affected = request_cancel_tree(self.dsn, call_id, reason=reason)
        return {"call_id": call_id, "cancellation_requested": list(affected)}

    def complete_work(self, call_id: str, envelope: CompletionEnvelope) -> dict:
        if call_id not in self.owned_calls or envelope.call_id != call_id:
            raise PermissionError("completion is outside this bridge client's owned calls")
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute("SELECT owner_incarnation,worker_instance_id FROM runtime_jobs "
                               "WHERE call_id=%s", (call_id,)).fetchone()
        binding = ExecutionBinding(worker_instance_id=row[1], producer_id=self.emitter.producer_id,
            producer_incarnation=row[0], call_id=call_id, attempt_id=envelope.attempt_id)
        receipt = record_completion(self.dsn, envelope, binding)
        return {"result_id": receipt.answer.result_id,
                "accounting_complete": receipt.accounting_complete,
                "diagnostics": list(receipt.diagnostics)}

    def acknowledge_claude_result(self, call_id: str, report: dict) -> dict:
        if call_id not in self.owned_calls:
            raise PermissionError("acknowledgement is outside this parent process's owned calls")
        claim = ParentAcknowledgement.model_validate(report)
        receipt = record_parent_acknowledgement(self.dsn, call_id=call_id,
            producer_id=self.emitter.producer_id, report=claim)
        return {"acknowledgement_id": receipt.acknowledgement_id,
                "output_result_id": receipt.output_result_id,
                "judgment": receipt.judgment}


def create_mcp(coordinator: BridgeCoordinator, *, audience: str = "parent"):
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("Bridge Monitor")
    if audience == "parent":
        @mcp.tool()
        def delegate_claude(task: dict, prompt: str, requested_model: str,
                            reserve_tokens: int, source_id: str | None = None) -> dict:
            """Submit a scoped Claude worker call and return its result or durable queue ticket."""
            return coordinator.delegate_claude(TaskPacket.model_validate(task), prompt,
                requested_model=requested_model, reserve_tokens=reserve_tokens,
                source_id=source_id)

        @mcp.tool()
        def complete_work(call_id: str, envelope: dict) -> dict:
            """Supply a later completion acknowledgement for this client's owned call."""
            return coordinator.complete_work(call_id, CompletionEnvelope.model_validate(envelope))

        @mcp.tool()
        def acknowledge_claude_result(call_id: str, report: dict) -> dict:
            """Report how this parent used a delivered Claude result; parent-reported evidence."""
            return coordinator.acknowledge_claude_result(call_id, report)

        @mcp.tool()
        def get_execution_status(call_id: str) -> dict:
            """Inspect durable ownership, result and capture health for a call."""
            return coordinator.get_execution_status(call_id)

        @mcp.tool()
        def cancel_work(call_id: str, reason: str) -> dict:
            """Request cancellation of this client's call and its managed descendants."""
            return coordinator.cancel_work(call_id, reason=reason)
    elif audience == "claude":
        @mcp.tool()
        def delegate_local_analysis(prompt: str) -> dict:
            """Ask the scoped local Qwen worker for analysis; return answer or paid-continuation signal."""
            return coordinator.delegate_local_analysis(prompt)
    else:
        raise ValueError("unknown MCP audience")
    return mcp


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Project-local bridge MCP server")
    parser.add_argument("--audience", choices=("parent", "claude"), required=True)
    parser.add_argument("--parent-call-id")
    parser.add_argument("--parent-lease-id")
    args = parser.parse_args(argv)
    config_path = os.environ.get("BRIDGE_MONITOR_RUNTIME_CONFIG")
    if not config_path:
        print("BRIDGE_MONITOR_RUNTIME_CONFIG is required", file=sys.stderr)
        return 2
    try:
        from bridge.startup import build_coordinator
        coordinator = build_coordinator(config_path, audience=args.audience,
            parent_call_id=args.parent_call_id, parent_lease_id=args.parent_lease_id)
        if args.audience == "parent":
            coordinator.resume_queued_recoveries()
        create_mcp(coordinator, audience=args.audience).run(transport="stdio")
    except Exception as exc:
        print(f"bridge MCP startup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
