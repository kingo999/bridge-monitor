# Skynet task-status pilot evidence — 17 October 2026

## Implementation boundary

The agent and bridge adapter are project-owned. The existing bridge trees were
not edited. The reviewed `config/pilot-policy.skynet-agent-proposal.toml` is
active in the side-by-side Bridge Monitor pilot. Its source hash is
`445c3debabdc63c876c61ace504b0d1f281f8f52b95cb2b8fbee80ee5e428629`.
The raw policy-file SHA-256 is
`5fd0dd67d28bc9cbe19469ac8236b0bf4da5b717b249d3dc760d9628d1d8019f`;
the desktop-verified runtime capability proposal SHA-256 is
`5735e24a0a5bd627da1fd4ad4b7bde71f47202432ab7dfd57cf4facbe97b89f7`.

## Local verification

- Agent API/journal: 17 passed. Includes idempotency, restart-to-unknown,
  buffer gaps and expiry, terminal immutability, content-free journal, request
  limits, client allowlist and cancellation before task start.
- Adapter/direct-Qwen/policy/status-query focused suite: 28 passed. Deadline tests include
  healthy-but-silent status, idle stall, continuous output past total cap,
  poll outage, sequence gap, duplicate status, restart-to-unknown, slow telemetry,
  and a terminal page arriving after
  the total or shared local cutoff.
- PostgreSQL history/fallback/policy-reload integration: 19 passed against the
  dedicated `bridge_monitor_test` database. A synthetic agent adapter run
  persisted exact prompt/answer bytes, hashes and status events. Existing
  fallback tests confirmed a post-dispatch paid transition retains the local
  slot and a late local result is not delivered. A silent fake-agent job through
  `BridgeCoordinator` returned paid continuation and left the Qwen slot held.
  A query check kept Qwen status visible after more than 1,000 later deltas
  on another call in the same work item.
- Dashboard `npm run typecheck` and `npm run build` passed. Next produced a
  static export, preserving the no-SSR design.
- A broad local adapter/contracts/query run had two failures in existing
  `test_claude.py` process-termination tests. The preexisting Claude runner
  checkout change was not modified for this work. The other 46 tests passed
  and 19 skipped in that run.

## Skynet pilot

The current source-only archive SHA-256 is
`b94a585f0943e8e2e908db639f50ddb27e3f32554a0a966d07b13ef57bb24b1c`;
Skynet reported the same hash before extraction. Python 3.14.4 and an isolated
user-owned venv host the pinned agent dependencies. The `dave` user service
reported `active`. Authenticated `/v1/health` returned `model_ready=true` and
the configured Qwen digest
`9ec8897f747e246e970bc5cfdda85d22f1123dc2e3d34978a010a75968716849`.

One bounded adapter probe submitted job
`5557e1ed-83ee-44ec-8d10-785c95e8c901` with a 60-second total cap and
32-token output cap. It completed in about 25 seconds with `READY`, 36 input
and 2 output tokens. The adapter emitted one `response.delta`, three
`attempt.status` events and one `response.completed`. A subsequent
authenticated status read returned the same job ID, `completed`, sequence 1,
no gap, the pinned model and output usage 2. The probe did not use the active
bridge route or write to the production PostgreSQL database.
After switching to release `20261017-status-r2`, authenticated health still
reported the pinned model ready. The pre-restart job remained `completed` with
sequence 1 and `gap=true`, correctly signaling its lost in-memory output.
Release `20261017-status-r3` then passed a second bounded probe: job
`d4490edd-695d-4080-a57b-130cf62d7df7` returned `READY` with 36 input and
2 output tokens. Its correlated receipt showed `completed`, sequence 1, no gap,
and output usage 2; the authenticated health check still reported the pinned
model ready. At that stage the service remained active and Qwen admission was gated.
Release `20261017-status-r4` passed a final bounded probe: job
`0649ef10-7290-4495-b884-9a538a8d3fc7` returned `READY` with 36 input and
2 output tokens. Its authenticated receipt reported `completed`, sequence 1,
no gap and matching usage. At that stage the service remained active and Qwen
admission was still gated.
The local dashboard continued to serve HTTP 200 after its static build. A
read-only operational database check found active policy version
`77f6331062bd9666` still has no agent endpoint and keeps the prior one-second
Qwen execution limit. It found zero held Qwen slots. No operational policy or
job state was changed by these checks.

## Read-only review

