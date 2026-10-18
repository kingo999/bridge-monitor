"""Authenticated, request-scoped Ollama job status API."""

import asyncio
import hashlib
import json
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from skynet_agent.config import AgentSettings
from skynet_agent.jobs import JobJournal, OutputBuffer


class SubmitJob(BaseModel):
    job_id: str = Field(min_length=1)
    model: str = Field(min_length=1)
    model_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt: str
    options: dict[str, Any]
    keep_alive: str


@dataclass
class ActiveJob:
    output: OutputBuffer
    task: asyncio.Task | None = None
    last_ollama_frame_at: str | None = None
    last_output_at: str | None = None
    finished_at: float | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _request_sha256(request: SubmitJob) -> str:
    canonical = json.dumps(request.model_dump(exclude={"job_id"}),
                           sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_app(settings: AgentSettings, *, ollama_client: httpx.AsyncClient | None = None,
               job_task_factory=None) -> FastAPI:
    journal = JobJournal(settings.journal_path)
    incarnation = str(uuid.uuid4())
    jobs: dict[str, ActiveJob] = {}
    upstream = ollama_client or httpx.AsyncClient()

    async def sweep():
        while True:
            await asyncio.sleep(settings.cleanup_seconds)
            for expired in journal.prune_terminal(settings.receipt_retention_days):
                jobs.pop(expired, None)
            now = time.monotonic()
            for job_id, active in list(jobs.items()):
                if (active.finished_at is not None and
                        now - active.finished_at >= settings.output_retention_seconds):
                    jobs.pop(job_id, None)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        sweeper = asyncio.create_task(sweep())
        yield
        sweeper.cancel()
        with suppress(asyncio.CancelledError):
            await sweeper
        for active in jobs.values():
            if active.task and not active.task.done():
                active.task.cancel()
        await asyncio.gather(*(active.task for active in jobs.values() if active.task),
                             return_exceptions=True)
        await upstream.aclose()

    app = FastAPI(title="Skynet Job Status", lifespan=lifespan)

    def valid_auth(request: Request) -> bool:
        supplied = request.headers.get("authorization", "")
        try:
            return secrets.compare_digest(supplied.encode("ascii"),
                                          f"Bearer {settings.token}".encode("ascii"))
        except UnicodeEncodeError:
            return False

    @app.middleware("http")
    async def restrict_peer(request: Request, call_next):
        if settings.allowed_client_ips and (
                request.client is None or request.client.host not in settings.allowed_client_ips):
            return JSONResponse(status_code=403, content={"detail": "client not allowed"})
        if not valid_auth(request):
            return JSONResponse(status_code=401, content={"detail": "unauthorized"})
        length = request.headers.get("content-length")
        if length is not None and length.isdecimal() and int(length) > settings.max_request_bytes:
            return JSONResponse(status_code=413, content={"detail": "request exceeds configured limit"})
        return await call_next(request)

    async def inspect_model() -> tuple[bool, str | None]:
        try:
            tags = await upstream.get(f"{settings.ollama_endpoint.rstrip('/')}/api/tags",
                                      timeout=settings.ollama_connect_seconds)
            tags.raise_for_status()
            match = next((model for model in tags.json().get("models", [])
                          if model.get("name") == settings.model), None)
            observed = match.get("digest") if isinstance(match, dict) else None
            return observed == settings.model_digest, observed
        except Exception:
            return False, None

    async def authenticated(request: Request):
        if not valid_auth(request):
            raise HTTPException(status_code=401, detail="unauthorized")

    async def run_ollama(request: SubmitJob, active: ActiveJob):
        job_id = request.job_id
        try:
            journal.set_state(job_id, "ollama_connecting")
            ready, _ = await inspect_model()
            if not ready:
                journal.set_state(job_id, "failed", json.dumps(
                    {"reason": "model_digest_mismatch"}))
                return
            wire = {"model": request.model, "prompt": request.prompt,
                    "options": request.options, "keep_alive": request.keep_alive,
                    "stream": True}
            timeout = httpx.Timeout(settings.ollama_read_seconds,
                                    connect=settings.ollama_connect_seconds)
            async with upstream.stream("POST", f"{settings.ollama_endpoint.rstrip('/')}/api/generate",
                                       json=wire, timeout=timeout) as response:
                if response.status_code != 200:
                    journal.set_state(job_id, "failed", json.dumps(
                        {"reason": "ollama_http_error", "status_code": response.status_code}))
                    return
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    frame = json.loads(line)
                    if not isinstance(frame, dict) or frame.get("model") != request.model:
                        raise ValueError("invalid Ollama model frame")
                    active.last_ollama_frame_at = _utc_now()
                    delta = frame.get("response", "")
                    if delta:
                        if not isinstance(delta, str):
                            raise ValueError("invalid Ollama response text")
                        sequence = active.output.append(delta)
                        journal.note_output(job_id, sequence)
                        active.last_output_at = _utc_now()
                        journal.set_state(job_id, "streaming")
                    elif frame.get("done") is not True:
                        journal.set_state(job_id, "streaming")
                    if frame.get("done") is True:
                        terminal = {key: frame[key] for key in (
                            "model", "done", "prompt_eval_count", "eval_count",
                            "total_duration", "load_duration", "prompt_eval_duration",
                            "eval_duration", "done_reason") if key in frame}
                        journal.set_state(job_id, "completed", json.dumps(terminal))
                        return
                journal.set_state(job_id, "unknown", json.dumps(
                    {"reason": "missing_terminal_frame"}))
        except asyncio.CancelledError:
            journal.set_state(job_id, "unknown", json.dumps(
                {"reason": "cancelled_without_server_termination_evidence"}))
        except Exception as exc:
            journal.set_state(job_id, "unknown", json.dumps(
                {"reason": "upstream_uncertain", "exception_type": type(exc).__name__}))
        finally:
            active.finished_at = time.monotonic()

    @app.get("/v1/health", dependencies=[Depends(authenticated)])
    async def health():
        ready, observed = await inspect_model()
        return {"agent_incarnation": incarnation, "model": settings.model,
                "model_digest": settings.model_digest, "agent_alive": True,
                "model_ready": ready, "observed_digest": observed}

    @app.post("/v1/jobs", dependencies=[Depends(authenticated)])
    async def submit(request: SubmitJob):
        for expired in journal.prune_terminal(settings.receipt_retention_days):
            jobs.pop(expired, None)
        if len(request.prompt.encode("utf-8")) > settings.max_prompt_bytes:
            raise HTTPException(status_code=413, detail="prompt exceeds configured limit")
        if request.keep_alive != settings.keep_alive:
            raise HTTPException(status_code=422, detail="unconfigured keep-alive")
        options = request.options
        if (set(options) != {"num_ctx", "num_predict", "temperature"} or
                type(options.get("num_ctx")) is not int or
                not 1 <= options["num_ctx"] <= settings.max_context_tokens or
                type(options.get("num_predict")) is not int or
                not 1 <= options["num_predict"] <= settings.max_output_tokens or
                options["num_predict"] >= options["num_ctx"] or
                type(options.get("temperature")) not in (int, float) or
                not 0 <= options["temperature"] <= 2):
            raise HTTPException(status_code=422, detail="unconfigured generation options")
        if request.model != settings.model or request.model_digest != settings.model_digest:
            raise HTTPException(status_code=422, detail="unconfigured model")
        try:
            record, created = journal.accept(request.job_id, _request_sha256(request),
                                             request.model, incarnation)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if created:
            active = ActiveJob(OutputBuffer(max_bytes=settings.max_buffer_bytes))
            jobs[request.job_id] = active
            schedule = job_task_factory or asyncio.create_task
            active.task = schedule(run_ollama(request, active))

            def record_unstarted_cancel(task: asyncio.Task):
                if task.cancelled():
                    journal.set_state(request.job_id, "unknown", json.dumps(
                        {"reason": "cancelled_without_server_termination_evidence"}))
                if active.finished_at is None:
                    active.finished_at = time.monotonic()

            active.task.add_done_callback(record_unstarted_cancel)
        return JSONResponse(status_code=202, content={
            "job_id": record["job_id"], "request_sha256": record["request_sha256"],
            "state": record["state"], "agent_incarnation": record["agent_incarnation"],
            "accepted_at": record["accepted_at"]})

    @app.get("/v1/jobs/{job_id}", dependencies=[Depends(authenticated)])
    async def status(job_id: str, after_sequence: int = Query(default=0, ge=0)):
        record = journal.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown job")
        active = jobs.get(job_id)
        page = (active.output.page(after_sequence) if active else
                {"sequence": record["last_sequence"],
                 "gap": after_sequence < record["last_sequence"], "deltas": []})
        terminal = json.loads(record["terminal_json"]) if record["terminal_json"] else None
        return {"job_id": job_id, "request_sha256": record["request_sha256"],
                "agent_incarnation": record["agent_incarnation"],
                "state": record["state"], "accepted_at": record["accepted_at"],
                "updated_at": record["updated_at"], "last_ollama_frame_at":
                active.last_ollama_frame_at if active else None,
                "last_output_at": active.last_output_at if active else None,
                "terminal": terminal, **page}

    @app.post("/v1/jobs/{job_id}/cancel", dependencies=[Depends(authenticated)])
    async def cancel(job_id: str):
        record = journal.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown job")
        if record["state"] not in {"completed", "failed", "unknown"}:
            journal.set_state(job_id, "cancel_requested")
            active = jobs.get(job_id)
            if active and active.task and not active.task.done():
                active.task.cancel()
        return {"job_id": job_id, "state": journal.get(job_id)["state"]}

    return app
