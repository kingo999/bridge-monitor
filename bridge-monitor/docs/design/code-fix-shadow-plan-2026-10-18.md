# Test-backed Code-Fix Shadow Implementation Plan

> **For agentic workers:** Implement task by task with red/green tests and verify the whole suite. This plan is executed inline under the user's existing authorization.

**Goal:** Safely measure Qwen-first versus Sonnet-direct behavior on test-backed Python fixes without changing live routing.

**Architecture:** A Docker-only verifier executes full-file candidates against prewritten inputs while expected outputs remain on the host. A matched shadow runner reuses the pinned Qwen and Sonnet profiles, records both paths and a metadata-only Codex parent proxy, and produces a reviewable go/no-go report.

**Tech Stack:** Python 3.13, Docker, existing Skynet agent, Claude CLI, Codex CLI, pytest.

**Spec:** `docs/design/code-fix-shadow-2026-10-18.md`

**Execution status:** The retrospective harness and two-case trial are complete; see `docs/verification/code-fix-shadow-2026-10-18.md`. A genuine natural-task cohort and any live canary remain pending.

## Global constraints

- Keep all new or modified project files under `C:\AI\bridge-monitor`; do not modify the original bridge.
- Do not activate a live local-first route or write shadow results into production Postgres.
- Keep routing, resource and trial limits in TOML where appropriate; enforce container security flags in code.
- Treat model output as untrusted; execute only in the isolated container.
- Label retrospective probes separately from genuine new user work.

## Review focus

- Malformed or oversized model output must never reach Docker as an accepted answer.
- Docker unavailability, timeout and unexpected exit must fail closed and preserve evidence.
- A broken baseline or broken reference must reject a case before model dispatch.
- Interrupted or changed receipts must not trigger duplicate model calls.
- Parent usage missing or provider prices unknown must remain unknown in savings calculations.

## Tasks and exit checks

### Task 1: Independent verifier

**Files:** `bridge/code_fix_verifier.py`, `config/code-fix-shadow.example.toml`, `tests/shadow/test_verifier.py`.

- [ ] Write failing tests for reference pass/baseline fail, candidate pass/fail, malformed source, timeout and unavailable Docker.
- [ ] Implement a digest-pinned, network-disabled Docker run using argument arrays, project-local temporary mounts and host-held expected values.
- [ ] Run focused tests; verify a real isolated container can execute a harmless trusted test.

### Task 2: End-to-end measurement contract

**Files:** `bridge/shadow_accounting.py`, `tests/shadow/test_accounting.py`, `scripts/run-code-fix-shadow.py`.

- [ ] Write failing tests for separate GPT, Sonnet and Qwen token categories, missing usage, fallback and interrupted receipts.
- [ ] Implement matched-arm orchestration and Codex CLI JSON usage capture with configured model and read-only sandbox.
- [ ] Validate config, record exact prompts/raw outputs/model choices/latency and hashes, and avoid re-dispatch on uncertain resume.
- [ ] Run focused tests and a no-model dry run.

### Task 3: Shadow trial and decision

**Files:** `tests/fixtures/code-fix-shadow-20261018.json`, `docs/verification/code-fix-shadow-2026-10-18.md`.

- [ ] Register two retrospective, test-backed fixes before calls; validate reference and baseline in Docker.
- [ ] Run the matched Qwen/Sonnet/GPT proxy trial and independently recompute its receipt summary.
- [ ] Obtain a read-only Claude review with before/after file and Git verification; apply justified fixes under this authorization and rerun checks.
- [ ] Run the full suite and record a canary go/no-go decision. A live canary remains off unless genuine eligible work and a reviewed activation policy exist.

**Suggested execution model: Claude Opus.** The isolation and acceptance boundary are consequential even though the shadow path is offline. Advisory; the current model stays in use.
