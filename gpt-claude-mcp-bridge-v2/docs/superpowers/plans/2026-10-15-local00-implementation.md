# LOCAL-00 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend primary bridge v2 with one optional, explicitly selected LOCAL-00 worker tool, preserving v1 as a manual fallback only.

**Architecture:** Add a separate `bridge/local_worker` package for strict contracts, shared durable state, bounded HTTP, prompt budgeting, and supervised task transitions. Register one new MCP tool and start discovery in the background without coupling worker failures to the existing architect service. Serialize local inference across cooperating processes under one stable worker lock and preserve uncertain outcomes across restarts.

**Tech Stack:** Windows 11, Python 3.13, existing locked `mcp==1.30.0`, Pydantic, AnyIO, stdlib HTTP/subprocess/JSON, and `unittest`. Use the existing pinned environment; no new dependency is planned.

**Spec:** [Approved LOCAL-00 specification](../specs/2026-10-15-local00-design.md), `C:\AI\gpt-claude-mcp-bridge-v2\docs\superpowers\specs\2026-10-15-local00-design.md`. Read the complete specification and supplied handoff before execution. The specification was approved in this chat on 2026-10-15.

## Global Constraints

- V2 is the primary bridge. V1 is a manually selected fallback only. Do not modify or automatically invoke v1, change its registration/state, or route LOCAL-00 failure to it.
- Preserve the names, descriptions, and input/output schemas of `consult_architect` and `review_implementation`. Add exactly one MCP tool, `local_worker`; the Codex routing list still contains only the original two tools.
- Worker: `LOCAL-00`; preferred URL: `http://skynet.local:11434`; initial IP candidate: `192.168.0.78`; fixed HTTP port: `11434`.
- Model: `qwen2.5-coder:14b-instruct-q4_K_M`; expected digest: `9ec8897f747e246e970bc5cfdda85d22f1123dc2e3d34978a010a75968716849`.
- Exact generation settings: `num_ctx=4096`, `num_predict=1024`, `temperature=0`, `stream=false`, `keep_alive=10m`. No alternate model, model pull/update, unload, raw mode, system override, LAN/service change, or hidden generation retry.
- Input plus all normal template overhead must have a proven bound of at most `3072` tokens. Reserve `1024` output tokens. Unverified budgeting prevents inference.
- Discovery timeout: `5` seconds per candidate, configurable from `1`–`15`. Generation timeout: `180` seconds, configurable from `30`–`900`. Validate finite numbers strictly. Worker-lock wait: `0`. Discovery body limit: `1 MiB`; generation body limit: `4 MiB`.
- Shared worker root: `%USERPROFILE%\.gpt-architect-bridge-local00`, independent of v2 state overrides, endpoint aliases, repositories, and sessions. Coordination covers cooperating processes on this host/account; the pilot excludes other inference clients.
- Actions: `status`, `reconnect`, `continue_without`, `generate`, `record_validation`. Generation states: `draft_returned`, `unavailable`, `busy`, `incomplete_error`; control state: `status_returned`.
- Keep availability (`discovering`, `available`, `unavailable`, `disabled`), coordination (`idle`, `busy`, `uncertain`), and validation (`pending`, `accepted`, `failed`, `unverifiable`) separate.
- Eligible categories: `isolated_repair`, `mechanical_transformation`, `supplied_code_explanation`. Require supplied context, supervisor-owned acceptance criteria, source authorization, and explicit approval for full request/response audit storage.
- Independent compilation plus executable behaviour checks are required for code changes. Explanations require independent source/documentation checks. Permit one failure-backed feedback retry only. Supervisor edits never count as worker success.
- An uncertain POST blocks further generation until deliberate operator resolution. Discovery, elapsed time, and `/api/ps` do not resolve it. Never replay generation to repair logging or transport failures.
- Tests use an isolated home and synthetic material, default to no Skynet connection, and do not call a premium model. MCP stdout remains JSON-RPC only. No credentials are read into logs.

## Review Focus

1. A delayed startup result must not re-enable a session after the user chose continue-without; Task 5 tests session revision ordering.
2. Two concurrent deliveries of the same task/attempt must produce one POST and stable replay after a restart; Tasks 3 and 7 test the transaction and actual POST count.
3. A helper that stalls or emits malformed/oversized protocol output must be reaped without blocking MCP or losing POST uncertainty; Task 4 tests the parent/helper boundary.
4. A caller repeating a task ID with different source, approval, criteria, or result evidence must get a conflict rather than fresh inference or acceptance; Tasks 2 and 8 test immutable identity.
5. An endpoint/template change between discovery, budgeting, and POST must invalidate stale preparation; Tasks 6 and 7 test identity fingerprints and rechecks under the worker lock.

## Workspace and checkpoint rules

Project root for all relative paths and commands below: `C:\AI\gpt-claude-mcp-bridge-v2`. Python command prefix: `.\.venv\Scripts\python.exe -B`. Read any newly applicable AGENTS.md before execution.

The inspected project directory has no `.git`. Do not invent a Git history, initialize a repository, or claim a commit. At execution start, recheck `git rev-parse --show-toplevel` and `git status --short` if Git becomes available. Use an existing suitable isolated checkout when applicable; create a worktree only through the applicable worktree workflow and with the correct approved source state. For the current plain directory, take a source baseline and use reviewable file diffs/checkpoints. Keep the approved specification and this plan alongside the working source.

Task 1 creates `docs/evidence/local00/` and a protected-files baseline. At each task's checkpoint, run `tools/record_evidence.py fingerprint` and copy `docs/evidence/source_fingerprint.json` to a unique `docs/evidence/local00/task-NN-source.json`; preserve focused test output and a diff against the source baseline. Do not overwrite earlier task records. If a real Git checkout is present at execution time, commit only the task's reviewed files after its checks pass; proposed messages appear in each task. Do not commit runtime audit content or user state.

