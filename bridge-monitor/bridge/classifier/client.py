"""Bounded, non-retrying Ollama chat client for the advisory classifier.

One request per call. Any failure after the request may have reached the server is
reported, never retried, repaired or sent elsewhere.
"""

import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from bridge.classifier.rubric import RESPONSE_SCHEMA, RUBRIC
from contracts.classifier import GenerationProfile

DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")
MIN_GPU_FRACTION = 0.99


def _utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class IdentityCheck:
    ok: bool
    reason: str
    runtime_version: str | None = None
    observed_digest: str | None = None


@dataclass
class ResidencyCheck:
    """Evidence that the pinned model is resident and fully offloaded to a GPU."""

    state: str  # gpu_resident | not_resident | partial_offload | cpu_only | unavailable
    model: str | None = None
    digest: str | None = None
    size_bytes: int | None = None
    size_vram_bytes: int | None = None
    context_length: int | None = None
    expires_at: str | None = None

    @property
    def gpu_demonstrated(self) -> bool:
        return self.state == "gpu_resident"

    def evidence(self) -> dict:
        return {"state": self.state, "model": self.model, "digest": self.digest,
                "size_bytes": self.size_bytes, "size_vram_bytes": self.size_vram_bytes,
                "context_length": self.context_length}


@dataclass
class InferenceOutcome:
    status: str  # completed | incomplete | timeout | cancelled | transport_error | http_error | oversized
    sent: bool = False
    raw_text: str = ""
    thinking_present: bool = False
    done_reason: str | None = None
    reported_model: str | None = None
    terminal: dict | None = None
    error_type: str | None = None
    error_detail: str | None = None
    started_at: datetime | None = None
    first_content_at: datetime | None = None
    finished_at: datetime | None = None
    request_seconds: float | None = None
    first_content_seconds: float | None = None
    usage: dict = field(default_factory=dict)
    durations_ms: dict = field(default_factory=dict)

    @property
    def terminal_ok(self) -> bool:
        return (self.status == "completed" and self.done_reason == "stop"
                and not self.thinking_present)