Claude's enforced read-only review used `C:\AI` as an additional Git read
root. Its before/after snapshot reported no source drift. An independent
comparison found zero changed nonignored `bridge-monitor` file hashes and the
same `codex/skynet-task-status` HEAD; Git status gained only Claude's permitted
review receipt under the older bridge tree. The first review identified a
cancel-before-start state leak, a dashboard event-window loss, and late
completion after slow telemetry. Focused tests now cover each case. Other
review observations were addressed with request limits, explicit latency
telemetry and the documented uncertain-slot reconciliation boundary. A second
enforced read-only review (job `308b64f6-eb0a-4767-8a7b-788e4aca513a`)
found no blocker for a gated pilot and confirmed the three medium fixes. Its
before/after source snapshot and an independent 218-file hash comparison were
unchanged; Git status gained only the permitted review receipt. Its remaining
timing and early-auth observations were addressed after that review with
focused tests. No further external review has examined those last edits.

## Isolated live timeout and paid-continuation probe

On 17 October, a fresh PostgreSQL database,
`bridge_monitor_agent_probe_20261017195231`, ran one real
Codex → Claude Sonnet → Skynet delegation with a temporary test policy. Its
source hash was
`64896b515dd5dfd9629f98e692b7252b9197c8b69b5bc2c6944f091c2e041e23`.
The test shortened first-output, idle-output and total Qwen limits to 8, 6 and
60 seconds, respectively, to bound the exercise. It did not activate the
production proposal or change the desktop registration.

The Qwen child `d3060359-e67c-4528-8465-6acb874a5ea6` was dispatched to
the agent, streamed output, and reached the 60-second execution deadline. The
bridge emitted `attempt.uncertain` with `local_execution_deadline`, committed
an execution-stage paid fallback, and kept the Qwen slot held. Claude Sonnet
5.5 returned `56`; Codex received one completed bridge result after 84.173
seconds. The database contains one delivered Claude result, zero Qwen results,
routes `local` then `paid`, and resolution `paid_replaced`. The Qwen prompt and
Claude answer are retained as content; the stored paid answer exactly matches
the delivered answer. The dashboard history query exposes both model choices,
the agent status, and the timeout reason. Its live agent status is a last bridge
observation, not a continuing remote poll after fallback.

Investigation established why this r4 attempt fell back. The agent journal
opened a fresh SQLite connection for each streamed token but did not explicitly
close it. A separate 200-connection reproduction on Skynet left 200 descriptors
open until garbage collection. The service's soft descriptor limit was 1,024;
the live job reached sequence 1,329, and its journal logged repeated
`Too many open files` errors. Ollama's bounded journal shows the sole generate
request ended at 19:53:04 UTC, task 6 was released, and all slots became idle.
The bridge emitted its timeout at 19:53:48 UTC because the agent never wrote a
terminal receipt. Thus the paid continuation protected delivery, but this
particular takeover was avoidable agent overhead rather than a slow Qwen task.

The [bounded Ollama excerpt](../../var/probes/agent-live-timeout-20261017195231.ollama-journal.txt)
and [correlated termination receipt](../../var/probes/agent-live-timeout-20261017195231.termination-evidence.json)
capture the unique request, host boot ID, service PID, lease and source hash.
After a pre-reconciliation custom-format backup (SHA-256
`d63fb68ad4edc5155ace6858aa5379bd5defe80e0014c4b2591b76eebd18133e`),
operator reconciliation set only this isolated child to `terminated` and
`slot_held=false`. Its completed paid fallback and sole delivered result remain.
A post-reconciliation backup has SHA-256
`0eb8e0eec541a574cf35694d0614fef5af24559f9cd855ae5a0c5f3633a6c1a1`.
The production policy stayed at `77f6331062bd9666` with zero held production
Qwen slots.

The isolated receipt, audit and later read-only follow-up are under
`var/probes/agent-live-timeout-20261017195231.*`. The dashboard's recorded
process was also absent during the follow-up; its stale project-owned receipt
was archived and the dashboard was restarted. Its loopback page returned HTTP
200 afterward.
The two existing Claude process-termination tests also pass with normal local
process permissions (`2 passed`), resolving the earlier sandbox-only failure.

## r5 agent repair and normal route

The journal now closes each SQLite connection after its transaction, including
an error path. A regression test failed before the fix with 54 opened and zero
explicitly closed connections, then passed. The 18 agent tests and the complete
project suite passed (`236 passed, 2 skipped`; one Starlette deprecation
warning). The source-only r5 archive SHA-256 is
`2e4a0016a38cf1c19a78c7a8f154fec6b23e614d387cba049b1f1d1106437060`.
Skynet reported that hash before extraction; its user service now points to
`20261017-status-r5`, retaining r4 for rollback. The old nonterminal job became
`unknown` on restart, as designed; the Ollama journal, not that restart,
supported the reconciliation above.

A deterministic 2,000-update journal run on Skynet's deployed Python completed
under its 1,024-descriptor soft limit with four descriptors before and after.
The running agent had seven descriptors after a bounded model-only response,
which completed with a terminal receipt. That model chose a short 39-token
answer, so it did not itself stress the old limit; the journal run did.
After the r5 live timeout streamed 665 deltas, the agent still had seven open
descriptors and its bounded journal contained zero `Too many open files` lines.