## File structure and interfaces

All listed package and test files are new unless marked existing. Keep implementation changes inside v2.

| File | Responsibility |
| --- | --- |
| `bridge/local_worker/__init__.py` | Package marker; no discovery, state creation, or inference during import. |
| `bridge/local_worker/models.py` | Strict requests, task/evidence models, result and persisted-record contracts. |
| `bridge/local_worker/settings.py` | Immutable baseline constants, address parsing, shared-root/config validation. |
| `bridge/local_worker/storage.py` | Worker lock, bounded atomic records, task ledger, active marker and recovery transactions. |
| `bridge/local_worker/transport.py` | Async parent, total deadlines, pipe bounds, cancellation and delivery-state classification. |
| `bridge/local_worker/http_worker.py` | Standalone stdlib helper for one HTTP exchange; no subprocesses or repository reads. |
| `bridge/local_worker/prompts.py` | Versioned packet rendering, baseline proof, conservative token upper bounds. |
| `bridge/local_worker/audit.py` | Approved content audit, separate metadata and measured intervals. |
| `bridge/local_worker/generation.py` | Locked one-POST transaction and completion checks. |
| `bridge/local_worker/validation.py` | Supervisor evidence checks, verdict events, retry eligibility. |
| `bridge/local_worker/service.py` | Per-process session control, discovery and action dispatch. |
| `bridge/local_worker/admin.py` | LOCAL-00 CLI handlers and operator uncertainty resolution. |
| `bridge/server.py` existing | One new tool and background lifespan; original handlers remain compatible. |
| `bridge/admin.py` existing | Register a separate `local-worker` CLI command group. |
| `tools/record_evidence.py` existing | Recursive package/fixture staging and fingerprints. |
| `tools/make_verification_prompt.py` existing | Include the nested worker package in review inventory and diffs. |
| `tools/local_worker_live_check.py` | Explicit synthetic live check; never run by test discovery. |
| `tests/local_worker_helpers.py` | Synthetic packets, disposable server, helper processes and controllable clock/fault fixtures. |
| `tests/local_worker_server.py` | Test-only stdio launcher injecting the synthetic worker service; existing stdio tests use it to avoid startup LAN calls. |
| `tests/test_local_worker_*.py` | Focused contracts, storage, transport, budget, service, validation, admin, protocol, and provenance tests. |
| `tests/test_server_admin.py` existing | Three-tool list and original-two schema preservation. |
| `README.md`, `docs/LOCAL00_OPERATIONS.md` | Current v2 usage, operating policy, recovery and fallback instructions. |

Persisted state uses schema version 1: `config.json`, `snapshot.json`, `worker.lock`, `active.json`, hashed `tasks/<task-key>.json`, append-only validation events in task records, and `audit/<request-id>/request.json`, `response.json`, `metadata.json`. Store the original response bytes as base64 inside `response.json`, with parsed fields and explicit truncation metadata. Snapshot availability describes shared reachability; session `disabled` state is overlaid by `WorkerService`, never persisted as another session's disable decision.

Use canonical JSON with sorted keys and compact separators for packet/result hashes. Keep source-bearing diagnostic/evidence text in approved audit/task records, not ordinary architect telemetry. Caller references are opaque strings; the adapter never follows file paths or URLs in a packet.

## Task 1: Make evidence cover the nested package

**Files:** Modify `tools/record_evidence.py::SOURCE_GLOBS/source_files/cmd_tests` and `tools/make_verification_prompt.py` nested-file inventory/diff enumeration; create `tests/test_local_worker_provenance.py`.

**Interfaces:** Preserve `source_files(root: Path = ROOT) -> list[Path]` and `fingerprint(root: Path = ROOT) -> dict`. Change bridge/test Python discovery to recursive patterns; include recursive JSON fixtures. Exclude `.venv`, baselines, caches and runtime state. New `tests/local_worker_helpers.py` and standalone HTTP helper must be fingerprinted/staged.

Assertion anchor, with `self.root` a disposable source tree containing the nested file:

```python
def test_nested_source_change_invalidates_fingerprint(self):
    before = fingerprint(self.root)["combined_sha256"]
    (self.root / "bridge/local_worker/transport.py").write_text("CHANGED = True\n")
    self.assertNotEqual(before, fingerprint(self.root)["combined_sha256"])
```

- [x] **Step 1:** Save a fresh v1 manifest with `tools/v1_manifest.py snapshot`, snapshot v2 source/config fixtures, and store only structural registration summaries needed to prove names/commands unchanged. Keep credentials out of content snapshots. Capture current lock verification and source fingerprints; source-only baseline capture does not call an architect tool.
- [x] **Step 2:** Write failing provenance tests using a temporary tree containing `bridge/local_worker/transport.py` and `tests/fixtures/local_worker/tags.json`. Assert both appear in `source_files`; modifying either changes `fingerprint()['combined_sha256']`; excluded runtime files do not. Stage a disposable package and assert its imports work from the staged tree. Add an assertion that generated review inventory includes nested code.
- [x] **Step 3:** Run `.\.venv\Scripts\python.exe -B -m unittest tests.test_local_worker_provenance -v`; expect failures identifying omitted nested files before the fix.
- [x] **Step 4:** Update recursive discovery and review inventory, preserving deterministic sorting/deduplication and existing provenance checks. Update `cmd_tests` evidence wording to describe model-free local HTTP/process tests as well as the fake Codex CLI.
- [x] **Step 5:** Repeat the focused command; expect `OK`, including staged fingerprint equality and nested-module import. Record checkpoint 01; proposed commit: `test: include nested local worker files in evidence`.

## Task 2: Define strict contracts and validated worker settings

**Files:** Create `bridge/local_worker/__init__.py`, `models.py`, `settings.py`, `tests/local_worker_helpers.py`, `tests/test_local_worker_models.py`.

