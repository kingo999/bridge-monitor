# LOCAL-00 optional worker specification for bridge v2

Date: 2026-10-15. Status: approved specification; implementation and final verification are recorded in `docs/LOCAL00_RELEASE.md`.

Extend `C:\AI\gpt-claude-mcp-bridge-v2` with one optional MCP tool named `local_worker`. GPT or Claude explicitly selects a small eligible task, obtains a local draft, and independently validates it. Failure, refusal, or unavailability of LOCAL-00 leaves the ordinary v2 architect workflow usable.

**V2 is the primary bridge. V1 is a manually selected fallback only.** LOCAL-00 failure never triggers an automatic switch to v1. This work does not modify the v1 source, state, or registration.

## Approved direction and governing requirements

The user approved extending v2 with the optional `local_worker` tool and requested this detailed specification. The governing functional contract is the supplied **LOCAL-00 — GPT–Claude bridge handoff**, prepared 2026-10-15, including its discovery sequence, model baseline, narrow task allowlist, independent validation, single feedback retry, and pilot measurements. The user's clarification that v1 is a fallback only supersedes older bridge documentation describing v1 as the primary live bridge.

The handoff is available at `D:\Temp\LOCAL-00-BRIDGE-HANDOFF.txt` and as the pasted attachment supplied in this chat. The design below resolves implementation choices against the existing v2 Python/MCP code. Approval of this direction authorizes specification work; runtime activation and implementation evidence are separate from this document's status.

## Existing integration points

`architect_bridge.py` imports `bridge.server`, whose FastMCP server currently exposes `consult_architect(question: str)` and `review_implementation(focus: str)`. Blocking architect work runs on a worker thread so the MCP event loop can handle protocol traffic and cancellation. `bridge.service.ArchitectService` owns the Codex workflow.

`bridge.config` routes the two architect tools to Codex models. `bridge.state` provides atomic file writes, state-directory checks, and advisory file locks. Existing architect telemetry deliberately excludes prompts, source, and answers. LOCAL-00 needs its own state and audit schema rather than weakening that telemetry contract.

The current MCP surface tests assert exactly two tools. Revise those assertions to require three tools while retaining byte-identical names, descriptions, and schemas for the original two. Keep the Codex routing tool list limited to the two architect tools; adding `local_worker` must not send it through Codex catalog validation or architect session management.

Current Codex and Claude registrations observed in this session point the primary `gpt-architect` entry at v2. Registrations are not changed by writing this specification. Historical README and review-package status statements need a targeted update when the implementation documentation is revised; historical test evidence remains historical.

## Scope and operating boundaries

The extension provides discovery, status, explicit reconnect, session-level continue-without-worker, generation, supervisor-reported validation, retry enforcement, and pilot records through one MCP tool. It supplies a local administration recovery action for an uncertain generation outcome.

The supervisor retains architecture decisions, ambiguous tasks, broad Java/Spring advice, missing-context work, and final acceptance. No local response is automatically applied to a repository. The adapter never executes acceptance commands supplied in a task packet, reads referenced source files on the supervisor's behalf, or obtains remote-shell access to Skynet.

Eligible categories are:

| Category | Required contract |
| --- | --- |
| `isolated_repair` | A small code repair with exact expected behaviour and independent compiler and executable behaviour checks. |
| `mechanical_transformation` | Explicit transformation rules and independent result checks; compiler and executable behaviour checks for code changes. |
| `supplied_code_explanation` | Sufficient supplied source and version-specific authoritative context to check all material claims independently. |

Use synthetic material or explicitly approved repository material. Proprietary/Broadcom source requires explicit authorization. A caller's approval reference records the supervisor's authorization decision; the adapter cannot independently authenticate the underlying human conversation. A repository path or source reference alone does not authorize transmission.

## Tool interface

Expose `async local_worker(request: LocalWorkerRequest) -> LocalWorkerResult`. Use strict, versioned typed models and an action discriminator. Reject unknown fields, incorrect types, and irrelevant action fields before network or file-content operations. Exactly one new tool is registered.

