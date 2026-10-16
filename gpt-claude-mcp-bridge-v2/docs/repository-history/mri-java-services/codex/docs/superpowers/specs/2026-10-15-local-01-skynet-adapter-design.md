# LOCAL-01: Separate Skynet adapter and explicit local routing

- Status: Proposed
- Approval: pending written-design review
- Date: 2026-10-15
- Repository: D:/IdeaProjects/mri/mri-java-services
- Base revision: 4f2216210097664e0ee9ad852d3532267ffc50f4
- Execution method: Claude Code implementation; independent Codex review and verification, as requested by Kian in this chat.
- Publication authority: no staging, commit, push, installation or registration changes.

## Intent and boundaries

Kian requested a separate local-model adapter and explicit routing to Skynet,
delegated to Claude and independently verified. The working GPT bridge,
registrations and accepted ADRs must remain unchanged. This proposal adds local
assistance without assigning architectural authority or implementation authority
to model-generated text.

Architectural assessment: use a new Python component under
`C:/AI/gpt-claude-mcp-bridge-v2/tools/local-model-adapter/`. Provide a usable CLI and a stdio MCP server using
the existing Python/MCP runtime. The CLI and a directly launched MCP client can
use it immediately after verification. Existing native Codex/IntelliJ sessions
will not automatically gain new tools: this scope deliberately writes no MCP
registration. Documentation must state this activation limitation plainly.

Existing startup and dashboard code remain unchanged. A separate model-free
health command is included; integrating it into those products is later work.
No Ubuntu service, firewall, network, model or resource setting is changed.

## Evidence and uncertainty

- Source observation: GPT v2 uses ChatGPT-authenticated Codex CLI and its model
  catalog, per `C:/AI/gpt-claude-mcp-bridge-v2/README.md` and `bridge/config.py`.
  It is not repointed at Ollama.
- Source observation: `C:/AI/gpt-claude-mcp-bridge-v2/tools/claude-bridge/README.md` describes FLOW-01 source
  binding, durable implementation receipts and caller-recorded reviews at the
  base revision. These interfaces and their receipt store remain unchanged.
- Verified result, 2026-10-14: bounded Windows host requests to `/api/version`
  and `/v1/models` returned HTTP 200; Ollama 0.30.9 and the baseline model were
  present. Evidence is the local `skynet-windows-lan-verification.json` artifact.
- Supplied Ubuntu evidence: Skynet's restricted-LAN handoff reports a native
  smoke completion `LOCAL_SMOKE_OK`, 4332 ms, 4096 context, full reported GPU
  residency. This is not an independently repeated Windows inference result.
- Verified result, 2026-10-15: the fresh host-side command
  `curl.exe --noproxy "*" --connect-timeout 3 --max-time 5 --max-filesize 32768 --silent --show-error http://192.168.0.78:11434/api/version`
  timed out connecting, exit 28. Current reachability is not established.
  Offline fixture work can proceed after approval; live completion requires
  restored reachability. The reason for the timeout is unknown.
- Physical reboot recovery, router reservations, other-host rejection and
  advanced model capabilities remain unverified.

## Options considered

Architectural assessment:

1. Separate native Ollama adapter (recommended): preserves existing bridges and
   exposes the already-tested request-level context controls.
2. Codex local-provider configuration: introduces CLI/provider/authentication
   compatibility work and configuration changes outside the requested boundary.
3. A general multi-provider dispatcher: adds session forwarding, permission and
   approval propagation before those interactions are needed. Defer it.

## Explicit routing contract

The caller must supply `route="skynet"` and one operation from `summarize`,
`extract`, or `draft`. There is no default route, natural-language routing,
automatic model selection or fallback. Unknown routes and operations fail before
network access. Architecture approval, final review decisions, file edits,
commands, tool execution and autonomous implementation are unsupported.