class OllamaClassifier:
    def __init__(self, *, endpoint: str, model: str, expected_digest: str,
                 profile: GenerationProfile, keep_alive: str = "30m",
                 connect_timeout: float = 2.0, request_timeout: float = 60.0,
                 max_response_bytes: int = 8192, client: httpx.Client | None = None):
        host = endpoint.rstrip("/")
        if not re.fullmatch(r"http://(127\.0\.0\.1|localhost|\[::1\]):\d+", host):
            raise ValueError("classifier endpoint must be an explicit loopback http URL")
        if not model or not DIGEST_PATTERN.fullmatch(expected_digest):
            raise ValueError("model name and a 64-hex manifest digest are required")
        if min(connect_timeout, request_timeout) <= 0 or max_response_bytes <= 0:
            raise ValueError("positive timeouts and response bound required")
        self.endpoint = host
        self.model = model
        self.expected_digest = expected_digest
        self.profile = profile
        self.keep_alive = keep_alive
        self.request_timeout = request_timeout
        self.max_response_bytes = max_response_bytes
        self._owns_client = client is None
        self.http = client or httpx.Client(
            timeout=httpx.Timeout(connect=connect_timeout, read=request_timeout,
                                  write=5.0, pool=connect_timeout), trust_env=False)

    def close(self) -> None:
        if self._owns_client:
            self.http.close()

    # -- identity and residency (read-only endpoints; no inference) ----------------
    def check_identity(self) -> IdentityCheck:
        try:
            version = self.http.get(f"{self.endpoint}/api/version", timeout=3.0)
            if version.status_code != 200:
                return IdentityCheck(False, "endpoint_unavailable")
            runtime_version = version.json().get("version")
            tags = self.http.get(f"{self.endpoint}/api/tags", timeout=5.0)
            if tags.status_code != 200:
                return IdentityCheck(False, "model_metadata_unavailable", runtime_version)
            match = next((m for m in tags.json().get("models", [])
                          if m.get("name") == self.model), None)
        except (httpx.HTTPError, ValueError):
            return IdentityCheck(False, "endpoint_unavailable")
        if not match or not isinstance(match.get("digest"), str):
            return IdentityCheck(False, "model_not_installed", runtime_version)
        digest = match["digest"]
        if digest != self.expected_digest:
            return IdentityCheck(False, "model_digest_mismatch", runtime_version, digest)
        return IdentityCheck(True, "model_identity_verified", runtime_version, digest)

    def residency(self) -> ResidencyCheck:
        try:
            response = self.http.get(f"{self.endpoint}/api/ps", timeout=5.0)
            if response.status_code != 200:
                return ResidencyCheck("unavailable")
            rows = response.json().get("models", [])
        except (httpx.HTTPError, ValueError):
            return ResidencyCheck("unavailable")
        row = next((m for m in rows if m.get("name") == self.model), None)
        if row is None:
            return ResidencyCheck("not_resident")
        size, vram = row.get("size"), row.get("size_vram")
        valid = (isinstance(size, int) and isinstance(vram, int) and not isinstance(size, bool)
                 and not isinstance(vram, bool) and size > 0 and 0 <= vram <= size)
        base = dict(model=row.get("name"), digest=row.get("digest"),
                    size_bytes=size if isinstance(size, int) else None,
                    size_vram_bytes=vram if isinstance(vram, int) else None,
                    context_length=row.get("context_length"), expires_at=row.get("expires_at"))
        if not valid:
            return ResidencyCheck("unavailable", **base)
        if vram == 0:
            return ResidencyCheck("cpu_only", **base)
        if vram < size * MIN_GPU_FRACTION:
            return ResidencyCheck("partial_offload", **base)
        return ResidencyCheck("gpu_resident", **base)

    # -- inference --------------------------------------------------------------
    def payload(self, user_message: str, seed: int | None = None) -> dict:
        return {"model": self.model,
                "messages": [{"role": "system", "content": RUBRIC},
                             {"role": "user", "content": user_message}],
                "format": RESPONSE_SCHEMA, "think": self.profile.think, "stream": True,
                "keep_alive": self.keep_alive, "options": self.profile.options(seed)}

    def classify(self, user_message: str, *, seed: int | None = None,
                 cancel: threading.Event | None = None,
                 deadline_seconds: float | None = None) -> InferenceOutcome:
        budget = min(deadline_seconds or self.request_timeout, self.request_timeout)
        out = InferenceOutcome(status="transport_error", started_at=_utc())
        begun = time.perf_counter()
        parts: list[str] = []
        size = 0
        stop = threading.Event()
        flags = {"cancelled": False, "timed_out": False}
        holder: dict = {}

        def watch():
            while not stop.wait(0.05):
                if cancel is not None and cancel.is_set():
                    flags["cancelled"] = True
                elif time.perf_counter() - begun >= budget:
                    flags["timed_out"] = True
                else:
                    continue
                response = holder.get("response")
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        pass
                return

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            with self.http.stream("POST", f"{self.endpoint}/api/chat",
                                  json=self.payload(user_message, seed)) as response:
                holder["response"] = response
                out.sent = True
                if response.status_code != 200:
                    out.status, out.error_type = "http_error", f"http_{response.status_code}"
                    return out
                for line in response.iter_lines():
                    if flags["cancelled"] or flags["timed_out"]:
                        break
                    if not line.strip():
                        continue
                    frame = json.loads(line)
                    if not isinstance(frame, dict):
                        raise ValueError("response frame is not an object")
                    if frame.get("error"):
                        out.status, out.error_type = "http_error", "server_error"
                        out.error_detail = str(frame["error"])[:300]
                        return out
                    if isinstance(frame.get("model"), str):
                        if out.reported_model and out.reported_model != frame["model"]:
                            raise ValueError("reported model changed mid-stream")
                        out.reported_model = frame["model"]
                    message = frame.get("message") or {}
                    if message.get("thinking"):
                        out.thinking_present = True
                    delta = message.get("content") or ""
                    if delta:
                        if out.first_content_at is None:
                            out.first_content_at = _utc()
                            out.first_content_seconds = time.perf_counter() - begun
                        size += len(delta.encode("utf-8"))
                        if size > self.max_response_bytes:
                            out.status, out.error_type = "oversized", "response_too_large"
                            return out
                        parts.append(delta)
                    if frame.get("done") is True:
                        out.terminal = frame
                        out.done_reason = frame.get("done_reason")
                        break
        except Exception as exc:  # network loss, bad frame, closed stream: state is uncertain
            if flags["cancelled"]:
                out.status, out.error_type = "cancelled", "cancelled"
            elif flags["timed_out"]:
                out.status, out.error_type = "timeout", "deadline_exceeded"
            elif isinstance(exc, (httpx.TimeoutException,)):
                out.status, out.error_type = "timeout", type(exc).__name__
            else:
                out.status, out.error_type = "transport_error", type(exc).__name__
            out.error_detail = str(exc)[:300]
        else:
            if flags["cancelled"]:
                out.status, out.error_type = "cancelled", "cancelled"
            elif flags["timed_out"]:
                out.status, out.error_type = "timeout", "deadline_exceeded"
            elif out.terminal is None:
                out.status, out.error_type = "incomplete", "missing_terminal_frame"
            elif out.done_reason != "stop":
                out.status = "incomplete"
                out.error_type = ("token_limit" if out.done_reason == "length"
                                  else "unexpected_done_reason")
            else:
                out.status = "completed"
        finally:
            stop.set()
            watcher.join(timeout=1)
            out.raw_text = "".join(parts)
            out.finished_at = _utc()
            out.request_seconds = time.perf_counter() - begun
        if out.terminal is not None:
            terminal = out.terminal
            for source, target in (("prompt_eval_count", "input_tokens"),
                                   ("eval_count", "output_tokens")):
                value = terminal.get(source)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    out.usage[target] = value
            for source, target in (("total_duration", "inference_ms"), ("load_duration", "load_ms"),
                                   ("prompt_eval_duration", "prompt_eval_ms"),
                                   ("eval_duration", "eval_ms")):
                value = terminal.get(source)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    out.durations_ms[target] = round(value / 1e6)
        return out