| Action | Required input | Behaviour |
| --- | --- | --- |
| `status` | Action only | Return current discovery, coordination, and availability state without inference or an implicit reconnect. |
| `reconnect` | Optional `address` | Run bounded discovery, or validate one explicitly entered address; never clear uncertain-generation state. |
| `continue_without` | Action only | Disable local generation for this MCP server session; return the handoff's continue message. |
| `generate` | Task packet and `attempt` 1 or 2; feedback reference for attempt 2 | Perform eligibility, authorization, context, budget, coordination, and resident-model checks; obtain at most one draft. |
| `record_validation` | Task ID, attempt, result digest, verdict, diagnostics, evidence references, and measured effort | Persist the supervisor's independently obtained validation outcome without executing commands or calling a model. |

`reconnect` is an explicit action that can re-enable a session after `continue_without`. Automatic startup discovery runs once per process. A subsequent failed generation does not silently rediscover, prompt repeatedly, or replay the request.

Give session enable/disable changes a monotonically increasing revision. A late background-discovery completion may update the validated endpoint snapshot but cannot undo a newer `continue_without` action. Only a successful explicit reconnect can re-enable that disabled session.

### Task packet

Every generation request includes:

- A caller-supplied `task_id`, category, one objective, exact behavioural constraints, concise requested output, and packet version.
- Supplied source, minimum necessary caller/diagnostic context, applicable JDK/framework versions, and authoritative excerpts with source labels when needed. Fields may be empty only where genuinely inapplicable.
- Supervisor-owned acceptance criteria, stable check IDs, and an explanation of how each check will be performed independently. The adapter treats commands as descriptive data.
- A source classification (`synthetic` or `approved_repository`), an approval reference, and explicit approval to store the full task request and response in the pilot audit.
- Context/version identifiers and optional measured preparation time. Unmeasured values are null.

Restrict externally supplied IDs to 1–64 ASCII letters, digits, dots, underscores, and hyphens, starting with a letter or digit. Use generated IDs or hashes for file names; do not join caller text into paths. Bound individual fields and total serialized input before assembling the prompt. Reject missing essential context instead of guessing it.

The first attempt establishes an immutable hash of the task packet, including acceptance criteria, context, versions, and source approval. The feedback retry retains that packet and attaches only the relevant prior output and exact failed check. Changed requirements form a new task, which the supervisor must assess as a new delegation decision rather than use to bypass the retry limit.

### Result envelope

All actions return `schema_version`, action, worker ID `LOCAL-00`, a machine-readable state/code, concise explanation, and applicable next actions. Generation and validation results additionally include task ID, packet hash, request ID, attempt, selected endpoint, actual responding peer IP, model, validated digest, result digest, and validation state.

Generation states are `draft_returned`, `unavailable`, `busy`, or `incomplete_error`. Control actions use `status_returned`. Keep availability (`discovering`, `available`, `unavailable`, `disabled`) and coordination (`idle`, `busy`, `uncertain`) as separate fields; a compatible endpoint can still be blocked by an uncertain earlier POST.

For a completed draft, preserve the response text, `done`, `done_reason`, raw Ollama token counts and durations, and measured client elapsed seconds. Preserve absent optional metrics as null. Record validation as `pending` until the supervisor reports `accepted`, `failed`, or `unverifiable`.

Validation records carry `evidence_origin: supervisor_reported`. The adapter enforces required evidence fields and state transitions; it does not claim to have executed checks. A worker clarification request remains unaccepted and returns to the supervisor. Prompt the worker to prefix such a request with `NEEDS_CONTEXT:` so common clarification output can be flagged; semantic completeness remains a supervisor decision.

## Discovery and startup

The persistent preferred name is `http://skynet.local:11434`. The initial IP candidate is `192.168.0.78`; its initial provenance is supplied configuration, not a successful validation by a running adapter.

Start discovery in the FastMCP lifespan as a background operation. MCP initialization, pings, and the two architect tools remain available while discovery runs. Local generation during discovery returns promptly with `busy` and a `discovering` code. Startup must not read interactive input from MCP stdin or print text to stdout.