Each operation uses a fixed system instruction and caller-supplied text. The
adapter reads no repository files or URLs on the caller's behalf. Local output
is returned as untrusted assistance; it is never executed or promoted into an
approval, accepted ADR, verified fact, or implementation instruction.

GPT architectural consultations/reviews and Claude implementation continue
through their existing tools. Reserved routes `gpt` and `claude`, and reserved
operations `approve`, `review`, and `implement`, return a fixed `role_referral`
refusal naming the existing workflow. Other unknown routes/operations return
`invalid_route` or `invalid_operation`. No caller text is inspected to infer
intent. Requests are not forwarded. Thus LOCAL-01 explicitly routes eligible work
to Skynet; it does not replace the existing orchestration workflow.

CLI commands: `health`, `run`, and `receipt`. `run` requires explicit route,
operation and text supplied through stdin, with a task identifier. Avoid prompt
content in command-line arguments. Responses are machine-readable JSON; errors
have meaningful nonzero exit codes. MCP exposes equivalent `local_model_health`,
`run_local_task`, and `get_local_receipt` tools. All three share the same core
validation/routing behavior. Health and receipt retrieval never generate text.

### Public interfaces

Entry points: `C:/AI/gpt-claude-mcp-bridge-v2/tools/local-model-adapter/local_model_cli.py` and
`C:/AI/gpt-claude-mcp-bridge-v2/tools/local-model-adapter/local_model_mcp.py`, executed with
`C:/AI/gpt-claude-mcp-bridge/.venv/Scripts/python.exe -B`.

| Interface | Exact caller inputs |
|---|---|
| CLI `health` / MCP `local_model_health()` | None; no inference |
| CLI `run` | Required `--route`, `--operation`, `--task-id`; optional `--max-tokens` default 256; UTF-8 text through stdin only |
| MCP `run_local_task` | `route: str`, `operation: str`, `task_id: str`, `text: str`, `max_tokens: int = 256`; unknown keys refused |
| CLI `receipt --job-id` / MCP `get_local_receipt(job_id: str)` | Canonical lowercase UUID4 job ID |

Task IDs match `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`. They are caller-assigned
nonsecret identifiers, not prompt/text fields. Job IDs are generated UUID4 values.
Callers are responsible for authorizing content sent over LAN HTTP; no content
classifier is claimed.

Results use a JSON envelope with `schema="local-01/1"`, `status`, nullable
`job_id`, `route`, `operation`, `output`, `receipt`, `error_code` and `referral`.
Health adds `health` with API/model availability and residency `unknown`, `loaded`
or `unloaded`. Query `/api/ps` within the same deadline; unavailable optional
residency stays unknown. Health succeeds only if version and exact installed-model
discovery succeed. Receipt lookup returns its validated record, never model output.

CLI exits: 0 completed/healthy/receipt-found; 2 invalid/refused/not-found;
3 transport/preflight/protocol failure or unhealthy; 4 timed out;
5 busy; 6 incomplete; 7 persistence uncertainty/failure. MCP returns the same
envelope with failures visibly identified rather than success-looking text.

Example: pipe UTF-8 text to
`C:/AI/gpt-claude-mcp-bridge/.venv/Scripts/python.exe -B C:/AI/gpt-claude-mcp-bridge-v2/tools/local-model-adapter/local_model_cli.py run --route skynet --operation summarize --task-id LOCAL-DEMO --max-tokens 256`.
The README includes PowerShell examples preserving UTF-8 correctly.

## Serving contract and budgets

Fixed endpoint: `http://192.168.0.78:11434`. Fixed installed model:
`qwen2.5-coder:14b-instruct-q4_K_M`. No caller-controlled endpoint, model, headers,
proxy, credentials or arbitrary generation options in this version.

Use nonstreaming native `POST /api/generate`, with `stream=false`,
`options.num_ctx=4096`, `options.temperature=0`, and explicit `num_predict`.
Default output budget is 256 tokens; callers may request an integer from 16
through 1024. Reject invalid values rather than silently clamp them. These
request overrides do not change the server default of 2048 context.

