# Scoped repository execution through the existing Bridge

**30 October update:** see [project access and queue maintenance](project-access-and-queue-maintenance.md)
for mandatory task access/write grants, the distinct MRI3 `mri3` root, automatic
expired-queue cleanup, current activation requirements and verification evidence.
The historical notes below describe earlier MRI services work.

Enabled by explicit user instruction on 27 October 2026 for the MRI P1 plan.

The existing route_work entry point, model-selection policy, budgets, telemetry and durable job ownership remain the only routing path. Analysis workers still have no repository tools. Repository execution is separately opt-in in runtime-capabilities.proposal.toml and requires exact scope [repository-execution], context.repository_ids and an approved context.project_root.

Approved roots: mri = D:/IdeaProjects/mri/mri-java-services-3; ui = D:/IdeaProjects/mri/mri-node-ui-services. Writes are limited to configured prefixes, safe text files and expected-content hashes. Git metadata, secret/environment files, raw pt data, build descriptors and linked/Windows-alias paths are denied. There is no general shell or Git mutation tool. Builds/tests execute trusted repository code as the host user; this capability is not an OS sandbox for hostile source or malicious build plugins.

Repository children receive a SELECT-only ownership database identity, not the parent's operational/system credentials. Build/test environments are allowlisted separately, excluding Bridge/provider/Git/SSH/application secrets; configured Artifactory dependency credentials remain available when needed by Maven. Never print credential values.

File writes are checked and atomic. Source ownership locks are durable and exclusive. Lease cancellation/expiry fences tools. Windows child commands and the CLI are started suspended, assigned to kill-on-close jobs, then resumed. Timeouts terminate the command tree and preserve receipts. A confirmed terminated repository call is reconciled without automatically replaying its writes; an uncertain termination retains ownership until evidence-backed reconciliation.

## Calling convention

Use requested_model=sonnet, normal caller budgets, context.complexity/risk/routing_rationale and route_override=auto. The Bridge chooses the actual pinned execution model. Do not infer costs from reservations.

The existing desktop MCP process must reload before its already-imported code sees the capability. During the authorized rollout, the SAME existing launcher can execute one durable request:

    .venv/Scripts/python.exe -B scripts/start-desktop-mcp.py --task-json var/repository-requests/request.json --receipt-json var/repository-requests/receipt.json

This invokes BridgeCoordinator.route_work with the active policy and database; it is not a separate router or a direct Claude CLI fallback. Request/receipt files must be inside Bridge var. Reuse exact task/work-item identities for unchanged admission retries, inspect receipts/status before retrying, and never replay an uncertain write call.

The repository MCP surface is read_file, write_file, replace_text, find_files, search_text, run_checks, read_run_log and git_status. Root IDs are mri/ui. run_checks accepts structured Maven compile/test/verify/version or targeted UI test/typecheck operations only. Operations and command receipts live under var/logs/repository/<call_id>/; no plaintext credentials or source bodies are added to operation audit metadata.

## Validation limits

Focused final boundary/stdio tests: 45 passed, 1 skipped (symlink creation privilege). A separate focused ownership/concurrency run passed 48 with the same skip. The initial broad run's database results are not an all-green release claim: its temporary runner lacked a Windows multiprocessing main guard and overlapping test runs reused the test database; the corrected focused runs were serial. Existing broad-suite expectations also include outdated tool/migration inventories. See repository-enablement-full-pytest.log for that historical attempt; do not hide it or attribute all failures to this feature.

No commits or pushes are authorized. Existing config/codex-claude.production.toml user changes were preserved. MRI checkpoints and execution evidence belong in its work/p1/PROGRESS.md.


## Blockage repairs and narrow UI preparation (27 October 2026)

Git status explicitly supplies DEVNULL stdin: inheriting the MCP input pipe caused the real stdio response to stall. The regression fails before the fix and passes afterward. replace_text requires a current full-file hash and exactly one matching substring; it reuses all atomic write/path/lease guards and preserves unrelated bytes.

Worker concurrency excludes a held accounting reservation only when the existing runtime job is terminated with slot_held=false and a correlated adapter/process-exit termination reconciliation matches its call, lease and owner. The reservation and missing usage remain unknown/held; no zero cost or no-charge claim is invented. Missing proof and uncertain executions continue to count. The configured worker limit is unchanged.

UI check additions are fixed commands: auth-build runs the existing test:auth:build; test optionally accepts one bounded .spec.ts basename. prepare-upload-parser is separately host-opt-in (ui_upload_parser_install; default false) and can add only @fastify/busboy@3.2.4 via pnpm --save-exact --ignore-scripts. It grants no generic package/lockfile editing or arbitrary install arguments. The pinned version was verified against https://github.com/fastify/busboy/releases/tag/v3.2.4 . The actual MRI UI install returned corporate Artifactory403 and made no package/lockfile change; user deferred UI work. Do not bypass that registry or read/print credentials.

Latest focused repository/concurrency/reconciliation verification:40passed,1skipped(symlink privilege),repository-ui-commands.log. Earlier broad-suite/harness failures above remain disclosed. One-shot launcher-owned results cannot be acknowledged through a different Desktop MCP process's owned-call set; independent parent verification remains documented,with no acknowledgement receipt claimed for that unsupported path. A transient capture_degraded admission was rejected before job creation and later capture recovered; no audit gate was disabled.


## MRI P1 execution follow-up (2026-10-27)

The scoped writer also accepts explicitly granted SQL migration-resource directories through `repository.sql_write_prefixes`; this grants text-file editing only, not live SQL execution. LF windows returned by `read_file` can match a unique CRLF region in `replace_text`; full-file hash, uniqueness, path and byte-preservation checks still apply.

`repository.max_log_bytes` has a default8MiB threshold and finite64KiB..32MiB validation. MRI synthetic-parser baseline output exceeded the original8MiB threshold; the explicitly approved host configuration now uses32MiB. The threshold is polled and bursts can overshoot before process termination; it is not an exact file-size ceiling. The display/tail budget, process containment, deadlines, commands, source roots and model policy are unchanged. A log-limit termination is incomplete execution evidence, never a test pass. Reconcile ownership/process termination before a bounded replacement work item.

Latest selected guard/ownership/reconciliation verification:46 passed,1 existing symlink-privilege skip. This is not a full Bridge-suite pass claim; historical broader-suite gaps remain recorded.