**Interfaces produced:**

- `LocalWorkerRequest`: action-discriminated union of `StatusRequest`, `ReconnectRequest`, `ContinueWithoutRequest`, `GenerateRequest`, `RecordValidationRequest`; validate with a shared `TypeAdapter`.
- `LocalWorkerResult`: the envelope from the approved spec, with typed action/state/availability/coordination/validation, optional generation fields, `next_actions`, audit references, and `replayed_from_ledger`.
- `TaskPacket`, `AcceptanceCheck`, `SourceApproval`, `ValidationReport`, `MeasuredEffort`, `TaskRecord`, `AttemptRecord`, `WorkerSnapshot`, `ActiveRequest`, `BudgetProof`, `PreparedPrompt`, `TransportRequest`, `TransportOutcome`, and `Endpoint`: strict models shared by later tasks.
- `parse_address(value: str) -> Endpoint`; `worker_root(env: Mapping[str, str]) -> Path`; `load_worker_settings(root: Path) -> WorkerSettings`; `canonical_packet_hash(packet: TaskPacket) -> str`.

`Endpoint` exposes validated `host` and canonical `base_url`; the actual peer belongs to `TransportOutcome`. Define `WorkerError(code: str)` for local failures; service dispatch converts it to a local result without raising an architect configuration error. `TaskRecord` stores attempt results and validation-event history; `WorkerSnapshot` stores reachability, coordination and any persistent budget block. IDs/hashes link these records without embedding source in the snapshot.

```python
def test_model_settings_and_address_are_fixed(self):
    self.assertEqual(parse_address(" skynet.local ").base_url, "http://skynet.local:11434")
    self.assertEqual(parse_address("2001:db8::78").base_url, "http://[2001:db8::78]:11434")
    with self.assertRaises(WorkerError):
        parse_address("skynet.local:11434")
```

Contract decisions: `schema_version` defaults to integer `1`, rejecting boolean values; unknown fields are forbidden. Attempt is strict integer `1 | 2`. Task/check IDs obey the spec's 64-character ASCII rule. Cap a request at 128 KiB serialized UTF-8, each source/context text field at 48 KiB, other text fields at 8 KiB, check/reference collections at 32 entries, and config JSON at 16 KiB. These input limits do not replace the stricter token budget. `TaskPacket` contains immutable contract/context/approval fields; preparation/review durations are action metadata outside its hash. `SourceApproval` includes classification, approval reference, and `audit_content_approved: true` for generation. `ValidationReport` includes packet/result hashes, per-check observations, exact diagnostics, evidence references, supervisor edits, and measured effort.

- [x] **Step 1:** Write tests rejecting booleans for attempts/version/metrics, nonfinite timeouts, extra fields, out-of-category tasks, missing approvals/context/checks, overlong inputs and action-inappropriate fields. Pin the exact baseline constants and timeout/body bounds. Test that changing source, criteria, versions or approval changes the packet hash, while separately reported effort does not.
- [x] **Step 2:** Add address assertions, including `parse_address(' skynet.local ').host == 'skynet.local'` and bracketed URL rendering for `2001:db8::78`. Reject `http://skynet.local`, `skynet.local:11434`, `0.0.0.0`, `::`, `127.0.0.1`, `::1`, multicast, credentials, paths and scoped IPv6. Restrict DNS input to ASCII labels; IPv6 brackets may enclose a literal only.
- [x] **Step 3:** Run `.\.venv\Scripts\python.exe -B -m unittest tests.test_local_worker_models -v`; expect missing-contract failures. Implement the models, constants, address validation and bounded config loader using installed Pydantic/stdlib; imports perform no I/O.
- [x] **Step 4:** Repeat the command; expect `OK`. Assert two environments with different `GPT_ARCHITECT_V2_STATE_DIR` values select the same LOCAL-00 root. Assert corrupt worker settings yield a worker-specific failure rather than touching existing `bridge.config`.
- [x] **Step 5:** Record checkpoint 02; proposed commit: `feat: define strict local worker contracts and settings`.

## Task 3: Implement shared durable coordination and idempotent records

**Files:** Create `bridge/local_worker/storage.py`, `tests/test_local_worker_storage.py`; extend synthetic helpers.

**Consumes:** Task 2 persisted models; existing `state.FileLock` and `state.atomic_write_text`.

**Interfaces produced:** `WorkerStore(root: Path, repo_roots: tuple[Path, ...], home: Path)`; synchronous `try_lock() -> WorkerLease` as a context manager; `read_snapshot() -> WorkerSnapshot`; `read_task(task_id: str) -> TaskRecord | None`; `begin_attempt(request: GenerateRequest, prepared: PreparedPrompt, request_id: str) -> AttemptRecord`; `finish_attempt(request_id: str, result: LocalWorkerResult) -> None`; `mark_uncertain(request_id: str, code: str) -> None`; `append_validation(report: ValidationReport) -> TaskRecord`; `resolve_uncertain(request_id: str, confirmed_idle: bool, reason: str, evidence_ref: str) -> None`. All mutators require the lease. Async callers offload bounded storage work without abandoning a thread holding a lease.

The test setup gives `self.store` and `self.other_store` the same worker root:

```python
def test_second_holder_is_busy_without_waiting(self):
    with self.store.try_lock():
        with self.assertRaises(WorkerError) as caught:
            with self.other_store.try_lock():
                self.fail("two holders entered the worker critical section")
    self.assertEqual(caught.exception.code, "worker_busy")
```