Caller text is nonempty and at most 2048 UTF-8 bytes; each fixed instruction is
at most 256 UTF-8 bytes. Reject oversized input without truncating it. This is
a conservative bounded-input policy, not a tokenizer-backed guarantee against
server truncation; do not claim lossless context handling. No chat history or
session state is maintained.

Health uses `/api/version` and `/api/tags` to establish API availability and the
exact installed model. Report model residency separately if `/api/ps` is queried;
API health does not prove a warm model. Missing model and unreachable host are
failures, not a download/preload request. Preflight discovery for generation is
included in the generation deadline.

Bound model-free health to 5 seconds total, discovery bodies to 32 KiB, and the
entire generation operation to 30 seconds total with at most 64 KiB response
capture. Disable environment proxies and redirects. Apply the deadline across
connection, headers and every body read, including slow-drip responses. No
retries, model pulls, warm-up requests or deadline extensions. Close owned I/O
on timeout; do not claim that this proves server cancellation/resource release.

Use a nonblocking cross-process local execution lock. If another adapter
generation is active, return `busy` promptly without enqueueing or sending a
request. This does not control other Ubuntu callers or create a global Ollama
concurrency guarantee.

Validate HTTP status, JSON types, returned model identity, completion status,
text and optional numeric counters. Do not accept a Boolean as an integer.
Report missing counters as unknown; never manufacture usage or cost. A length
stop is explicitly incomplete, with generated text available for inspection.
Malformed/oversized responses, mismatched identity and missing completion are
fixed-code failures. Suppress remote error bodies and raw exception text.

## Receipts and data handling

Use a separate ignored `.runtime/receipts/` store under the new component.
Never write into GPT or Claude state/receipt directories. Persist a validated,
atomic initial receipt before inference; if persistence fails, do not dispatch.
Persist the terminal state before reporting confirmed completion. A terminal
write failure is reported as persistence uncertainty, never confirmed success.

Receipts contain schema/job/task identity, timestamps, route/operation, requested
and reported model identity, budget values, fixed outcome codes, elapsed time,
completion reason and numeric usage counters. They contain no input, output,
system instruction, source text, authorization header, raw remote response,
stderr, exception text or secret. The live response returns generated text but
does not persist it. No claim of content-free end-to-end infrastructure logging:
Skynet's own logging is outside this adapter's control.

Receipt access accepts only generated job IDs and refuses traversal and linked
store paths. Interrupted/uncertain receipts never cause replay. Retrieval and
startup/import perform no inference. No human-acceptance field is inferred from
model completion. Keep receipts until manually removed; bounded per-file writes
do not imply a total retention quota. Use integrity checks for accidental
corruption without claiming authentication.

### Transport, lock and receipt clarifications

Architectural assessment: use the already installed
`C:/Windows/System32/curl.exe` for HTTP, with `-q` first to ignore curlrc,
explicit proxy bypass, no redirect following and `--max-time` set to the
remaining total budget. Send JSON through stdin, not argv or disk. Bound stdout
and stderr capture; impose an independent parent process deadline and terminate
and reap only the owned curl process on timeout/overflow. Request/response
tempfiles are forbidden. Exercise actual transport with slow headers/body and
blocked-I/O fixtures. Missing curl is a fixed prerequisite failure; no silent
transport substitution or dependency installation.

Use an OS-level file lock automatically released on process death, not existence
of a sentinel file. Order: validate input, acquire the nonblocking lock, recover
abandoned receipts, persist initial receipt, bounded preflight, dispatch once,
persist terminal receipt, release lock. Busy, invalid/referral, health, failed
lock and failed initial-write calls create no generation receipt. Preflight
failure after the initial write creates a failed receipt.

