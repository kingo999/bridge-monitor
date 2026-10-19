"""Single-flight observational runner. It records recommendations; it never acts on them.

It reads captured inputs, asks the local classifier once, validates and assesses the
answer, and writes an observation. It imports no dispatcher, admission, paid-call or
worker module, so a recommendation cannot start or alter any execution path.
"""

import hashlib
import math
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime

import psycopg

from bridge.classifier import store
from bridge.classifier.client import OllamaClassifier
from bridge.classifier.config import ClassifierShadowConfig
from bridge.classifier.interpret import interpret
from bridge.classifier.policy import map_actual_route
from bridge.classifier.rubric import RUBRIC
from contracts.classifier import ClassifierInput, TrustedFacts


class RunnerBusy(RuntimeError):
    """Another classifier runner already holds the single-runner lock."""


@dataclass
class ProcessResult:
    status: str  # processed | idle | unavailable | halted
    observation_id: str | None = None
    state: str | None = None
    reason: str | None = None


class ShadowRunner:
    def __init__(self, dsn: str, cfg: ClassifierShadowConfig, classifier: OllamaClassifier,
                 *, runner_id: str | None = None):
        self.dsn = dsn
        self.cfg = cfg
        self.classifier = classifier
        self.runner_id = runner_id or f"classifier-runner:{uuid.uuid4()}"
        self.halted: str | None = None
        self._lock_conn: psycopg.Connection | None = None

    # The advisory lock lives on a dedicated connection for the runner's lifetime.
    def start(self) -> int:
        if not self.cfg.enabled:
            raise RuntimeError("classifier shadow is disabled by configuration")
        conn = psycopg.connect(self.dsn, connect_timeout=5, autocommit=True)
        if not conn.execute("SELECT pg_try_advisory_lock(%s)", (store.RUNNER_LOCK,)).fetchone()[0]:
            conn.close()
            raise RunnerBusy("another classifier runner is active")
        self._lock_conn = conn
        return store.recover_interrupted(self.dsn)

    def close(self) -> None:
        if self._lock_conn is not None:
            self._lock_conn.close()
            self._lock_conn = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()

    def process_one(self, cancel: threading.Event | None = None) -> ProcessResult:
        if self.halted:
            return ProcessResult("halted", reason=self.halted)
        now = store.utcnow()
        store.expire_stale(self.dsn, self.cfg, now)
        identity = self.classifier.check_identity()
        if not identity.ok:
            # Nothing is claimed or sent; queued inputs age out instead of being classified late.
            return ProcessResult("unavailable", reason=identity.reason)
        claimed_at = store.utcnow()
        row = store.claim_next(self.dsn, self.runner_id, claimed_at)
        if row is None:
            return ProcessResult("idle")
        item = ClassifierInput(request=row["request_text"] or "", source=row["source_text"] or "",
                               facts=row["facts"])
        message = item.user_message()
        outcome = self.classifier.classify(message, seed=row["profile"].get("seed"),
                                           cancel=cancel,
                                           deadline_seconds=self.cfg.request_timeout_seconds)
        residency = self.classifier.residency()
        interp = interpret(outcome, expected_model=self.cfg.model, facts=item.trusted())
        fields = self._fields(row, outcome, interp, identity, residency, message)
        raw = outcome.raw_text.encode("utf-8") if outcome.raw_text else None
        store.finish(self.dsn, row["observation_id"], state=interp.state, fields=fields,
                     response=raw, response_complete=outcome.terminal_ok)
        if (self.cfg.halt_on_cpu_fallback
                and residency.state in ("cpu_only", "partial_offload")):
            self.halted = f"gpu_residency_{residency.state}"
        return ProcessResult("processed", row["observation_id"], interp.state,
                             interp.error_type or interp.validation_error)

    def _route_link(self, work_item_id: str | None) -> dict:
        """Optional enrichment: the unchanged system's route, or nothing.

        A lookup failure never costs the classification result: the route stays NULL (fillable
        later) and the failure is noted in the provenance. A missing, pending or unmapped
        decision also leaves the route NULL, never the literal ``unknown``.
        """
        try:
            project_route, provenance = store.actual_route_for(self.dsn, work_item_id)
        except Exception as exc:  # ordinary lookup failure only; termination signals propagate
            return {"actual_route_source": {"status": "lookup_failed",
                                            "error_type": type(exc).__name__}}
        if project_route is None:
            return {}
        mapped = map_actual_route(project_route)
        if mapped == "unknown":
            return {"actual_route_source": {**provenance, "status": "unmapped_route"},
                    "actual_route_observed_at": store.utcnow()}
        return {"actual_route": mapped, "actual_route_source": provenance,
                "actual_route_observed_at": store.utcnow()}

    def _fields(self, row, outcome, interp, identity, residency, message: str) -> dict:
        captured_at: datetime = row["captured_at"]
        dispatched_at: datetime = row["dispatched_at"]
        finished = outcome.finished_at
        fields = {
            "runtime_version": identity.runtime_version, "observed_digest": identity.observed_digest,
            "reported_model": outcome.reported_model, "backend": residency.evidence(),
            "done_reason": outcome.done_reason,
            "first_content_at": outcome.first_content_at, "completed_at": finished,
            "queue_ms": store.milliseconds(dispatched_at - captured_at),
            "request_ms": round(outcome.request_seconds * 1000) if outcome.request_seconds else None,
            "first_content_ms": (round(outcome.first_content_seconds * 1000)
                                 if outcome.first_content_seconds is not None else None),
            "end_to_end_ms": store.milliseconds(finished - captured_at),
            "error_type": interp.error_type, "validation_error": interp.validation_error,
            "valid": interp.valid, "policy_version": interp.assessment.policy_version,
            "policy_allowed": interp.assessment.allowed,
            "policy_blocks": interp.assessment.blocks, "policy_warnings": interp.assessment.warnings,
            **outcome.durations_ms}
        if outcome.error_detail:
            fields["error_detail"] = outcome.error_detail
        if outcome.raw_text:
            raw = outcome.raw_text.encode("utf-8")
            fields.update(response_bytes=len(raw), response_sha256=hashlib.sha256(raw).hexdigest())
        if interp.recommendation:
            fields.update(rec_category=interp.recommendation.category,
                          rec_route=interp.recommendation.route)
        fields.update(self._route_link(row["work_item_id"]))
        if outcome.usage:
            fields.update(outcome.usage, usage_provenance="ollama_terminal")
        elif outcome.status == "completed":
            prompt_bytes = len(RUBRIC.encode("utf-8")) + len(message.encode("utf-8"))
            fields.update(input_tokens=math.ceil(prompt_bytes / 4),
                          usage_provenance="estimated_utf8_bytes_div_4")
        return fields

    def run(self, *, stop: threading.Event, max_items: int | None = None,
            until_idle: bool = False, idle_seconds: float = 2.0) -> dict:
        """Process items until stopped. ``until_idle`` (or ``max_items``) also ends the loop
        when the queue is empty or the classifier is unavailable."""
        counts = {"processed": 0, "unavailable": 0}
        while not stop.is_set() and (max_items is None or counts["processed"] < max_items):
            result = self.process_one(stop)
            if result.status == "processed":
                counts["processed"] += 1
            elif result.status == "halted":
                counts["halted"] = result.reason
                break
            else:
                counts[result.status] = counts.get(result.status, 0) + 1
                if (until_idle or max_items is not None) and result.status in ("idle", "unavailable"):
                    break
                stop.wait(idle_seconds)
        return counts