1. Try the preferred hostname. Require a successful bounded HTTP response, valid `/api/tags` JSON, exactly one matching configured model, and the expected digest.
2. On failure, try the last successfully validated IP, or the initial candidate if no validated IP exists. Preserve separate candidate results and failure codes.
3. On success, keep the preferred name, record the actual peer IP used by the successful request, and atomically save that IP as the next fallback. A failed attempt never overwrites the last successful IP.
4. If both fail, mark LOCAL-00 unavailable and keep v2 running. Surface the recovery message through `status` and attempted local calls. Ordinary architect responses do not repeatedly carry this prompt.

Use the handoff's exact recovery wording:

> LOCAL-00 unavailable. Enter Skynet's hostname or LAN IP, retry discovery, or continue without the local worker.

Return choices `Enter address`, `Retry discovery`, and `Continue without LOCAL-00`, mapped respectively to `reconnect(address=...)`, `reconnect`, and `continue_without`. The calling GPT/Claude workflow can show these choices in conversation. This is the interaction surface for the existing stdio server; no new graphical UI is introduced. Noninteractive callers can simply continue using the architect tools.

For a failed entered address, return:

> LOCAL-00 could not be validated at <candidate>: <concise reason>. Enter another address, retry discovery, or continue without LOCAL-00.

For `continue_without`, return:

> Continuing with GPT/Claude; LOCAL-00 is unavailable for this session. You can reconnect later.

### Address handling

Accept a plain DNS hostname, IPv4 literal, or IPv6 literal; bracket an IPv6 literal when rendering its HTTP URL. Trim surrounding whitespace and validate the entire input. Reject schemes, explicit ports, paths, query strings, fragments, user information, embedded whitespace, wildcard/unspecified addresses, and multicast addresses. The port is always HTTP 11434. Loopback is inapplicable to this Windows-hosted Skynet worker and is rejected for this integration.

Use the validated hostname in the HTTP Host header and connect to a resolved candidate while retaining that connection's actual peer address. Do not save an unrelated resolver result. Disable environment HTTP proxies and redirects for this worker transport. Address acceptance still requires model and digest validation; digest matching is compatibility checking, not host authentication. No MAC scans or router configuration are involved.

### Timeouts and response bounds

| Setting | Default | Scope |
| --- | --- | --- |
| `discovery_timeout_s` | 5 | One candidate, including DNS, connect, body read, and parsing. |
| `generation_timeout_s` | 180 | One POST through a complete response, allowing cold load and output generation. |
| Worker-lock wait | 0 | Return `busy` immediately if another cooperating process owns the worker. |
| Discovery response limit | 1 MiB | Maximum body for tags, resident-model, or model-metadata inspection. |
| Generation response limit | 4 MiB | Maximum retained response body; overflow is incomplete work. |

Allow discovery timeouts from 1–15 seconds and generation timeouts from 30–900 seconds, with strict finite numeric validation. The two startup candidates have separate budgets, so default background discovery is bounded by ten seconds plus bounded local bookkeeping. Check `/api/tags` and `/api/ps` under the worker lock before each generation with their own discovery budgets; disclose those intervals separately from generation time.

Use a cancellable transport with a total deadline that covers DNS. A socket read timeout by itself is insufficient. A timed-out resolver may finish in a bounded helper process that the adapter terminates and reaps; do not accumulate abandoned resolver threads. Sequential connection attempts share the candidate's deadline. Tests must demonstrate the chosen transport's behaviour rather than infer DNS bounding from a timeout parameter.

## Model profile and prompt budgeting

Fix the pilot model and request profile:

```json
{
  "model": "qwen2.5-coder:14b-instruct-q4_K_M",
  "prompt": "assembled focused task packet",
  "stream": false,
  "keep_alive": "10m",
  "options": {
    "num_ctx": 4096,
    "num_predict": 1024,
    "temperature": 0
  }
}
```

Expected digest:

```text
9ec8897f747e246e970bc5cfdda85d22f1123dc2e3d34978a010a75968716849
```

Keep these values out of per-task overrides. The adapter does not pull/update models, choose alternatives, unload a resident model, tune streaming, or change the LAN binding. A digest mismatch marks the worker unavailable and records the observed identity for a separate baseline decision.

Version the first-pass and feedback prompt templates from handoff sections 7b and 7c. Send only the focused packet, retaining its exact behavioural contract. Use the server's normal template with no `raw`, system override, or conversation history. User-supplied code and reference excerpts are delimited data, not authority to change adapter policy.