- [x] **Step 1:** Write tests for atomic snapshot/ledger writes, hashed filenames, first-use schema establishment, corrupt/truncated/oversized records, missing established ledger, and rejected symlink/junction/reparse-point or repository/v1 overlaps. Establish a worker-instance witness and task index: deleting an indexed task is corruption, not permission to regenerate it.
- [x] **Step 2:** Add two-real-process tests: distinct v2 state overrides and hostname/IP aliases contend for one worker lock; a second holder receives busy immediately. An active marker survives holder death and prevents a new attempt despite OS lock release.
- [x] **Step 3:** Add fault injection at every transaction boundary. Persist task intent and active marker before any POST eligibility; finalize the result before retiring the marker. A committed terminal result plus leftover marker can be reported from the ledger but requires reconciliation before unrelated generation. Duplicate task/attempt returns the same result; conflicting packet hash refuses.
- [x] **Step 4:** Run `.\.venv\Scripts\python.exe -B -m unittest tests.test_local_worker_storage -v` red, implement the store with bounded atomic records, then repeat green. Canonical paths are checked before each state access; no silent repair/reset of an established corrupt store.
- [x] **Step 5:** Record checkpoint 03; proposed commit: `feat: persist shared local worker coordination and task ledger`.

## Task 4: Bound DNS and HTTP with an owned helper process

**Files:** Create `bridge/local_worker/transport.py`, `http_worker.py`, `tests/test_local_worker_transport.py`; extend disposable-server/process helpers.

**Interfaces produced:** `WorkerTransport.exchange(request: TransportRequest) -> TransportOutcome` is async. Request fields include endpoint, allowlisted method/path, optional JSON body, total deadline seconds and response-byte limit. Outcome includes request phase (`not_sent`, `may_have_started`, `terminal_received`), status, peer IP, bounded retained bytes, parsed JSON, timing, truncation and error code. Launch `sys.executable -I -B <absolute http_worker.py>` with pipe-only input/output, an absolute script path, no shell, no visible Windows window, and no child processes inside the helper.

The helper uses stdlib `socket`/`http.client` for exactly one request. Emit a versioned bounded JSON response over its private stdout; the parent never forwards it to MCP stdout. The parent enforces the total deadline across spawn, stdin transfer, DNS, connect, headers, body, parse and response transfer, terminates/reaps the helper on cancellation/timeout, and bounds stderr. Invalid or missing helper output after possible POST is uncertain. Avoid timeout-created abandoned threads. No environment proxies, redirects or transport retries. Preserve the validated hostname for Host and the connected peer for persistence; reject disallowed resolved peer addresses too.

Constructor-only test dependencies can replace the helper executable and dial target/port and permit the disposable server's loopback peer. Production constructs the fixed helper, port and peer policy; no tool field, config key or environment variable exposes these test injections. The test helper reports an external `generation_count` so tests can assert actual POST count.

For a fixture helper stalled in DNS and a request deadline of 0.25 seconds:

```python
async def test_dns_stall_obeys_total_deadline(self):
    started = time.monotonic()
    result = await self.transport.exchange(self.slow_dns_request)
    self.assertEqual(result.code, "transport_timeout")
    self.assertLess(time.monotonic() - started, 1.25)
```

- [x] **Step 1:** Write tests proving exact GET/POST bodies and fixed port, actual peer reporting, no proxy usage with hostile `HTTP_PROXY`/`ALL_PROXY`, no redirect following, and no second request on failure. A production request allowlist permits only GET `/api/tags`, GET `/api/ps`, POST `/api/show`, and POST `/api/generate`.
- [x] **Step 2:** Test real child fixtures stalled in DNS, connection, headers and trickled bodies; measure that each finishes within its injected deadline plus a documented 1-second cleanup allowance. Repeated timeouts leave no live child/abandoned resolver thread. Fault scripts are injected by tests at construction, never selected through production environment variables or request data.
- [x] **Step 3:** Test cancellation and helper crashes before/after possible POST, oversized stdout/stderr, malformed JSON, body overflows and failed helper startup. Parent-side framing has a fixed bound accommodating base64 expansion of the 4 MiB response. Treat an unknown delivery phase as `may_have_started` for generation.
- [x] **Step 4:** Run `.\.venv\Scripts\python.exe -B -m unittest tests.test_local_worker_transport -v` red, implement the helper/parent, then repeat green. Capture full-body terminal evidence separately from transport shutdown; 4xx/5xx alone do not prove generation ended.
- [x] **Step 5:** Record checkpoint 04; proposed commit: `feat: bound local worker transport and preserve delivery uncertainty`.

## Task 5: Implement discovery and session control without startup blocking

**Files:** Create `bridge/local_worker/service.py`, `tests/test_local_worker_service.py`.

**Consumes:** `WorkerStore`, `WorkerTransport`, validated settings and request/result models.

**Interfaces produced:** `WorkerService(env, store, transport)`; `async discover(address: str | None = None) -> LocalWorkerResult`; `async handle(request: LocalWorkerRequest) -> LocalWorkerResult`; `async startup() -> None`; `async aclose() -> None`. Later tasks add generation/validation collaborators to the constructor. Construction/import is side-effect free; startup discovery is an explicit background call.

Test setup starts discovery behind an event and exposes its task/release event:

```python
async def test_late_startup_cannot_undo_continue_without(self):
    await self.service.handle(ContinueWithoutRequest(action="continue_without"))
    self.discovery_release.set()
    await self.discovery_task
    status = await self.service.handle(StatusRequest(action="status"))
    self.assertEqual(status.availability, "disabled")
```