A fresh isolated database, `bridge_monitor_agent_normal_20261017201948`, ran
one real Codex → Sonnet → Skynet delegation using the proposal's unchanged
45-second first-output, 20-second idle and 120-second total Qwen limits. Its
policy source hash was the proposal hash,
`445c3debabdc63c876c61ace504b0d1f281f8f52b95cb2b8fbee80ee5e428629`.
The call completed in 27.835 seconds. Both Claude and Qwen completed with one
delivered result each, the Qwen agent reported `completed` with no sequence
gap, and Claude recorded an `accepted` disposition. The work resolution was
`local_retained`; there was no fallback or held slot. The dashboard history
query exposed both requested/reported model choices and completed status; all
seven prompt/response content references returned stored bytes. Receipts are
under `var/probes/agent-live-normal-20261017201948.*`. Production policy and
held capacity remained unchanged.

A second fresh isolated database, `bridge_monitor_agent_timeout_20261017202403`,
exercised the r5 timeout path with test-only Qwen limits of 4 seconds to first
output, 3 seconds idle and 10 seconds total. Qwen streamed 665 output deltas;
the total deadline then triggered an execution-stage paid fallback. Sonnet
returned `56`, Codex received one completed answer in 45.633 seconds, and the
database recorded routes `local` then `paid`, resolution `paid_replaced`, one
delivered Claude result and no delivered Qwen result. The agent marked the
cancelled child `unknown` because it could not itself prove Ollama had stopped,
so the isolated Qwen slot stayed held at delivery as designed.

The bounded [r5 Ollama journal](../../var/probes/agent-live-timeout-20261017202403.ollama-journal.txt)
shows one matching `/api/generate` request: task 44 ended at 20:24:36 UTC,
was cancelled and released, and all slots became idle. Its SHA-256 is
`0e41fa554a73ccecc91d5ff599253e9d039f7bc525fa26e50f24276b24232e67`.
The [correlated termination receipt](../../var/probes/agent-live-timeout-20261017202403.termination-evidence.json)
records the lease, owner incarnation, host boot ID and service PID. After a
pre-reconciliation custom-format backup (SHA-256
`f0cdb8395ade06bb0bb99cd5f9cbd281fbd2b54b25493e29cc0afe2157efa274`),
operator reconciliation set only that isolated child to `terminated` and
`slot_held=false`. The completed fallback and sole delivered paid result remain;
the post-reconciliation backup has SHA-256
`760faf1732a2b0fd5dd937e209c8966375e7e750d6d452bd4241a0e7f1fc3ecc`.
Both checks found the production policy and held-slot count unchanged.
The read-only history/content follow-up found five agent status events and a
`local_execution_deadline` uncertainty event, both model choices, a 91-byte
persisted Qwen prompt, and paid answer bytes matching the delivered answer.
Its grouped status is the last bridge observation (`streaming`), while the
reconciled execution state is `terminated`; the UI must keep those distinct.

An enforced read-only Claude review of the r5 journal repair and pilot evidence
(job `20884156-b94b-4ffd-93c8-f1f79498eb56`) found no release blocker. The
review tool used Sonnet 5.5 because that endpoint did not expose a model
selector; it was not an Opus review. The independent before/after check found
the same Git HEAD and the same hashes for all four reviewed project files.
Git status gained only the permitted review receipt in the older bridge tree.
The reviewer noted that the runner does not independently enforce an `env:`
token reference, although the validated policy contract already does, and
suggested an isolated-database restriction that would conflict with the
runner's existing reviewed operational pilot use. Neither suggestion was
applied. Its stated live timeout coverage gap is closed by the r5 probe above.

## Activation and registered-client pilot

The user approved the canonical policy hash and capability SHA-256 above under
`call_639f7ad0d5704dd6895e989a7a36e48a`. The review fields now record
`user-operator` and that reference. Immediately before activation there were
no active or held jobs and no source locks. Restricted backups preserve the
former direct policy (SHA-256
`b6b5354c566f87900d6b8f666c75fc6920cdc14ed8cda7e6327d4743e3c79fce`)
and full project database (SHA-256
`25bc705d81160fbd35bf4327281ebcf47b5aa41e4737c8facbd331b2e52e77c1`).
Startup activation set version `445c3debabdc63c8`, the agent endpoint and
120-second Qwen execution deadline. The protected desktop settings now point
to the project-local agent token file, and the launcher loads it only when
the active policy uses the agent route. A fresh model-free stdio handshake
exposed all four expected parent tools. The full project suite passed after
that launcher change: `239 passed, 2 skipped` (one Starlette deprecation
warning).

