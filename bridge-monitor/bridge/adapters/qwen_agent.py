"""Poll request-scoped Skynet jobs without treating agent health as model progress."""

import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timezone

from bridge.adapters.base import AdapterResult, ensure_lease
from bridge.adapters.qwen_runtime import ModelPreflight
from contracts.events import EventType


class QwenAgentAdapter:
    def __init__(self, client, *, endpoint: str, token: str, model: str,
                 expected_digest: str, context_tokens: int, output_tokens: int,
                 timeout_seconds: float, first_output_seconds: float,
                 idle_output_seconds: float, ack_seconds: float, poll_seconds: float,
                 poll_timeout_seconds: float, poll_failures: int,
                 keep_alive: str, temperature: float,
                 cancellation_probe=None):
        if not endpoint.startswith(("http://", "https://")) or not token or not model:
            raise ValueError("agent endpoint, token and model required")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_digest) or min(context_tokens, output_tokens) <= 0:
            raise ValueError("pinned model digest and token limits required")
        if output_tokens >= context_tokens:
            raise ValueError("output reservation must fit context")
        if min(timeout_seconds, first_output_seconds, idle_output_seconds, ack_seconds,
               poll_seconds, poll_timeout_seconds, poll_failures) <= 0:
            raise ValueError("positive agent deadline and poll limits required")
        self.client = client
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.model = model
        self.expected_digest = expected_digest
        self.context_tokens = context_tokens
        self.output_tokens = output_tokens
        self.timeout_seconds = timeout_seconds
        self.first_output_seconds = first_output_seconds
        self.idle_output_seconds = idle_output_seconds
        self.ack_seconds = ack_seconds
        self.poll_seconds = poll_seconds
        self.poll_timeout_seconds = poll_timeout_seconds
        self.poll_failures = poll_failures
        self.keep_alive = keep_alive
        self.temperature = temperature
        self.cancellation_probe = cancellation_probe

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def preflight(self) -> ModelPreflight:
        try:
            response = self.client.get(f"{self.endpoint}/v1/health",
                                       headers=self._headers, timeout=self.ack_seconds)
            if response.status_code != 200:
                return ModelPreflight(False, "agent_unavailable")
            status = response.json()
            if (status.get("model") != self.model or
                    status.get("model_digest") != self.expected_digest or
                    status.get("observed_digest") != self.expected_digest or
                    status.get("model_ready") is not True):
                return ModelPreflight(False, "model_profile_unverified",
                                      status.get("observed_digest"))
            return ModelPreflight(True, "model_profile_verified", self.expected_digest)
        except Exception:
            return ModelPreflight(False, "agent_unavailable")

    def _cancel_best_effort(self, call_id: str) -> bool:
        try:
            response = self.client.post(f"{self.endpoint}/v1/jobs/{call_id}/cancel",
                                        headers=self._headers, timeout=self.poll_timeout_seconds)
            return response.status_code == 200
        except Exception:
            return False

    def run(self, lease, prompt: str, emitter, task=None,
            deadline_at: datetime | None = None) -> AdapterResult:
        ensure_lease(lease)
        started = time.monotonic()
        absolute_deadline = started + self.timeout_seconds
        if deadline_at is not None:
            if deadline_at.tzinfo is None:
                raise ValueError("local cutoff must be timezone-aware")
            remaining = (deadline_at - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                return AdapterResult(state="denied", uncertainty="local_cutoff_passed")
            absolute_deadline = min(absolute_deadline, started + remaining)
        call = lease.call_id
        attempt_id = f"{call}:attempt:1"
        model_execution_id = f"{call}:model:1"
        common = {"call_id": call, "attempt_id": attempt_id,
                  "task_id": task.task_id if task else None,
                  "work_item_id": task.work_item_id if task else None,
                  "config_version": lease.policy_version}
        emitter.emit(EventType.MODEL_SELECTED, model_execution_id=model_execution_id,
                     requested_model=self.model,
                     payload={"lease_id": lease.lease_id, "fencing_token": lease.fencing_token},
                     **common)
        emitter.emit(EventType.MESSAGE_SENT, body=prompt.encode("utf-8"), role="user",
                     requested_model=self.model, **common)
        if time.monotonic() >= min(absolute_deadline, started + self.first_output_seconds):
            emitter.emit(EventType.ADMISSION_BLOCKED,
                         payload={"reason": "pre_submit_deadline"}, **common)
            return AdapterResult(state="denied", uncertainty="local_cutoff_passed")
        reachable: bool | None = None
        agent_state: str | None = None

        def uncertain(reason: str, *, accepted: bool = True) -> AdapterResult:
            nonlocal reachable
            if accepted:
                cancel_acknowledged = self._cancel_best_effort(call)
                if cancel_acknowledged:
                    reachable = True
                emitter.emit(EventType.ATTEMPT_STATUS,
                             payload={"agent_job_id": call, "agent_state": agent_state,
                                      "agent_reachable": reachable,
                                      "cancel_requested": True,
                                      "cancel_acknowledged": cancel_acknowledged}, **common)
            emitter.emit(EventType.ATTEMPT_STATUS,
                         payload={"agent_job_id": call, "agent_reachable": reachable,
                                  "agent_state": agent_state, "reason": reason}, **common)
            emitter.emit(EventType.ATTEMPT_UNCERTAIN,
                         payload={"reason": reason, "agent_job_id": call}, **common)
            return AdapterResult(state="uncertain", uncertainty=reason)

        request = {"job_id": call, "model": self.model,
                   "model_digest": self.expected_digest, "prompt": prompt,
                   "options": {"num_ctx": self.context_tokens,
                               "num_predict": self.output_tokens,
                               "temperature": self.temperature},
                   "keep_alive": self.keep_alive}
        canonical = json.dumps({key: value for key, value in request.items() if key != "job_id"},
                               sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        expected_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        try:
            response = self.client.post(f"{self.endpoint}/v1/jobs", json=request,
                                        headers=self._headers,
                                        timeout=min(self.ack_seconds,
                                                    max(0.001, absolute_deadline - time.monotonic())))
            if response.status_code != 202:
                if response.status_code in (401, 403, 409, 422):
                    return uncertain("agent_submission_rejected", accepted=False)
                return uncertain("agent_submission_uncertain")
            ack = response.json()
            if (ack.get("job_id") != call or ack.get("request_sha256") != expected_hash or
                    not isinstance(ack.get("agent_incarnation"), str) or
                    not ack["agent_incarnation"]):
                return uncertain("agent_ack_identity_mismatch")
            reachable = True
            agent_state = ack.get("state")
        except Exception:
            return uncertain("agent_ack_uncertain")

        expected_incarnation = ack["agent_incarnation"]
        emitter.emit(EventType.ATTEMPT_WAITING,
                     payload={"agent_job_id": call, "agent_incarnation": expected_incarnation,
                              "state": ack.get("state")}, **common)
        emitter.emit(EventType.ATTEMPT_STATUS,
                     payload={"agent_job_id": call, "agent_reachable": True,
                              "agent_state": ack.get("state"), "agent_incarnation": expected_incarnation},
                     **common)
        sequence = 0
        parts: list[str] = []
        first_output = None
        last_output = None
        consecutive_failures = 0
        previous_state = ack.get("state")

        def deadline_reason() -> str | None:
            now = time.monotonic()
            if now >= absolute_deadline:
                return "local_execution_deadline"
            if first_output is None and now - started >= self.first_output_seconds:
                return "first_output_deadline"
            if last_output is not None and now - last_output >= self.idle_output_seconds:
                return "idle_output_deadline"
            return None

        def next_deadline() -> float:
            progress_deadline = (started + self.first_output_seconds if first_output is None
                                 else last_output + self.idle_output_seconds)
            return min(absolute_deadline, progress_deadline)

        while True:
            expired = deadline_reason()
            if expired:
                return uncertain(expired)
            if self.cancellation_probe is not None and self.cancellation_probe(call):
                return uncertain("cancellation_requested")
            try:
                had_poll_failure = consecutive_failures > 0
                response = self.client.get(f"{self.endpoint}/v1/jobs/{call}",
                    params={"after_sequence": sequence}, headers=self._headers,
                    timeout=min(self.poll_timeout_seconds,
                                max(0.001, next_deadline() - time.monotonic())))
                response.raise_for_status()
                status = response.json()
                consecutive_failures = 0
            except Exception:
                consecutive_failures += 1
                reachable = False
                emitter.emit(EventType.ATTEMPT_STATUS,
                             payload={"agent_job_id": call, "agent_reachable": False,
                                      "agent_state": previous_state,
                                      "poll_failures": consecutive_failures}, **common)
                if consecutive_failures >= self.poll_failures:
                    return uncertain("agent_status_unavailable")
                time.sleep(min(self.poll_seconds, max(0, next_deadline() - time.monotonic())))
                continue
            expired = deadline_reason()
            if expired:
                return uncertain(expired)
            if (status.get("job_id") != call or status.get("gap") is not False or
                    status.get("request_sha256") != expected_hash or
                    status.get("agent_incarnation") != expected_incarnation):
                return uncertain("agent_status_identity_or_gap")
            reachable = True
            if (not isinstance(status.get("sequence"), int) or
                    isinstance(status["sequence"], bool) or status["sequence"] < sequence):
                return uncertain("agent_sequence_invalid")
            deltas = status.get("deltas")
            if not isinstance(deltas, list):
                return uncertain("agent_sequence_invalid")
            new_output = False
            for delta in deltas:
                expired = deadline_reason()
                if expired:
                    return uncertain(expired)
                if (not isinstance(delta, dict) or delta.get("sequence") != sequence + 1 or
                        not isinstance(delta.get("text"), str)):
                    return uncertain("agent_sequence_invalid")
                sequence += 1
                text = delta["text"]
                parts.append(text)
                if text:
                    now = time.monotonic()
                    if first_output is None:
                        first_output = now
                        last_output = now
                        emitter.emit(EventType.RESPONSE_STARTED,
                                     payload={"agent_job_id": call,
                                              "first_output_latency_ms": int((now - started) * 1000)},
                                     **common)
                        expired = deadline_reason()
                        if expired:
                            return uncertain(expired)
                    last_output = now
                    new_output = True
                    emitter.emit(EventType.RESPONSE_DELTA, body=text.encode("utf-8"),
                                 role="assistant", direction="in",
                                 requested_model=self.model, reported_model=self.model,
                                 message_id=f"{call}:response", chunk_sequence=sequence,
                                 **common)
                    expired = deadline_reason()
                    if expired:
                        return uncertain(expired)
            if status["sequence"] != sequence:
                return uncertain("agent_sequence_invalid")
            state = status.get("state")
            agent_state = state
            if state not in ("accepted", "ollama_connecting", "streaming", "completed",
                             "failed", "cancel_requested", "unknown"):
                return uncertain("agent_state_invalid")
            if state != previous_state or had_poll_failure or new_output:
                emitter.emit(EventType.ATTEMPT_STATUS,
                             payload={"agent_job_id": call, "agent_reachable": True,
                                      "agent_state": state, "agent_incarnation": expected_incarnation,
                                      "last_output_at": status.get("last_output_at"),
                                      "last_ollama_frame_at": status.get("last_ollama_frame_at")},
                             **common)
                previous_state = state
                expired = deadline_reason()
                if expired:
                    return uncertain(expired)
            if state == "completed":
                terminal = status.get("terminal")
                if (not isinstance(terminal, dict) or terminal.get("done") is not True or
                        terminal.get("model") != self.model):
                    return uncertain("agent_terminal_unverified")
                usage = {}
                for source, target in (("prompt_eval_count", "input_tokens"),
                                       ("eval_count", "output_tokens")):
                    count = terminal.get(source)
                    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                        usage[target] = count
                answer = "".join(parts)
                emitter.emit(EventType.MODEL_REPORTED, model_execution_id=model_execution_id,
                             requested_model=self.model, reported_model=self.model,
                             payload={"evidence": "agent_ollama_terminal"}, **common)
                expired = deadline_reason()
                if expired:
                    return uncertain(expired)
                if usage:
                    emitter.emit(EventType.USAGE_REPORTED, model_execution_id=model_execution_id,
                                 payload={"observation_id": str(uuid.uuid4()),
                                          "scope": "execution", "is_cumulative": True,
                                          "provenance": "agent_ollama_terminal", **usage}, **common)
                    expired = deadline_reason()
                    if expired:
                        return uncertain(expired)
                emitter.emit(EventType.RESPONSE_COMPLETED, body=answer.encode("utf-8"),
                             requested_model=self.model, reported_model=self.model,
                             payload={"usage": usage, "agent_job_id": call}, **common)
                expired = deadline_reason()
                if expired:
                    return uncertain(expired)
                return AdapterResult(state="completed", answer=answer,
                                     reported_model=self.model, usage=usage)
            if state in ("failed", "cancel_requested", "unknown"):
                return uncertain(f"agent_{state}")
            time.sleep(min(self.poll_seconds, max(0, next_deadline() - time.monotonic())))