- [x] **Step 1:** Write discovery tests for hostname success, saved-IP success after hostname failure, no candidate, both failures, entered address success/failure, duplicate/missing model, digest mismatch, malformed tags, and config write failure. Assert preferred hostname remains fixed and only the actual validated peer becomes the saved IP. Each candidate shares one five-second budget across all resolved addresses.
- [x] **Step 2:** Assert `status` does not connect, `continue_without` is session-local, failed generation never triggers discovery, and uncertainty survives successful reconnect. Corrupt worker state returns worker unavailable without constructing/failing `ArchitectService` or touching architect registry locks.
- [x] **Step 3:** Add controlled race tests: start discovery at revision 0, disable at revision 1, then complete discovery; assert availability remains disabled for that session. Explicit successful reconnect at revision 2 can re-enable it. Stale concurrent reconnect completions cannot override a newer control revision or newer successful peer persistence; persist a connection revision under the worker lock.
- [x] **Step 4:** Run `.\.venv\Scripts\python.exe -B -m unittest tests.test_local_worker_service -v` red, implement control dispatch/discovery, then repeat green. Return the exact recovery/continue strings from the approved spec with structured choices; do not read stdin or print.
- [x] **Step 5:** Record checkpoint 05; proposed commit: `feat: add optional worker discovery and session recovery controls`.

## Task 6: Render focused prompts with a verified conservative budget

**Files:** Create `bridge/local_worker/prompts.py`, `tests/test_local_worker_budget.py`, `tests/fixtures/local_worker/baseline_metadata.json`, `docs/LOCAL00_BUDGET_PROOF.md`.

**Interfaces produced:** `render_task(packet: TaskPacket, feedback: ValidationReport | None, prior_output: str | None) -> str`; `verify_baseline(metadata: dict, server_version: str, source_evidence: dict) -> BudgetProof`; `PromptBudgeter.prepare(packet: TaskPacket, proof: BudgetProof, feedback: ValidationReport | None = None, prior_output: str | None = None) -> PreparedPrompt`. PreparedPrompt contains exact prompt text, its hash, template version, proof fingerprint and input-token upper bound. Unknown/unproved templates return `budget_unverifiable` before generation.

Choose the spec's conservative-bound path first, avoiding a new tokenizer dependency/download. Read baseline metadata with bounded POST `/api/show` and bind the proof to the validated model digest, exact template/default-system hashes, tokenizer/pretokenizer identifiers and the observed Ollama server version. Server version capture is a bounded GET `/api/version` in the explicit metadata-readiness/live-check workflow; add that one metadata path to Task 4's internal allowlist when implementing this task. It is not another generation endpoint.

Load approved proof inputs from reviewed adapter artifacts, never task-supplied source or a caller-provided proof assertion. `BudgetProof` includes the bound method, supported tokenizer/render path, runtime/model identities, artifact hashes and provenance; `PreparedPrompt` binds that proof to this exact packet/prompt. Corrected reviewed proof evidence can clear a budget block under the worker lock; reconnect alone cannot.

With a synthetic verified proof and a packet whose complete bound is 3073:

```python
def test_complete_prompt_over_budget_never_prepares(self):
    with self.assertRaises(WorkerError) as caught:
        self.budgeter.prepare(self.over_budget_packet, self.proof)
    self.assertEqual(caught.exception.code, "context_budget_exceeded")
```

Inspect the installed baseline's single-prompt render path and the corresponding official tokenizer/template implementation. Implement a dedicated pure renderer for that exact verified path, including system/default text, BOS/EOS and assistant prefix; reject unsupported template constructs or normalizers. Count the UTF-8 bytes of all ordinary rendered text plus a proven bound for special tokens. Document why the verified byte-level BPE path cannot emit more tokens than that bound; do not implement a general Go-template interpreter or guess a characters/token ratio. Preserve exact template metadata and source-revision references as proof evidence. If this baseline cannot be proved with the available metadata, record the specific missing evidence and keep generation unavailable; obtaining a matching offline tokenizer then requires a documented plan amendment, not a silent dependency/model change.

- [x] **Step 1:** Capture/prepare the small baseline metadata fixture and golden render using read-only metadata only. Record actual hashes, version and official-source revision when observed; do not fabricate placeholders or use a different model's template as proof. Fixtures can be synthetic for unit mechanics, but cannot establish real-baseline readiness.
- [x] **Step 2:** Write prompt tests preserving all constraints, approved source and source labels; worker asks `NEEDS_CONTEXT:` instead of inventing context. Assert retry keeps the original packet/checks and includes the exact failed diagnostic and relevant prior answer. Never send the entire handoff.
- [x] **Step 3:** Add tests at upper bounds 3072/3073; Unicode, combining marks, long identifiers, code containing template delimiters/special-token text, absent/changed system metadata, unknown tokenizer/template, stale proof and retry overflow. Assert unsupported or oversized input causes zero generation requests. Exact counts are labelled only when actually exact; otherwise report an upper bound.
- [x] **Step 4:** Run `.\.venv\Scripts\python.exe -B -m unittest tests.test_local_worker_budget -v` red, implement versioned prompts/proof/bound, then repeat green. Include the conservative derivation and golden render references in `docs/LOCAL00_BUDGET_PROOF.md`.
- [x] **Step 5:** Record checkpoint 06, including whether real-baseline proof is established or explicitly unavailable; proposed commit: `feat: budget focused local prompts against the verified baseline`.

## Task 7: Deliver one audited generation transaction

**Files:** Create `bridge/local_worker/audit.py`, `generation.py`, `tests/test_local_worker_generation.py`; extend `service.py`.

**Consumes:** Tasks 2–6 contracts, store/lease, transport and prepared prompt/proof.

**Interfaces produced:** `AuditWriter(store)` with `write_request(request_id: str, request: dict, packet: TaskPacket, prepared: PreparedPrompt) -> str`, `write_response(request_id: str, outcome: TransportOutcome) -> str`, and `write_metadata(request_id: str, metadata: dict) -> None`; `GenerationRunner(store, transport, budgeter, audit)` with `async run(request: GenerateRequest, endpoint: Endpoint, proof: BudgetProof) -> LocalWorkerResult`. Service delegates generate only when the session is enabled and discovery is available.