After restarting Codex, the project PostgreSQL and dashboard services needed
to be restarted. The current policy, zero held or busy jobs, authenticated
dashboard session and dashboard detail endpoint then verified. One GPT-led
exchange ran through the installed `bridge_monitor` registration in Codex CLI. The preserved
[tool stream](../../var/probes/registered-agent-pilot-20261017.jsonl) and
[database audit](../../var/probes/registered-agent-pilot-20261017.audit.json)
show one tool call: Sonnet delegated to the pinned Qwen 2.5 profile, Qwen
returned `56`, Sonnet recorded `accepted`, and the bridge delivered `7 * 8 = 56`
with `accounting_complete=true`. The route was `local`, resolution
`local_retained`, both jobs completed under the active policy, and no slot
remained held. All eight referenced prompt/response body hashes and lengths
matched stored bytes; the authenticated work-item detail returned HTTP 200.
The first audit probe failed because it queried a nonexistent `reported_model`
column in `runtime_jobs`; the model choice is stored in `model_executions`.
The corrected read-only audit also authenticated to the dashboard and did not
send a second model request.

A fresh Codex desktop validation chat initially saw the tool as unavailable
after startup discovery had timed out while PostgreSQL was down. Its MCP server
subsequently reported `ready`; a follow-up in that same chat made exactly one
native `bridge_monitor.delegate_claude` call. The
[native database audit](../../var/probes/native-agent-pilot-20261017.audit.json)
correlates parent call `77522954-d78c-41dc-851c-6431f49550fe` with Qwen
child `ee729d0d-e956-4524-91c6-b689b54332b4`. Both completed under active
version `445c3debabdc63c8`. Sonnet recorded `accepted`, the sole effective
route was `local`, work resolution was `local_retained`, and the delivered
answer was `7 * 8 = 56`. All eight referenced content hashes and lengths
matched, authenticated dashboard detail returned HTTP 200, and no active or
held jobs remained. The native tool returned `accounting_complete=true`.

The isolated CLI runs verify local acceptance and automatic paid continuation,
but they do not exercise the full
600-second desktop paid-client deadline on their own. A separate model-free
native desktop MCP call waited 590.0 seconds and returned successfully after
593.074 seconds wall time under the same configured 600-second tool deadline;
its [receipt](../../var/probes/desktop-timeout-590s-20261017.receipt.json)
supports the desktop capability flag update. The temporary probe
registration was removed while preserving one concurrent Codex app config
edit. After explicit user approval,
`loginctl enable-linger dave` succeeded on Skynet; `loginctl show-user` returned
`Linger=yes` and the agent remained active. Logout survival is configured, but
the service has not been exercised across a logout or host sleep.
Claude's enforced read-only review of the 590-second evidence (job
`d97485c9-8885-4854-8ebd-ee83f163895e`) agreed that the paid-client flag
is justified for the desktop MCP transport. It did not treat the model-free
wait as proof of a near-limit bridge delegation. Its root snapshots and an
independent check found no source or Git HEAD changes; only the permitted
review receipt appeared. The review endpoint used Sonnet 5.5, with no Opus
selector. The registered Codex CLI and native desktop pilots above exercise
the activated route; the model-free wait remains a separate long-duration
transport check.
The agent has a per-read Ollama timeout and bounded output tokens, while the
bridge has the job's hard deadline. A failed cancel can leave a remote request
running after paid continuation. An uncertain Qwen slot is intentionally held
until correlated terminal evidence or operator reconciliation; there is no
automatic slot release based on agent health or elapsed time.

## Windows host lifecycle check

The project PostgreSQL and dashboard now run under separate per-user Windows
Task Scheduler entries, `BridgeMonitor-Postgres` and `BridgeMonitor-Dashboard`.
Their actions contain only project paths and no credentials. Both task wrappers
have the Task Scheduler service as parent, rather than the Codex process.
PostgreSQL binds to `127.0.0.1:55481`; the dashboard reads its existing
protected secret files and serves `127.0.0.1:8873`. The configurable task
values are in `config/windows-tasks.example.json`.

After cutover, the approved policy audit found zero busy jobs, held slots and
source locks, and the dashboard returned HTTP 200. A controlled dashboard
stop returned a verified process receipt; the task wrapper relaunched the
dashboard within its configured five-second delay. Four PowerShell config
tests passed. After an actual Codex desktop restart, both scheduled tasks
remained running, the active-policy audit again found zero busy/held jobs and
source locks, and the dashboard returned HTTP 200. The desktop discovered all
four `bridge_monitor` tools; a model-free `get_execution_status` probe for a
nonexistent call returned `unknown` with healthy capture, zero known gaps and
zero pending spool entries. This verifies service survival and MCP reachability
without another paid or local inference.
