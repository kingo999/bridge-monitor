# Project access and automatic queue maintenance

Updated 30 October 2026. This extends the existing Bridge; `route_work` remains
the sole execution router. No provider-selection layer, unrestricted shell, Git
mutation tool, commit, push or deployment capability is added.

## Project selection and task permissions

The user-confirmed MRI3 root is `D:/IdeaProjects/mine/mri3`, registered as `mri3`.
It is distinct from the earlier `mri` services root. Earlier independently
authorized `mri`, `mri_test` and `ui` roots remain configured. This upgrade did
not edit or build MRI3; its existing staged diagnostic files were preserved.

Host roots must be explicit existing local directories without symbolic links,
junctions, overlapping roots or path aliases. Operations recheck root and path
containment. Linked files, Git metadata and recognized secret paths are denied.
MRI3's `mcp/`, `report/` and `seed_db/` are excluded. Its host write prefixes
are `src/`, `ui/`, `docs/` and `scripts/`; build descriptors remain denied.

Every repository task requires exactly `scope: ["repository-execution"]` and
the following string-valued context (alongside normal complexity/risk/rationale):

```json
{
  "project_root": "D:\\IdeaProjects\\mine\\mri3",
  "repository_ids": "mri3",
  "repository_access": "read-only"
}
```

`repository_access` is mandatory, case-sensitive and either `read-only` or
`read-write`. Missing/invalid access is rejected before worker launch. Read-only
grants expose only `read_file`, `find_files`, `search_text`, `read_run_log` and
`git_status`; file writes, tests/builds and framework execution are also denied
inside the tool implementation. Git status suppresses `core.fsmonitor` and
optional index writes. A read-only review must not write progress notes.

Read-write tasks additionally require a JSON object encoded as a context string:

```json
{
  "repository_access": "read-write",
  "repository_write_prefixes": "{\"mri3\":[\"src/test/\",\"docs/\"]}"
}
```

Each task prefix must be an exact filename or a directory ending in `/`, and
must be contained in a host write grant. Out-of-scope roots/prefixes are rejected,
not silently widened. Build and campaign permissions apply only to write-granted
roots. A task selecting several roots cannot execute checks on its other roots.
`expected_sha256` is required for existing-file edits; replacement and atomic-write
checks continue to reject stale content. Writes remain working-tree changes.
Stage approved changes explicitly as an operator; workers cannot stage or commit.

MRI3 uses the existing structured Maven command runner, with an empty configured
module list: checks run at the project root rather than the legacy
`report-application` reactor module. Supported Maven goals remain compile, test,
verify and version; test selectors remain bounded. UI-specific npm checks have
not been added to this upgrade. Do not claim a UI check ran through this runner.

The grant snapshot and its hash include access, roots, write permissions and
command configuration. Routing, adapter launch and source locks must agree.
Changing host configuration or a task's permissions requires a new grant; old
leases are never silently upgraded. Existing task IDs cannot be replayed with
changed prompt or permissions.

## Worker-limit finding and repair

At investigation time, the active policy allowed four Claude workers. There
were 38 held accounting reservations: 34 already had correlated termination
proof and did not consume concurrency. The four blockers were:

| Call | State before repair | Action |
| --- | --- | --- |
| `72a4bf46-52db-5ae1-b55c-64d5a9386a15` | uncertain, slot held | Preserved; no correlated termination receipt |
| `24f557e6-d0f8-5cdb-b833-eecbf4a5825f` | expired queue, never launched | Cancelled and reservation/budget returned |
| `8d99ff06-053d-5553-b0f4-048b9470bfd8` | expired queue, never launched | Cancelled and reservation/budget returned |
| `4c029f32-70f8-5f49-b802-919b412350f8` | expired queue, never launched | Cancelled and reservation/budget returned |

The queue owner had disappeared without cleaning these expired requests. The
reservation gate counted them even though queue dispatch would never select
them again. A failed bounded analysis also did not use the existing confirmed
exit reconciliation path, which had been restricted to repository execution.

The dashboard service now runs `QueueExpiryMonitor` immediately at startup and
every five seconds. The admission path also performs the same reconciliation.
Both use the existing arbitration lock shared with dispatch and cancellation:

- Cancel only expired, still-queued Codex/Claude calls without a lease, launch
  claim or held runtime slot; return their task budget exactly once.
- Release accounting reservations only for durable cancelled, never-launched
  calls. A verified prelaunch abort can have a lease; its cancelled state and
  absent launch claim fence future launch. A `dispatch:` intention is not a
  provider launch, and cannot bypass `claim_dispatch`/`claim_launch` state checks.
- Append the existing release event with `unlaunched_queue_terminal` evidence.
  No provider receipt, token usage, zero-cost assertion for executed work or
  guessed charge is created.
- Keep active/uncertain executions, missing-job reservations and unproven exits
  held. A cancellation request, stale heartbeat or old deadline is not proof.
- Reconcile future failed bounded analysis exits only when the adapter confirms
  termination. Do not retry inference or writes automatically. If reconciliation
  fails, keep the job uncertain and capacity held.

The live Monitor was restarted through its existing verified process receipt
and existing `BridgeMonitor-Dashboard` scheduled task. Its first sweep expired
and released exactly three requests; each recovered task budget became 0/0.
The uncertain call stayed held. No worker-limit or model policy was changed.
The useful pending design analysis then completed through the same Bridge.

Monitor health is available to the existing authenticated operator/browser at
`GET /api/v1/queue-maintenance`: running state, five-second interval, last
successful sweep, last error type, failed sweep count, and cleanup totals.
No prompt, credential or source body is included. Database or audit failures
roll back cleanup and remain visible; they do not authorize deleting holds.
The monitor stops with the dashboard lifecycle; its existing scheduled-task
watchdog restarts the dashboard. PostgreSQL and model processes are untouched.

## Audit, cancellation, activation and limits

Repository operations retain call IDs, operation result hashes and receipts;
new operation metadata includes access mode and policy binding. The child still
uses a SELECT-only ownership identity. Authority lookup now has explicit
connection/query/lock time bounds and fails closed on DB unavailability, so an
unresponsive database cannot indefinitely stall the command cancellation loop.
Windows process jobs, lease fencing, cancellation and command receipts remain.

Reload the existing parent MCP before new repository tasks: a running Python
process does not hot-reload these permissions. For an explicitly authorized
one-shot task, the existing `start-desktop-mcp.py --task-json ... --receipt-json ...`
path also loads the new code/policy. It is the same router, not a fallback route.
No automatic retry or paid test probe was used to validate MRI3 access.

Compatibility change: older repository calls without access and task write
prefixes are intentionally rejected. Existing application tasks must migrate
their task context. Supplied-context analysis remains supported as before.

This is a capability boundary for trusted local projects, not an operating
system sandbox. Approved builds/plugins/tests run as the host user and may write
build outputs/caches or execute project code outside the file-edit API. Do not
grant execution for hostile repositories. The path denylist cannot discover
secrets stored under arbitrary ordinary source filenames. Read-only source
locks remain conservative/exclusive; large-file reads and detailed Git status
output retain their earlier limitations. These are recorded review limitations,
not claims of universal filesystem isolation or secret detection.

## Verification evidence

Evidence is under `var/authorization/bridge-access-20261030/`. Focused tests use
synthetic projects and explicitly selected disposable `bridge_monitor_test_*`
databases; they never execute MRI3 code. `review-before.json`/`review-after.json`
confirm no source or Git-state changes during the external read-only review.

The initial full run reported **1305 passed, 18 failed, 4 skipped**. Three shadow
CLI failures were caused by pytest's temporary directory being outside Bridge;
rerunning `tests/shadow/test_cli.py` with a Bridge-local `--basetemp` passed all
four tests. The remaining failures are the parent-tool inventory expectation,
migration inventory expectation and 13 validation-export usage/lineage tests.
They were not hidden, weakened or fixed as unrelated cleanup. The full run is
not an all-green release claim; see `full-suite.log` for every failing node ID.
Final focused results after the review fixes: **151 passed, 2 skipped**, one
upstream Starlette/httpx deprecation warning; see `final-focused.log`. The skips
are missing Windows symlink privilege and the optional native framework fixture
not being configured. The full suite was not
rerun after the focused review fixes; its earlier failures remain disclosed.