```python
async def test_duplicate_attempt_returns_record_without_second_post(self):
    first = await self.runner.run(self.request, self.endpoint, self.proof)
    second = await self.runner.run(self.request, self.endpoint, self.proof)
    self.assertEqual(self.http_server.generation_count, 1)
    self.assertEqual(first.result_digest, second.result_digest)
    self.assertTrue(second.replayed_from_ledger)
```

- [x] **Step 1:** Write tests for a successful synthetic draft, independent validation still pending, exact serialized model settings, no raw/system field, and captured request/response audit. Required `prompt_eval_count` must be a nonnegative integer; optional metrics are null if absent and reject invalid present values. Test nanosecond-to-second conversion using `1_000_000_000 -> 1.0`.
- [x] **Step 2:** Exercise lock/check order with spies: preliminary packet/budget, lock, ledger/uncertainty, identity/proof recheck, tags, ps, persisted request/active marker, one POST, response audit, terminal ledger, marker retirement. Refuse another resident model or same-name wrong digest. Revalidate current template/proof metadata under the worker lock before generation; detect endpoint/version/template drift and return unavailable rather than use a stale prompt. This narrows drift among cooperating clients; it cannot authenticate a server or prevent external model changes after the check.
- [x] **Step 3:** Test all completion failures: wrong model, non-200, malformed response, `done=false`, boolean metrics, empty text, missing/unknown/length reason, missing prompt count, count exceeding the bound, and `NEEDS_CONTEXT:` output. Require `done=true` and `done_reason='stop'` for a completed draft. Truncation or an excessive prompt count records failure; a prompt-bound contradiction persists a budget-block flag shared across sessions until corrected proof evidence is established. Clarification remains unaccepted and cannot unlock retry.
- [x] **Step 4:** Test audit approval/refusal and write failures before/after POST, dropped responses, cancellation, hard kill, duplicate deliveries, and restart. Assert at most one POST per task/attempt; replay returns its ledger result. Complete output-limit responses end that request but remain unsuccessful. Ambiguous transport/persistence leaves the active marker/uncertainty and never launches a duplicate.
- [x] **Step 5:** Run `.\.venv\Scripts\python.exe -B -m unittest tests.test_local_worker_generation -v` red, implement transaction/audit/completion, then repeat green. Audit output is retained without automatic deletion; metadata keeps unknown human time/cost null and excludes content from architect telemetry.
- [x] **Step 6:** Record checkpoint 07; proposed commit: `feat: obtain audited local drafts with durable one-request semantics`.

## Task 8: Enforce supervisor validation and one feedback retry

**Files:** Create `bridge/local_worker/validation.py`, `tests/test_local_worker_validation.py`; extend `service.py`, `generation.py` as necessary.

**Interfaces produced:** `ValidationRecorder(store)` with `record(report: ValidationReport) -> LocalWorkerResult`; pure `retry_feedback(task: TaskRecord, request: GenerateRequest) -> ValidationReport | None`. Generation calls the latter while holding the lease. It returns none for a valid first attempt, the exact recorded failure for an eligible second attempt, and a typed refusal for all other transitions.

```python
def test_compilation_alone_cannot_accept_a_code_change(self):
    with self.assertRaises(WorkerError) as caught:
        self.recorder.record(self.compiler_only_acceptance_report)
    self.assertEqual(caught.exception.code, "validation_evidence_incomplete")
```

- [x] **Step 1:** Write tests accepting only exact packet/result/check identities. Code acceptance requires distinct passing compilation and executable behaviour evidence for all required checks, with applicable toolchain/version references; compiler-only evidence is rejected. Explanation acceptance requires claim/source evidence. Checks remain supervisor-reported; no command/path/URL is executed or opened.
- [x] **Step 2:** Test pending-to-accepted/failed/unverifiable, append-only corrections, identical report replay, and changed/missing evidence. A supervisor-edited accepted result is recorded as a correction and cannot set first-pass worker acceptance. Missing toolchain/context or clarification escalates; model confidence is never evidence.
- [x] **Step 3:** Test attempt 2 requires attempt 1's completed draft and recorded exact failed check. Preserve packet hash and original checks, re-budget combined feedback, reject attempt 3, retry after acceptance, retry on unverifiable/transport failure, and conflicting task IDs. Restart or revised validation history cannot restore a spent retry.
- [x] **Step 4:** Run `.\.venv\Scripts\python.exe -B -m unittest tests.test_local_worker_validation tests.test_local_worker_generation -v` red, implement the recorder and retry predicate, then repeat green. Record first-pass/retry outcomes, supervisor edits, measured intervals, escalation and optional human time with its provenance.
- [x] **Step 5:** Record checkpoint 08; proposed commit: `feat: enforce supervisor evidence and a single local feedback retry`.

## Task 9: Add deliberate local administration and uncertainty recovery

**Files:** Create `bridge/local_worker/admin.py`, `tests/test_local_worker_admin.py`; modify `bridge/admin.py::build_parser` to register the command group without running architect `_context`/CLI auth/catalog checks.

**Interfaces produced:** `register_local_worker_commands(subparsers) -> None`; `cmd_local_status(args, env) -> int`; `cmd_local_reconnect(args, env) -> int`; `cmd_local_resolve_uncertain(args, env) -> int`.

Use exit 0 for completed administration, 2 for missing operator acknowledgement/invalid usage, and 1 for a worker-state/transport refusal. The fixture in this assertion has an active uncertain request:

Define `resolution_args(confirmed_idle: bool) -> argparse.Namespace` on the admin test case to supply its seeded request ID, nonempty reason and evidence reference.

```python
def test_resolution_requires_explicit_idle_acknowledgement(self):
    args = self.resolution_args(confirmed_idle=False)
    self.assertEqual(cmd_local_resolve_uncertain(args, self.env), 2)
    self.assertEqual(self.store.read_snapshot().coordination, "uncertain")
```

