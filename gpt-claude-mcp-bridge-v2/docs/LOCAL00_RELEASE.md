# LOCAL-00 v2 implementation record

Date: 2026-10-15. Initial implementation record, followed by the current Markdown-envelope fix and verification below. V1 remains a manual fallback only.

V2 now exposes the optional `local_worker` alongside the original two architect tools. V2 is primary; v1 remains a manual fallback only. Existing registrations, original architect schemas, v1 source/state and Codex configuration/authentication are preserved. Restart the relevant v2 MCP process or use a client session that starts a fresh process to discover the new tool. No in-use server was forcibly restarted.

## Implemented behaviour

The worker supports explicit status, reconnect/address entry, continue-without, bounded generation and supervisor-reported validation. Startup discovery runs in the background. It prefers `skynet.local`, saves the actual successfully validated peer as the fallback, and validates the handoff's model/digest. Model settings remain fixed; no router/service/model changes or automatic v1 fallback occur.

One shared LOCAL-00 lock and durable task ledger serialize cooperating v2 processes. The request audit and active marker are persisted before POST. Unknown outcomes survive process death and block further generation until deliberate operator resolution. Metadata, compatibility checks and `/api/ps` cannot clear uncertainty. The adapter does not automatically execute/apply model output or run packet-supplied commands.

The installed Ollama 0.30.9 template/tokenizer baseline has a reviewed conservative UTF-8 BPE bound, including normal template/default-system overhead. Unknown identities or input exceeding the 14336-byte upper bound prevent inference, retaining the fixed 2048-token output allowance in a 16384 context. Full approved request/response audits are separate from content-free architect telemetry. Independent compiler/behaviour or source/reference evidence controls acceptance and the one permitted feedback retry.

## Verification record

Initial implementation suite result: **531 tests passed**, zero failures/skips, zero evidence problems, 605.672 seconds. Live-before, staged-before, staged-after and live-after source hashes all match `3b76e942f384c846467d9cebf62de24105c5d0ecfae782c85e4d2565f0d92b3b`.

Final dependency verification: the original 30-package hash-pinned environment matches. Protected-files comparison: v1 source, venv tripwire and session records unchanged; Codex configuration/auth stat unchanged; architect registration names/commands unchanged.

Initial source manifest: `docs/evidence/local00/final-source.json`. Initial staged suite label: `local00_full_final`, with machine-readable result and retained raw log under `docs/evidence/`. The source set includes the nested worker package, standalone HTTP helper and recursive JSON fixtures.

The untouched baseline ran 483 tests: 482 passed and one depended on the host's real sandbox-override settings file. That test now uses an isolated settings directory, preserving the production loader and actual override. An earlier intermediate suite exposed two legacy exact-two-tool/shallow-glob assertions; both were updated while retaining exact original tool-schema comparisons. Its source also changed during review fixes, so its `INVALID` provenance is not final release evidence.

Fresh native MCP processes verified the third tool, actual hostname/IP connectivity, continue/reconnect behaviour and validation reporting. Unit/integration tests use disposable transports and synthetic homes, including real stdio responsiveness, two-process locking, hard process death, corrupt metadata, DNS/HTTP deadlines, cancellation, durable replay, prompt boundaries and retry/validation transitions.

## Initial synthetic pilot outcome (historical)

Exactly one approved synthetic task was sent to Skynet: `local00-pilot-20261015-01`. The fixed request profile matched the handoff. The worker evaluated 258 input tokens against a conservative upper bound of 1150, completed normally and returned an unnecessary `NEEDS_CONTEXT:` question about non-integer inputs despite the integer-list contract.

The adapter returned the result unaccepted and did not use a feedback retry or a new task ID to force another answer. The supervisor recorded `unverifiable`, independently supplied a candidate repair, and passed `py_compile` plus the original None/empty/stable-order/no-mutation/fresh-list assertions on that separate candidate. This is a supervisor correction, not worker success, and establishes no savings or general model correctness.

The active marker is absent and the ordinary v2 workflow remains available. See `docs/evidence/local00/live-local00-pilot-20261015-01.json`, `pilot-supervisor-validation.json`, `pilot-recorded-validation.json`, `pilot-audit-check.json` and `post-review-metadata.json`. Full approved content remains in the shared worker root's audit folder.

## Independent review and fixes

One fresh-context read-only reviewer identified four Important findings and one Minor, with no Critical findings. The material findings were fixed in one test-driven pass:

- Capture reconnect intent before it queues; automatic startup cannot undo a later continue-without choice.
- Recheck selected endpoint and connection revision under the worker lock before network preparation.
- Convert malformed worker witness objects into optional-worker failures so ordinary MCP startup/architect calls remain usable.
- Keep later validation corrections authoritative when an older report is replayed, with verdict/history persisted atomically.

Every finding had a reproducing failure before correction; the related regression run passed 31 tests. The final full suite verifies the corrected source. There was no second independent review after the fix pass. Details are in `docs/evidence/local00/final-review.md`.

## Deferred minor

Replaying an already completed second attempt with a changed or missing `feedback_ref` returns the existing result instead of reporting a request conflict. It never issues another POST or restores the retry budget. A later refinement should include retry-evidence identity in cached-request conflict checks.

## Execution rulings and their limits

1. Work in the approved plain v2 directory with a baseline and per-task checkpoints because no Git repository exists. Rollback relies on file snapshots rather than commits.
2. Use direct model-free source fingerprints for per-task checkpoints rather than running unrelated Codex doctor each time. Environment evidence is collected with the final staged suite; intermediate checkpoints alone do not describe the complete runtime environment.
3. Isolate the host-dependent baseline test's settings directory. This could hide a loader regression if considered alone; separate settings-loader tests retain coverage of the real file behaviour.
4. Keep external Ollama clients and changes after final compatibility checks outside the cooperating-host lock guarantee. An external inference client can break sequential operation if the documented pilot exclusion policy is ignored.
5. Treat source/operator approval references as recorded supervisor/operator assertions. False assertions can lead to inappropriate acceptance or recovery; this adapter does not authenticate the human conversation.
6. Keep the real clarification result unverifiable and the supervisor correction separate. The observed pilot supplies no worker-success or savings evidence.
7. Resolve final-suite/release evidence in the executing workflow and distinguish fresh-server tests from already-running client processes. Source tests alone cannot establish that an old process has discovered the new tool.
8. Retain the plain-directory execution ledger and source baseline because no Git history can recover them after scratch deletion. This leaves additional local evidence files for audit and rollback.

Operating instructions: [LOCAL-00 operation](LOCAL00_OPERATIONS.md). Approved requirements: [specification](superpowers/specs/2026-10-15-local00-design.md) and [implementation plan](superpowers/plans/2026-10-15-local00-implementation.md). Execution ledger: `.superpowers/sdd/2026-10-15-local00-implementation/progress.md`.

## Markdown-envelope follow-up: current verification

The registered clamp synthetic task exposed a format defect: two raw responses contained Markdown fences despite the prose request for Python source. Manual fence removal produced correct code, but that run was recorded as supervisor-corrected.

V2 now reserves the existing `output_form` value `plain_code` for deterministic envelope handling. It removes only one complete matching outer code fence, preserves payload bytes, and rejects ambiguous, mixed, empty or incomplete envelopes. Generic output behavior, public tool schemas and legacy packet hashes are preserved. The original HTTP response stays in the content audit; a separate normalization witness binds the raw result, returned source and policy to the new result digest. Replay does not regenerate or renormalize an existing result. Code remains a draft until independent validation.

Current staged suite: **537 tests passed**, zero failures/skips/evidence problems, **609.801 seconds**. All four source fingerprints match `78a831df94c066dfa0e465fec4c186e4ec0fa80b09ba5c86d924a9575acfd164`. See `docs/evidence/tests_local00_plain_code_final.json` and its retained log. The six added regression tests cover normalization/audit/replay, unchanged generic and bare outputs, payload byte preservation, ambiguous envelopes, audit-write failure and fenced clarification.

A fresh registered `mcp__gpt_architect__local_worker` connection completed task `registered-clamp-plaincode-20261015` on attempt 1. The raw model response was fenced; the adapter normalized it. The exact tool-returned bytes compiled under Python 3.13.7 and passed the unchanged **2,547-case** independent verifier. Acceptance was recorded with empty `supervisor_edits`, no feedback retry and no manual source correction. Raw/witness digests, packet identity and returned bytes were independently checked. The reviewer found no Critical, Important or Minor issues. This synthetic result establishes no general code-correctness or savings claim.

Evidence and the source manifest are under `docs/evidence/local00/plain-code-fix/`; start with `summary.json`. V1 source is unchanged and no v1 tool was invoked. Registrations remain unchanged. Existing MCP sessions retain imported Python modules and need reconnecting to load the source update; the live verification used a fresh registered process.