Timeout metadata identifies preflight or generation phase, without claiming
whether the server generated tokens. Cold load may exceed the deadline; retain
the timeout without retry or increasing it. Remote generation may remain active
after timeout, so a later explicit call may queue; the client lock does not prove
server idleness.

Receipt states: `running`, `completed`, `incomplete`, `failed`, `timed_out`,
`interrupted`. Under the execution lock, recover old running receipts as
interrupted before new generation or an unlocked receipt lookup. If held, lookup
must not rewrite another client's active receipt. Recovery records client
uncertainty, not proof that remote generation stopped. Terminal-write failure
returns `persistence_uncertain`, job ID, fixed metadata and `output=null`.

Persist only allow-listed completion reasons `stop`, `length`, `unknown`, and
reported model identity only after validation. Record installed-model digest
from `/api/tags`; it is metadata, not authentication or tag immutability.
Each receipt is bounded to 16 KiB. Declare explicit product/test/README files in
FLOW-01 source paths, excluding ignored `.runtime/` so receipt writes do not
invalidate implementation review. Needed receipts are preserved separately
before removing the new component for rollback; they are never replayed.

Offline tests inject endpoint/client and receipt-root dependencies through
non-public constructors/test harnesses. These are not CLI/MCP flags or environment
overrides of production endpoints. Fixture CLI/MCP tests instantiate entry
points with isolated dependencies and temporary stores. Live tests use the fixed
production endpoint and normal separate store.

## Delivery and verification

Implementation scope is only `C:/AI/gpt-claude-mcp-bridge-v2/tools/local-model-adapter/` and its own tests/docs.
Draft decision/contract metadata are maintained by Codex outside that scope.
Reuse installed dependencies; no package installation or Git publication.

Claude implements after written design and plan review. Codex independently:

1. Reviews actual source/diff, including routing, trust boundaries and receipt
   persistence; captures before/after hashes for protected code/configuration.
2. Runs offline tests for route refusal, budgets, cross-process busy behavior,
   slow headers/body, total deadlines, output caps, malformed data, incomplete
   completion, model mismatch, transport errors and content suppression.
3. Tests CLI and actual MCP stdio discovery/health/run using a fixture server,
   without GPT/Claude model calls. Tests receipt failure, corruption and traversal.
4. Rechecks Windows model-free discovery on Skynet. When idle, sends one tiny
   synthetic native smoke request through the adapter, 16 output tokens,
   30-second deadline, no retries or repository content. Verifies the generated
   reply and the exact corresponding local receipt independently. A failed live
   check is reported rather than replaced by fixture evidence.
5. Confirms protected GPT/Claude bridges, existing registrations, startup,
   dashboard, accepted ADRs, HEAD/index and pre-existing processes were preserved.
   Records a FLOW-01 implementation review against the final source digest.

Offline checks establish `offline-verified, live-unverified` progress, not
end-to-end completion. Codex is the GPT architect/reviewer here; its review is
independent of Claude's implementation, not an independent review of its own
architecture. Claude permission denials are reported; Codex runs needed tests
on the Windows host without changing permissions.

No physical reboot, hostile-host test, arbitrary remote capability test,
benchmark or production repository-content inference belongs to this scope.

Suggested execution model: Claude Sonnet. Advisory: native HTTP, existing MCP
patterns and bounded fixture verification suit ordinary implementation; this
recommendation does not change the user's selected model.

## Review status

Written-design approval is pending. The user has selected Claude delegation and
independent Codex review. No implementation is claimed or authorized by this
proposal's existence. The implementation plan will be prepared after this
written design is approved. Accepted ADRs are unchanged; the companion new ADR
is Proposed.

Read-only Claude design review: job `668e6005-d42b-4d70-9ab8-84182ecd1b25`,
completed without edits or permission denials. Its interface, routing,
test-injection, lock and receipt ambiguities were clarified above. Its inference
that the supplied 4332 ms smoke was warm was not adopted: the supplied handoff
describes the request as cold. No adapter implementation or inference test was
performed during this design review.