Before POST, establish an upper bound of at most 3072 input tokens including all prompt/template overhead, leaving 1024 output tokens within 4096. Never silently truncate context or increase the model context setting.

The budgeting component supports two auditable methods:

1. An offline matching tokenizer plus verified rendering of the installed baseline template, including its normal system/default text and special tokens. Record tokenizer identity, artifact hashes, template fingerprint, method, and count. No automatic tokenizer download or model modification occurs.
2. A conservative bound supported by the verified baseline tokenizer/template behaviour. Count UTF-8 bytes for each supplied/template literal plus separately bounded special tokens only where the implementation has established that this bounds token count. Record the derivation and tests. A fixed characters-per-token ratio or an unexplained template reserve is insufficient.

If neither method can establish a bound, return `unavailable` with `budget_unverifiable` before inference. This preserves a usable supervisor workflow while making tokenizer/template readiness an explicit generation prerequisite. Read-only model metadata inspection may use `/api/show`; it must not load or alter a model. Provisioning artifacts and proving the budgeting method are implementation acceptance work, not a licence to guess an artifact hash in this specification.

Record the actual `prompt_eval_count` after generation. A count exceeding the preflight bound, missing mandatory count evidence, or other evidence of truncation makes the output `incomplete_error` and unaccepted, and disables further generation until the budgeting defect is resolved. Retry budgeting includes the original packet, relevant failed answer, and feedback diagnostic in full.

## Shared coordination and uncertain outcomes

Use a stable worker identity `LOCAL-00` for coordination, independent of hostname, IP alias, repository, Codex session, or v2 state-directory override. On this Windows host, all cooperating bridge processes under the same user share `%USERPROFILE%\.gpt-architect-bridge-local00`. Tests inject an isolated home rather than configure independent production lock roots.

This coordinates v2 sessions and clients on this host/account only. Other Windows accounts, bridge hosts, and unrelated Ollama clients must remain excluded from the pilot by operating policy. A client-side lock cannot enforce server-wide exclusivity against them. Distributed coordination is outside this pilot.

Use one advisory OS file lock for the entire local generation critical section. Serialize task-ledger updates with that same worker lock. Local status can read the last atomically committed snapshot without waiting. Do not acquire Codex session/registry locks while holding the worker lock, and do not invoke architect tools from inside the local adapter.

For each generation:

1. Validate the packet and preliminary budget before attempting the lock.
2. Acquire the worker lock or return `busy`. Re-read durable coordination and task state under the lock.
3. Refuse unresolved uncertainty. Enforce the task/attempt ledger and content-audit authorization, and repeat the decisive budget check against the selected baseline metadata.
4. Validate the selected endpoint's model/digest again, then inspect `/api/ps`. Any different resident model produces `busy`; do not unload it or start a new discovery loop. A resident entry claiming the configured name with a different digest also prevents generation. Malformed or failed resident-model checks prevent generation.
5. Persist the request, audit identity, and an active marker before any generation bytes can be sent. If required persistence fails, do not POST.
6. Make one POST with no automatic retries or redirects. Keep the worker lock through response processing and durable finalization.
7. Persist the full retained response and final ledger state before clearing the active marker and releasing the lock. An uncertain persistence outcome must not permit a duplicate generation.

An active marker found after process death, a timeout/cancellation after POST may have begun, a dropped/truncated transport, or ambiguous request delivery leaves coordination `uncertain`. The OS lock may release on process death, but the durable marker continues to block generation across sessions and process restarts. Read-only status and architect work remain usable.

Errors proven to occur before any POST transmission can return to idle. A fully received terminal response, including a rejected output-limit result, establishes that this adapter's request has ended; it remains an unsuccessful task and is not automatically retried. Treat ambiguous server errors conservatively if they do not establish generation completion.

`reconnect`, successful tags validation, an empty `/api/ps`, and a matching loaded model do not prove the old request has finished. They cannot clear an uncertain marker.

Provide a local administration command under `bridge_admin.py local-worker resolve-uncertain`, requiring the exact pending request ID, an explicit `--confirmed-idle` acknowledgement, and a recorded reason/evidence reference. The command acquires the worker lock, verifies the marker still matches, records the operator's external confirmation that the old generation has ended, and retires that request without replay. The supervisor must obtain that confirmation from an operator with server-side visibility; the adapter has no remote-shell or server-cancellation authority. Never infer confirmation from elapsed time alone. If confirmation is unavailable, continue without LOCAL-00.

