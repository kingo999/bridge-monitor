# Skynet Task Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each Qwen delegation an acknowledged, pollable job identity so silent or lost Skynet work falls back promptly without mistaking agent health for model progress.

**Architecture:** A small FastAPI agent on Skynet submits one Ollama request per idempotent bridge call ID and exposes bounded status/delta polling. A new bridge adapter polls with short HTTP deadlines and separate first-output, idle and total deadlines. Existing direct Ollama behavior remains configured until the new route passes synthetic and live checks.

**Tech Stack:** Python 3.13, FastAPI, HTTPX, SQLite, Pydantic, PostgreSQL telemetry, pytest.

**Spec:** [Skynet task status addendum](../../specifications/skynet-task-status.md), extending the approved [phase 1 specification](../../specifications/phase-1-specification.md).

## Global Constraints

- Keep all maintained source under `C:\AI\bridge-monitor`; do not edit older bridge trees.
- Preserve automatic paid fallback, one winner per child call, late-result handling, and uncertain-slot retention.
- Keep Qwen gated and the existing desktop default route unchanged during implementation.
- Never treat agent reachability, a model resident in `/api/ps`, or a heartbeat as proof of this job's model progress.
- All timeout, poll, buffer, retention, endpoint and model values belong in validated config files. Secrets use environment references.
- Use the current checkout because the project has uncommitted desktop-adoption changes that this work must preserve; stage or commit no unrelated files.

**Suggested execution model: Claude Opus.** Request idempotency, timeouts and cancellation cross process and host boundaries, so a mistaken state transition could duplicate inference or free uncertain capacity. Advisory; the user's chosen model remains valid.

## Review Focus

1. A duplicate job ID with different prompt, model or options must reject without a second Ollama call. Task 1.
2. Agent restart after acceptance but before terminal evidence must report `unknown`, not replay. Task 1.
3. Agent remains healthy and returns heartbeats while Ollama sends no output: first-output or idle deadline still triggers fallback. Task 2.
4. A status page skips an output sequence or changes the job/model identity: mark uncertain, never assemble a false complete answer. Task 2.
5. Bridge loses contact after Ollama accepted a request: continue paid work, retain Qwen capacity until correlated termination evidence. Tasks 2–3.

---

### Task 1: Request-scoped Skynet agent

**Files:** Create `skynet_agent/app.py`, `skynet_agent/jobs.py`, `skynet_agent/config.py`, `scripts/start-skynet-agent.py`, `config/skynet-agent.example.toml`; test in `tests/agent/test_jobs.py` and `tests/agent/test_api.py`.

**Interfaces:** `POST /v1/jobs`, `GET /v1/jobs/{id}?after_sequence=N`, `POST /v1/jobs/{id}/cancel`, and `GET /v1/health` per spec. The agent returns contiguous output sequences and terminal metadata; bridge callers authenticate with a bearer token.

- [x] Write failing tests for immediate durable ack, same-hash idempotency, conflicting-hash rejection, ordered deltas, bounded-buffer gap, terminal `done: true`, restart-to-unknown, auth and cancellation uncertainty.
- [x] Run the agent tests and confirm the intended failures.
- [x] Implement the minimal FastAPI agent and SQLite status journal. Keep prompt and response bodies out of the journal; use a bounded active buffer and fake Ollama transport in tests.
- [x] Run agent tests and confirm all pass; inspect the journal schema for content leakage.

### Task 2: Bridge adapter and deadline policy

**Files:** Create `bridge/adapters/qwen_agent.py`; modify `contracts/policy.py`, `bridge/startup.py`, `bridge/runtime/policy_reload.py`, `contracts/events.py`; test in `tests/adapters/test_qwen_agent.py` and `tests/contracts/test_policy.py`.

**Interfaces:** `QwenAgentAdapter.preflight() -> ModelPreflight` and `run(lease, prompt, emitter, task=None, deadline_at=None) -> AdapterResult`, matching the current Qwen adapter. Select it only when an explicit agent endpoint and token reference are configured. Config fields include acknowledgement, poll cadence/request timeout/failure threshold, first-output and idle deadlines; existing `qwen_execution_seconds` remains the hard cap.

- [x] Write failing fake-agent tests for successful chunks, model/digest mismatch, first-output silence, idle silence, total cap despite heartbeats, poll outage, gap, duplicate status, and late terminal result.
- [x] Run focused tests and confirm failures.
- [x] Implement polling with local monotonic deadlines and short request timeouts. Emit status and output events without interpreting agent health as progress. Return uncertain or denied results through existing fallback and capacity rules.
- [x] Run focused tests and the existing Qwen adapter suites; confirm no direct-route regression. The broad Claude runner tests have two preexisting process-termination failures.

### Task 3: End-to-end capture and dashboard status

**Files:** Modify `service/queries/history.py`, relevant dashboard contracts/components, and integration tests under `tests/integration/`; add one browser or query-level check if the UI mapping changes.

**Interfaces:** Existing work-item detail includes per-Qwen `agent_state`, `agent_reachable`, `last_model_output_at`, and local timeout reason derived from durable events. The grouped execution view shows these separately.

- [x] Write failing integration tests for status event persistence, prompt/response hashes, fallback linkage and uncertain capacity after a silent job.
- [x] Add a synthetic agent run against the bridge adapter; update query and dashboard presentation without changing routed/not-routed or takeover formulas.
- [x] Run integration tests, dashboard typecheck/build and one synthetic stalled-job scenario.

### Task 4: Controlled Skynet rollout

**Files:** Add `config/pilot-policy.skynet-agent-proposal.toml`, deployment instructions in `docs/operations/runbook.md`, and results in `docs/verification/skynet-task-status.md`.

**Interfaces:** The live agent starts from a project-built package copied to Skynet. Its listener accepts only the bridge host and the configured bearer token. The existing direct route is retained for rollback.

- [ ] Validate the reviewed policy hash and a separate Skynet agent configuration; keep Qwen admission gated.
- [x] Start the agent on Skynet, verify authenticated health and pinned model without sending inference, and test a fake or bounded live task only after the status path passes locally.
- [ ] Compare agent receipt, bridge events, PostgreSQL content and dashboard state. Confirm zero unexpected held slots; any uncertain slot stays held pending reconciliation.
- [x] Document the exact activation, rollback and evidence. Do not enable default Qwen routing merely because the agent responds.
