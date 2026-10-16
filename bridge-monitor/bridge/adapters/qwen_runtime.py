"""Ollama streaming adapter. A transport loss after POST is uncertain, never retried."""

import json
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from contracts.events import EventType
from bridge.adapters.base import AdapterResult, ensure_lease


@dataclass(frozen=True)
class ModelPreflight:
    ready: bool
    reason: str
    observed_digest: str | None = None


class QwenAdapter:
    def __init__(self, client, *, endpoint: str, model: str, context_tokens: int,
                 output_tokens: int, timeout_seconds: int, allowed: bool = True,
                 keep_alive: str = "10m", temperature: float = 0.0,
                 expected_digest: str = "", cancellation_probe=None,
                 cancel_poll_seconds: float = 0.25):
        if not endpoint.startswith(("http://", "https://")) or not model:
            raise ValueError("validated endpoint and model are required")
        if min(context_tokens, output_tokens, timeout_seconds) <= 0:
            raise ValueError("positive context, output and timeout limits required")
        if output_tokens >= context_tokens:
            raise ValueError("output reservation must fit context")
        if expected_digest and not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
            raise ValueError("expected model digest must be 64 lowercase hex characters")
        self.client = client
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.context_tokens = context_tokens
        self.output_tokens = output_tokens
        self.timeout_seconds = timeout_seconds
        self.allowed = allowed
        self.keep_alive = keep_alive
        self.temperature = temperature
        self.expected_digest = expected_digest
        self.cancellation_probe = cancellation_probe
        self.cancel_poll_seconds = cancel_poll_seconds

    def preflight(self) -> ModelPreflight:
        if not self.allowed:
            return ModelPreflight(False, "local_capability_denied")
        if not self.expected_digest:
            return ModelPreflight(False, "model_digest_unpinned")
        try:
            response = self.client.get(f"{self.endpoint}/api/tags", timeout=min(5, self.timeout_seconds))
            if response.status_code != 200:
                return ModelPreflight(False, "model_metadata_unavailable")
            models = response.json().get("models", [])
            match = next((item for item in models if item.get("name") == self.model), None)
            if not match or not isinstance(match.get("digest"), str):
                return ModelPreflight(False, "model_not_installed")
            digest = match["digest"]
            if digest != self.expected_digest:
                return ModelPreflight(False, "model_digest_mismatch", digest)
            return ModelPreflight(True, "model_profile_verified", digest)
        except Exception:
            return ModelPreflight(False, "model_metadata_unavailable")

    def run(self, lease, prompt: str, emitter, task=None,
            deadline_at: datetime | None = None) -> AdapterResult:
        ensure_lease(lease)
        if not self.allowed:
            emitter.emit(EventType.ADMISSION_BLOCKED, call_id=lease.call_id,
                         payload={"reason": "local_capability_denied"})
            return AdapterResult(state="denied")
        request_started = datetime.now(timezone.utc)
        effective_deadline = request_started + timedelta(seconds=self.timeout_seconds)
        if deadline_at is not None:
            if deadline_at.tzinfo is None:
                raise ValueError("local cutoff must be timezone-aware")
            effective_deadline = min(effective_deadline, deadline_at)
            if effective_deadline <= request_started:
                emitter.emit(EventType.ADMISSION_BLOCKED, call_id=lease.call_id,
                             payload={"reason": "local_cutoff_passed_before_request"})
                return AdapterResult(state="denied", uncertainty="local_cutoff_passed")
        timeout = (effective_deadline - request_started).total_seconds()
        call = lease.call_id
        attempt_id = f"{call}:attempt:1"
        model_execution_id = f"{call}:model:1"
        common = {"call_id": call, "attempt_id": attempt_id,
                  "task_id": task.task_id if task else None,
                  "work_item_id": task.work_item_id if task else None,
                  "config_version": lease.policy_version}
        emitter.emit(EventType.MODEL_SELECTED, model_execution_id=model_execution_id,
                     requested_model=self.model,
                     config_version=lease.policy_version,
                     payload={"lease_id": lease.lease_id, "fencing_token": lease.fencing_token},
                     call_id=call, attempt_id=attempt_id)
        emitter.emit(EventType.MESSAGE_SENT, body=prompt.encode("utf-8"), role="user",
                     requested_model=self.model, **common)
        wire = {"model": self.model, "prompt": prompt, "stream": True,
                "options": {"num_ctx": self.context_tokens, "num_predict": self.output_tokens,
                            "temperature": self.temperature},
                "keep_alive": self.keep_alive}
        parts = []
        reported = None
        terminal = None
        message_id = str(uuid.uuid4())
        cancelled = threading.Event()
        try:
            with self.client.stream("POST", f"{self.endpoint}/api/generate", json=wire,
                                    timeout=timeout) as response:
                if response.status_code != 200:
                    emitter.emit(EventType.CALL_FAILED, call_id=call,
                                 payload={"status_code": response.status_code})
                    return AdapterResult(state="failed")
                emitter.emit(EventType.RESPONSE_STARTED, payload={"lease_id": lease.lease_id}, **common)
                stop_monitor = threading.Event()
                def monitor_cancel():
                    while not stop_monitor.wait(self.cancel_poll_seconds):
                        try:
                            requested = self.cancellation_probe(call)
                        except Exception:
                            continue
                        if requested:
                            cancelled.set()
                            try:
                                response.close()
                            except Exception:
                                pass
                            return
                watcher = None
                if self.cancellation_probe is not None:
                    watcher = threading.Thread(target=monitor_cancel, daemon=True)
                    watcher.start()
                try:
                    for raw_line in response.iter_lines():
                        if cancelled.is_set():
                            raise InterruptedError("local cancellation requested")
                        if datetime.now(timezone.utc) >= effective_deadline:
                            raise TimeoutError("local execution deadline reached during response")
                        if not raw_line:
                            continue
                        row = json.loads(raw_line)
                        if not isinstance(row, dict):
                            raise ValueError("invalid Ollama response frame")
                        if isinstance(row.get("model"), str):
                            if reported and reported != row["model"]:
                                raise ValueError("reported model changed during response")
                            reported = row["model"]
                        delta = row.get("response", "")
                        if delta:
                            if not isinstance(delta, str):
                                raise ValueError("invalid response text")
                            parts.append(delta)
                            emitter.emit(EventType.RESPONSE_DELTA,
                                         body=delta.encode("utf-8"), role="assistant", direction="in",
                                         requested_model=self.model, reported_model=reported,
                                         message_id=message_id, chunk_sequence=len(parts), **common)
                        if row.get("done") is True:
                            terminal = row
                            break
                finally:
                    stop_monitor.set()
                    if watcher is not None:
                        watcher.join(timeout=1)
        except Exception as exc:
            emitter.emit(EventType.ATTEMPT_UNCERTAIN,
                         payload={"reason": "cancellation_requested" if cancelled.is_set()
                                  else "transport_or_frame_loss", "exception_type": type(exc).__name__,
                                  "partial_output": bool(parts)}, **common)
            return AdapterResult(state="uncertain", reported_model=reported,
                                 uncertainty="request_may_have_run", cancelled=cancelled.is_set())
        if cancelled.is_set():
            return AdapterResult(state="uncertain", reported_model=reported,
                                 uncertainty="request_may_have_run", cancelled=True)
        if terminal is None or reported != self.model:
            emitter.emit(EventType.ATTEMPT_UNCERTAIN,
                         payload={"reason": "missing_terminal_or_model_evidence"}, **common)
            return AdapterResult(state="uncertain", reported_model=reported,
                                 uncertainty="completion_unverified")
        answer = "".join(parts)
        usage = {}
        for source, target in (("prompt_eval_count", "input_tokens"), ("eval_count", "output_tokens")):
            value = terminal.get(source)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                usage[target] = value
        emitter.emit(EventType.MODEL_REPORTED, model_execution_id=model_execution_id,
                     requested_model=self.model, reported_model=reported,
                     payload={"evidence": "ollama_terminal"}, **common)
        if usage:
            emitter.emit(EventType.USAGE_REPORTED, model_execution_id=model_execution_id,
                         payload={"observation_id": str(uuid.uuid4()), "scope": "execution",
                                  "is_cumulative": True, "provenance": "ollama_terminal", **usage},
                         **common)
        emitter.emit(EventType.RESPONSE_COMPLETED, body=answer.encode("utf-8"),
                     requested_model=self.model, reported_model=reported,
                     payload={"usage": usage, "lease_id": lease.lease_id}, **common)
        return AdapterResult(state="completed", answer=answer, reported_model=reported, usage=usage)