## Generation completion and acceptance

Accept a response as a draft only when HTTP succeeds, bounded JSON has the expected structure, the response model matches, `done` is the boolean true, and output is a nonempty string. For this pilot require the normal stop reason `stop`; an absent or unknown reason, output-limit termination, malformed metrics, or incomplete content produces `incomplete_error`. Preserve the original reason and body for review.

Token counts and durations must be nonnegative integers, excluding booleans. Preserve the documented duration fields in nanoseconds and calculate reported seconds by dividing by 1,000,000,000. Optional new API fields can be retained in the bounded audit body without becoming acceptance claims. Semantic completeness and factual correctness remain pending supervisor review even for a well-formed draft.

Independent validation follows the original behavioural contract:

- For code changes, the supervisor uses a reviewable candidate copy in the approved project toolchain and runs compilation plus executable behaviour tests covering the contract and its edge cases. Compilation alone does not pass.
- For explanations, the supervisor checks material claims against the supplied source and authoritative version-specific references.
- Missing toolchains, missing evidence, unresolved ambiguity, or clarification requests produce `unverifiable` and escalation to the supervisor. Confidence, model self-review, and invented test evidence never count as acceptance.

`record_validation` must identify the exact result digest, packet hash, check IDs, observed verdicts, and toolchain/reference evidence. Record compiler and behavioural evidence separately for code changes. Record supervisor edits separately; an edited accepted result is a supervisor correction, not first-pass worker success.

## Retry and task ledger

Store a durable ledger keyed by the canonical task identifier and packet hash in the shared worker state. The first generation is attempt 1. A completed draft can receive one feedback retry, attempt 2, only after a recorded independent validation failure with an exact compiler diagnostic, failing assertion, or factual contradiction. The failure record references the original checks and result digest.

The retry carries the original task packet and relevant prior output, preserves the original criteria, uses the same model/options, and must fit the budget again. Re-run the same independent checks against attempt 2. Further failure or unverifiability escalates to GPT/Claude. Clarification requests and transport/incomplete failures escalate rather than open an extended conversation or hidden retry loop.

A repeated call for an existing task/attempt returns its recorded result and `replayed_from_ledger: true`; it never issues another POST. Conflicting task data returns an input conflict. An attempt interrupted by uncertainty remains blocked. Ledger state survives process restarts so restarting the server cannot reset the retry budget. Idempotency covers cooperating clients using the same task ID; the supervisor remains responsible for not disguising the same failed task under new IDs.

Once accepted, a task is closed to further generation. Validation updates are append-only records tied to the exact attempt/result; a correction can supersede a reported verdict for audit purposes but cannot restore a spent retry or erase an earlier failure. Duplicate identical validation submissions are idempotent.

## Configuration and persisted data

Keep worker configuration separate from the existing strict `settings.json`, whose accepted sandbox-override keys remain unchanged. Put `config.json` under the shared LOCAL-00 state directory, with schema version 1, preferred hostname, initial candidate, last validated IP and provenance, validation timestamp, and discovery/generation timeouts. The preferred hostname is fixed for the pilot. Validate size, schema, types, and address values before use.

The shared directory contains a worker lock, atomic availability/coordination snapshot, active request marker, per-task ledgers, metadata telemetry, and explicitly approved content audit records. Use atomic writes and the existing locking primitives where applicable, without sharing the architect registry schema. Validate resolved paths against reviewed repository roots and the v1 state directory; reject symlinks/junctions or overlapping locations before writing. Stored caller identifiers cannot escape the shared root.

A corrupt or unwritable worker configuration/ledger makes LOCAL-00 unavailable. It does not cause `ArchitectService` construction or ordinary calls to fail. Do not silently reset a corrupt task ledger or uncertain marker. `continue_without` is session-local; a successful validated IP is persistent.

### Audit and measurements

Require explicit content-audit approval in each task packet before generation. Store the exact assembled request and retained response locally with the source approval reference, model/digest/options, prompt-template version, context identifiers, attempt, completion result, and ledger identity. Keep these content records separate from the existing content-free architect telemetry and return audit references rather than copying request source into status messages.