- [x] **Step 1:** Write parser/handler tests for `bridge_admin.py local-worker status --json`, `reconnect [--address HOST]`, and `resolve-uncertain --request-id ID --confirmed-idle --reason TEXT --evidence-ref REF`. Only this local CLI exposes operator resolution; the MCP tool has no action that clears uncertainty.
- [x] **Step 2:** Test that missing acknowledgement, empty reason/evidence, wrong/stale request ID, a held worker lock, missing marker or corrupt state refuse. A valid acknowledgement records external operator confirmation, retires the exact unresolved request and never sends generation, assumes server idleness from `/api/ps`, or resets the retry ledger. Retired uncertain attempts remain unavailable for replay/retry.
- [x] **Step 3:** Add acceptance tests showing read-only status/reconnect work independently of Codex login/architect registry configuration; neither changes v1 or makes an architect model call. Recovery is idempotent only for the same already-recorded resolution and never resolves a different marker.
- [x] **Step 4:** Run `.\.venv\Scripts\python.exe -B -m unittest tests.test_local_worker_admin -v` red, implement the group/handlers, then repeat green. CLI human output is separate from MCP stdout; the group never alters service binding or invokes a remote shell.
- [x] **Step 5:** Record checkpoint 09; proposed commit: `feat: add explicit local worker administration and recovery`.

## Task 10: Register the third MCP tool and background lifecycle

**Files:** Modify `bridge/server.py`, `tests/test_server_admin.py`; create `tests/local_worker_server.py`, `tests/test_local_worker_mcp.py` and `tests/fixtures/local_worker_tool_schema.json`.

**Consumes:** `LocalWorkerRequest`, `LocalWorkerResult`, `WorkerService.handle/startup/aclose`.

**Interfaces produced:** `async local_worker(request: LocalWorkerRequest) -> LocalWorkerResult` decorated once on the existing server; a FastMCP lifespan context schedules startup once and closes owned tasks/helpers on shutdown. Keep the original two decorators, functions/docstrings and schemas unchanged. Retain original `bridge.config.TOOL_NAMES` and architect telemetry enums; LOCAL-00 has separate records.

```python
async def test_v2_exposes_exactly_three_tools(self):
    tools = await mcp.list_tools()
    self.assertEqual(sorted(tool.name for tool in tools),
                     ["consult_architect", "local_worker", "review_implementation"])
```

- [x] **Step 1:** Change the surface test to assert exactly `consult_architect`, `local_worker`, `review_implementation`. Filter the original two tools and compare their complete schema dumps to `v1_tool_schemas.json`; apply single-required-string assertions only to those two. Review and capture a separate new-tool schema fixture rather than regenerating the old one.
- [x] **Step 2:** Add real stdio tests for all five local actions with an injected disposable transport fixture and isolated home. Production does not accept test endpoint/port overrides; `tests/local_worker_server.py` constructs the service before starting the real server. Migrate all three existing normal-entry-point stdio launches in `tests/test_server_admin.py` to that launcher so the existing full suite cannot contact Skynet during new startup discovery. Exercise entry-point import/wiring separately with the same injected factory, never a model-controlled request field.
- [x] **Step 3:** During delayed/failed discovery, a held worker lock, uncertain POST and corrupt local config, prove initialization/ping and both architect calls continue through the fake CLI. Test exact recovery choices, structured draft results, server cancellation and shutdown helper reaping. Assert no ordinary architect answer receives repeated local prompts and every stdout line is JSON-RPC.
- [x] **Step 4:** Run `.\.venv\Scripts\python.exe -B -m unittest tests.test_server_admin tests.test_local_worker_mcp -v` red, implement lifespan/tool wiring, then repeat green. Also scan the existing suite for any remaining exact-two-tool assumption; update only surface assumptions, preserving architect regressions and v1 fixtures.
- [x] **Step 5:** Record checkpoint 10; proposed commit: `feat: expose optional local worker through the v2 MCP server`.

## Task 11: Document operation and produce current release evidence

**Files:** Update `README.md`; create `docs/LOCAL00_OPERATIONS.md`, `tools/local_worker_live_check.py`, `tests/test_local_worker_live_check.py`, and current records under `docs/evidence/local00/`. Update only the current-status notes of existing review documentation; retain historical results as historical.

**Interfaces produced:** `tools/local_worker_live_check.py::main(argv: list[str] | None = None) -> int` accepts `--metadata-only` by default and requires explicit `--generate-synthetic --source-approved --audit-approved` to generate. It uses the real service/transport through an MCP client, the fixed Skynet endpoint/profile, and a stable run/task ID. Live requests use the native user's shared LOCAL-00 root and lock; isolate the synthetic candidate repository/architect registry rather than creating a second worker lock namespace. Unit tests alone inject an isolated home. A separate mode targets the registered v2 entry point for metadata/status verification after restart; no script edits registrations or restarts other sessions silently.

```python
def test_live_generation_requires_source_and_audit_flags(self):
    with self.assertRaises(SystemExit) as caught:
        live_check.main(["--generate-synthetic"])
    self.assertEqual(caught.exception.code, 2)
```