Retain pilot audit records until explicit operator archival/removal; no automatic deletion or rotation discards task evidence during the pilot. If required audit persistence fails before POST, refuse generation. If it fails after POST, report the persistence failure, preserve uncertainty where needed, and never replay inference as a logging repair. Oversized/incomplete responses record the retained prefix and explicit truncation; they are never represented as fully preserved or accepted output.

Record first-pass acceptance, retry outcome, material errors/omissions, supervisor edits, escalation reason, preparation time, lock/check time, inference time, validation time, correction/review effort, and total elapsed time to acceptance. Use monotonic intervals for agent/tool measurements, and UTC timestamps for correlation. Human active time is populated only from actual human measurement, with its measurement source; otherwise null. Premium token/cost fields remain unknown without receipts.

Any future comparison selects tasks before the delegation path and uses comparable context and acceptance checks. Record order/learning effects. Token throughput alone is not evidence of economic savings or reduced review effort. Broader routing remains a separate decision.

## Proposed code boundaries

These are implementation destinations, not existing APIs to assume:

| Destination | Responsibility |
| --- | --- |
| `bridge/server.py` | Add the one typed tool and background lifecycle hooks; preserve architect handlers and stdout hygiene. |
| `bridge/local_worker/models.py` | Strict action, packet, result, and validation contracts. |
| `bridge/local_worker/service.py` | Discovery/session states, explicit action dispatch, eligibility, retry transitions, and result assembly. |
| `bridge/local_worker/transport.py` | Bounded DNS/HTTP, exact peer tracking, disabled proxies/redirects, cancellation, and delivery-phase reporting. |
| `bridge/local_worker/storage.py` | Shared worker lock, atomic coordination/task ledger, idempotency, and uncertain markers. |
| `bridge/local_worker/prompts.py` | Versioned first-pass/retry assembly and auditable context budgeting. |
| `bridge/local_worker/audit.py` | Separate content audit and pilot metadata with explicit provenance. |
| `bridge/admin.py` | Local status/reconnect and explicitly acknowledged uncertainty resolution. |
| `tests/test_local_worker_*.py` | State, transport, budgeting, protocol, persistence, and supervisor-contract tests. |
| `README.md` and relevant current evidence notes | Primary v2 usage, v1 fallback role, local-worker operation/recovery, and current verification scope. |

Prefer installed dependencies where they satisfy the deadline and protocol requirements. Any new transport/tokenizer dependency must enter the pinned input/lock workflow and pass the existing environment verification. The adapter does not modify Codex authentication, sandbox settings, model routing, or MCP allowlists. Automatic delegation from a bridge-spawned Codex architect remains disabled; a supervising client explicitly calls `local_worker`.

## Acceptance evidence

Use mock HTTP transports and a disposable local server for deterministic failure tests. Tests default to an isolated home/state and synthetic source. They must not contact Skynet or invoke a premium model implicitly.

| Requirement | Required executable evidence |
| --- | --- |
| Three-tool MCP surface | List exactly the original two tools plus `local_worker`; compare original tool schemas with their fixtures and round-trip the new typed tool over stdio. |
| Ordinary workflow availability | Architect tools, initialization, pings, and cancellation remain responsive during failed/slow discovery, a busy worker, and uncertain state; stdout remains JSON-RPC only. |
| Discovery | Hostname success, saved-IP fallback, no saved IP, both failures, manual input, actual peer persistence, failed-write handling, and preferred-name retention. |
| Address validation | Valid hostname/IPv4/IPv6 cases and rejected ports, schemes, paths, credentials, wildcard/loopback/multicast addresses, and malformed inputs. |
| Identity and residency | Missing/duplicate model, digest mismatch, malformed tags/ps, different resident model, and expected model resident; assert no unload, pull, alternate model, or service change. |
| Total deadlines | Slow DNS, connection, headers, trickled body, cancellation, oversized body, and helper cleanup; wall-clock bounds cover the whole candidate/request. |
| Shared serialization | Two real processes using distinct sessions and v2 state overrides contend on the same worker lock; hostname/IP aliases cannot produce concurrent POSTs. |
| Crash and timeout recovery | Hard kill after marker creation, interrupted POST, lost response, and failed final persistence block subsequent generation; successful discovery and `/api/ps` never clear uncertainty. |
| Operator recovery | Exact pending request ID and explicit confirmation required; stale ID, missing confirmation, and unresolved coordination refuse; recovery never repeats the old POST. |
| Fixed request profile | Inspect serialized requests for the exact baseline settings and absence of system/raw overrides, implicit generation retries, redirects, and proxy routing. |
| Budget correctness | Full template overhead, retry overhead, Unicode/long identifiers, threshold boundaries, missing tokenizer proof, and observed count exceeding the preflight bound. |
| Completion checks | HTTP errors, malformed JSON, wrong model, empty output, false/missing done, unknown/length reason, invalid metrics, and clarification output remain unaccepted. |
| Validation and retry | Compiler-only evidence cannot accept code; unverifiable work escalates; one failure-backed retry only; result/packet mismatch, duplicate call, restart, and altered criteria do not bypass the ledger. |
| Audit and measurement | Source/audit approval required; complete approved request/response retained; content excluded from architect telemetry; persistence failure cannot trigger inference replay; unknown human time/cost stays null. |
| Compatibility and fallback | Original architect regression checks pass; v1 source/state/registration remain unchanged; no automatic v1 invocation on local failure. |

Run the focused new tests first, then the applicable existing full suite using the project's evidence harness with current source fingerprints. Update exact-tool-count assertions deliberately, retaining the original per-tool fixture comparisons. Bind test results to the implemented source and report environment limitations. Prior 483-test evidence is historical and is not a result for this extension.

A controlled live check uses the actual v2 runtime to verify discovery/model identity and the continue/reconnect paths. After tokenizer readiness, resident checks, and content approval are established, one explicitly selected synthetic bounded task may exercise generation and independent validation through the real adapter. Record its request, result, timing, and checks. No new benchmark programme, real proprietary source, or premium-model consultation is required for activation evidence.

## Observed connectivity baseline

At 2026-10-15 13:43:39 UTC, the native Windows v2 interpreter `C:\AI\gpt-claude-mcp-bridge-v2\.venv\Scripts\python.exe` successfully requested `/api/tags` through both `skynet.local` and `192.168.0.78`. Both connections used peer `192.168.0.78`, returned HTTP 200, and listed the expected model/digest. A subsequent `/api/ps` response listed no resident models. No inference ran and no settings changed.

These were native subprocess checks outside the agent's restricted network sandbox. They establish reachability from that host/interpreter at that time; the running MCP process and future adapter discovery/persistence paths still need their own implementation verification. Earlier in-sandbox failures did not describe native host connectivity.

## Delivery and fallback behaviour

Implementation delivery includes the v2 adapter, strict tool schema, supervisor usage instructions, recovery command, focused/full test evidence, and a record of any live checks. Update current v2 documentation to reflect its primary role and the new third tool while preserving historical evidence boundaries.

Deploying revised source requires restarting the relevant v2 MCP server so clients discover `local_worker`. Keep existing client registration names and commands unless an implementation finding requires a separately reviewed change. A local-worker-only failure is handled by continuing the ordinary v2 GPT/Claude workflow. A broader v2 bridge failure can lead the user to select v1 manually as the fallback; v1 does not gain LOCAL-00 in this scope.

## Review and execution guidance

Review this specification before producing the implementation plan. The implementation plan should turn these contracts and acceptance cases into ordered work against the actual code; this document does not select a parallel-agent execution method or authorize messages to other chats.

**Suggested execution model: Claude Sonnet.** The extension uses established Python/MCP patterns with focused transport, concurrency, and persistence tests. This is advisory; it does not switch the user's chosen model or add a model-selection approval gate.

## API references

The pilot request shape and returned completion/metric fields use Ollama's [Generate a response reference](https://docs.ollama.com/api/generate). Model-name/digest inspection uses [List models](https://docs.ollama.com/api/tags); resident-model inspection uses [List running models](https://docs.ollama.com/api/ps). Policy restrictions, acceptance criteria, timeout defaults, persistence rules, and the v2 tool contract above are requirements of this integration.