- [x] **Step 1:** Add a live-runner test proving its default path never calls `/api/generate`, explicit synthetic flags are all required, and failure cannot launch premium inference, v1, a model pull, or a duplicate POST. Run `.\.venv\Scripts\python.exe -B -m unittest tests.test_local_worker_live_check -v` red, implement the runner, then green.
- [x] **Step 2:** Document primary v2/manual v1 usage, exact JSON action examples, background discovery/recovery text, source/audit approval semantics, shared lock scope, exclusion of external inference clients, tokenizer proof readiness, uncertain-POST recovery with operator evidence, retained local audit location, measured-time provenance, and manual activation/rollback. Rollback LOCAL-00 by disabling it or restoring the recorded v2 source baseline and restarting v2; retain uncertain markers/audit. V1 selection is a separate manual fallback for broader v2 failure.
- [x] **Step 3:** Run `.\.venv\Scripts\python.exe -B -m unittest discover -s tests -t . -p 'test_local_worker_*.py' -v`, then `.\.venv\Scripts\python.exe -B tools\verify_lock.py`. Expect `OK` and `environment matches requirements.lock.txt`. Add no dependencies unless the approved constraints require a documented amendment and regenerated hash lock.
- [x] **Step 4:** Run `.\.venv\Scripts\python.exe -B tools\record_evidence.py tests --label local00_full`. Require `OK`, no failing/skipped tests, no evidence problems, equal live/staged/staged-after/live-after hashes, and retained logs. Confirm nested package/helper/JSON fixtures appear in the source manifest. Preserve fresh results; do not present old test counts as current results.
- [x] **Step 5:** Run `.\.venv\Scripts\python.exe -B tools\v1_manifest.py compare docs\evidence\local00\protected-before.json` against Task 1's snapshot, and compare the structural registration summaries. Expect v1 source/state and registration unchanged. Attribute any unrelated app-driven Codex config/auth stat drift separately; do not conceal drift or claim it proves a bridge mutation.
- [x] **Step 6:** Run the explicit metadata-only check from the actual native v2 host, outside a network-restricted agent sandbox when required. Record hostname/IP checks separately, exact peer/model/digest, current resident models, metadata/proof readiness and client process scope. Exercise local continue/reconnect paths through the actual adapter; distinguish isolated runtime checks from the already-running registered MCP process.
- [x] **Step 7:** Once baseline proof and required source/audit authorization are established, run one explicitly selected synthetic isolated repair through the real adapter. Supply `def stable_unique(items): return sorted(set(items))` with the fixed contract: integer-list input, stable first-occurrence order, `None` and empty input return an empty list, input remains unchanged, and output is always a fresh list. In a candidate copy run `py_compile` and executable assertions for `None`, `[]`, `[2, 1, 2, 0, 1] -> [2, 1, 0]`, unchanged input and distinct result identity. Assign stable check IDs before inference and keep these same checks for a possible single failure-backed retry. No Java or proprietary source is needed. If proof, network, external-client exclusivity or validation readiness is unavailable, report that specific activation gap and leave LOCAL-00 unavailable while v2 works.
- [x] **Step 8:** Review the current source/diff and evidence against every acceptance row in the approved spec; fix material findings and rerun only affected focused checks, then refresh full evidence if source changed. Request an independent code review only within the execution method/authorization selected by the user; a subagent or another chat is not silently started by this plan.
- [x] **Step 9:** Record checkpoint 11 and a release note distinguishing implemented/tested behaviour, live observations and any activation gaps. Deliver restart instructions for clients to discover the new tool. Do not restart an in-use server or declare the registered tool available based on source tests alone. Proposed commit: `docs: document local worker operation and bind release evidence`.

## Dependency order and completion criteria

Specification coverage after plan self-review:

| Approved acceptance requirement | Owning tasks |
| --- | --- |
| Complete source/evidence coverage and original tool contracts | 1, 10, 11 |
| Strict address, source, packet, identity and metric contracts | 2, 4, 5, 7 |
| Nonblocking startup, discovery, persistence and recovery choices | 3, 5, 10 |
| Bounded DNS/HTTP, cancellation, actual peer, body limits | 4, 10 |
| Fixed generation profile and verified input/output budget | 2, 6, 7 |
| Shared serialization, crash markers and no automatic replay | 3, 4, 7, 9 |
| Completion checks, clarification and independent validation | 7, 8 |
| Durable one-retry limit, immutable criteria and supervisor corrections | 3, 6, 8 |
| Approved full audit, separate telemetry and measured effort | 2, 7, 8, 11 |
| Manual operator resolution and manual v1 fallback only | 9, 11 |
| Model-free full tests, actual-runtime observations and activation limits | 1, 10, 11 |

Execute Tasks 1–11 in order. The contracts, store and transport are prerequisites for service behaviour; generation depends on proven budgeting and durable audit; retry enforcement precedes tool exposure. Each task ends at its own green focused check and checkpoint, except an honestly reported external readiness gap must remain explicit rather than being marked green as live activation.

The software is ready for rollout when deterministic focused/full tests pass with current complete fingerprints, the original tool contracts and v1 fallback are intact, state/audit recovery is documented and tested, and live metadata/generation evidence meets the approved activation criteria. A missing real-baseline budgeting proof permits delivering a safely unavailable optional worker, but does not fulfil successful generation activation. Report implementation completion and activation readiness separately.

## Execution recommendation

**Suggested execution model: Claude Sonnet.** The tasks use established Python/MCP patterns; quality depends on the specified process, transport and persistence tests. Advisory only; the user's chosen model remains in place.

Recommend **native execution in this session** because all tasks share strict request/store/transport contracts and benefit from one implementer maintaining those interfaces. An alternative is subagent-driven execution with task-level independent reviews, at the cost of additional context and coordination. The user has approved the specification and requested this plan, but has not selected an execution method. Ask for plan review and method selection before implementation; do not spawn agents while writing the plan.

## Supporting references

The approved specification supplies the policy and exact API profile. Ollama's [official API source reference](https://github.com/ollama/ollama/blob/main/docs/api.md#show-model-information) documents `/api/show` model metadata including the template/system and verbose tokenizer fields. Treat that metadata as proof input to inspect, not evidence by itself that an arbitrary template or tokenizer is safe to count. Read the source revision matching the installed runtime when establishing the budget derivation.

Execution completed on 2026-10-15. Final full staged suite: 531 tests passed with matching source fingerprints. The real worker pilot was recorded unverifiable and escalated; supervisor correction is separate from worker success. See [the release record](../../LOCAL00_RELEASE.md) for review dispositions, the deferred minor and runtime restart scope.
