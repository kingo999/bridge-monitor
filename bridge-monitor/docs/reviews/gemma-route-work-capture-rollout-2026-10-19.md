# Controlled arrival-capture rollout — 19 October 2026

The user authorized merging the verified branch and continuing a controlled rollout in auto
mode while preserving the live routing policy. `codex/gemma-route-capture` was merged by
fast-forward into `codex/skynet-task-status` at `1603286e109b85e7b38819c27c1d4822dbd1784a`.
No remote push was performed. The design draft and unrelated IDE/project files were unchanged.

The advisory model recommendation was Claude Opus because the rollout crosses migration,
process-ownership and failure-isolation boundaries. No model setting was changed. The single
paid canary used the existing live policy's Sonnet profile.

## Deployment and evidence

- Standard private custom-format backup: `var/backups/hook-rollout-20261019.dump`, 2,071,187
  bytes, SHA-256 `d3b63ec1207b99348bdaca0b919de623a3c0aec4082a8af245e04c417cd4b75a`.
  Checksum and `pg_restore --list` catalogue were verified. A restore into a newly created
  disposable test database reproduced every recorded table count, migration 007 and the
  policy hash; only that invocation's disposable database was then removed.
  The first Windows PowerShell subprocess could not load its security module. Retrying the
  unchanged backup script with the task's working PowerShell runtime preserved its ACL checks.
- Only migration `008_classifier_route_capture` was pending. It was applied with a one-second
  lock timeout and ten-second statement timeout. All existing table counts stayed unchanged;
  the new budget started at zero. The partial index occupied 8 KiB initially. Database size
  increased by 114,688 bytes at this step. Logical quotas do not bound physical database/WAL size;
  cluster WAL also includes concurrent dedicated-test-database activity.
- A project-local configuration first had the hook disabled; parent bootstrap returned no
  observer. After enablement, a fresh real stdio MCP parent handled exactly one controlled case.
  Existing desktop parent connections were not terminated. Docker Desktop was started because
  its configured local verifier daemon was initially stopped; baseline registration then
  passed the required failing-baseline check without changing the verifier settings.
- The production dashboard backend was restarted through its checked project process receipt.
  Its existing task supervisor restarted it. The static dashboard was rebuilt. Live reporting
  returns one manual observation and one hook observation; unauthenticated requests remain 401.

Local receipts and scripts are retained in ignored `var/hook-rollout-20261019/`. Protected
credentials were loaded in memory, never added to source or printed. Capture configuration is
`var/hook-rollout-20261019/capture.local.toml`.

## Verification on the merged checkout

- Full Python suite: **580 passed, 2 skipped**, 337.93 seconds, dedicated test database only.
- Browser regression: **4 passed**, installed Chrome; capture filters, production/Qwen
  separation, compatibility and authenticated operator session.
- Dashboard `npm run typecheck` and the ordinary `npm run build` both passed. The main
  checkout's normal dependency directory avoids the earlier worktree junction build limit.
- The controlled paid answer passed trusted container tests. Read-only accounting confirms
  exactly one model execution despite the recovery request. Git whitespace checks passed.
- The original design SHA-256 remains
  `1b8325f4940d1312cc0819804a41062f14f863fdcf95ef4f6d392646cba3337e`.

The first merged-suite invocation produced 577 passes and three existing CLI test failures:
Windows' default external temporary directory violated those CLIs' project-local file guard.
All four CLI tests passed with project-local temporary files; the full suite was then rerun
with a fresh project-local `--basetemp` and passed. No source or guard was relaxed. Both full
logs and JUnit receipts are retained. The two existing skips are unavailable host symlink
creation and the explicitly opt-in live-model pilot. The existing TestClient deprecation
warning remains. No new live-model opt-in was supplied to the test suite.

## Controlled canary

The new registered case was explicitly labelled **controlled**, using the clamp fixture. A
one-call task budget and a direct-answer instruction bounded the exercise; these were task
inputs, not routing policy changes. One real Sonnet execution completed and delivered a correct
module in 6.23 seconds. Its answer passed the pinned container verifier's trusted tests.

| Identity | Value |
| --- | --- |
| Task | `cfa93492-2149-4e4b-8c2f-ba62060d5321` |
| Work item | `0f76d86c-d12b-4213-86be-ceeca201207d` |
| Paid call | `f3ddf935-3025-5f5a-ab31-31256828e5c9` |
| Exact production decision | `1edaccd1-8bb4-5276-bd00-8e79b9cd9d8c` |
| Hook observation | `642d1218-1a7d-5fb5-8995-941d580818c7` |

Capture occurred at 18:59:50.468 UTC, before delivery at 18:59:56.418 UTC. Protected request and
source bytes match their hashes; the snapshot excludes hidden tests and the answer. Code
capacity remains unknown. An ordinary recovery request returned the same delivered answer
without another model execution or capture. Durable quota is **1 row / 574 bytes**, with no reset.
No Qwen job was admitted, and no Qwen or Gemma inference was made for this rollout.

A read-only exact-binding lookup matched the production paid decision. The observation is
still `captured`, with zero attempts, no response and SQL NULL `actual_route`: no classification
result or completed-row late fill was fabricated. One real runner iteration with Gemma stopped
returned `endpoint_unavailable`, reported no reconciliation errors and claimed/sent nothing.
Completed-row late linking remains covered by the merged regression suite, rather than this
unclassified live canary.

## Activation and rollback

Only `mcp_servers.bridge_monitor.env.BRIDGE_CLASSIFIER_SHADOW_CONFIG` was added to the user's
Codex configuration. Parsed before/after comparison confirmed every other setting was
unchanged; the original file was preserved beside it as
`config.toml.before-gemma-capture-20261019`. Server-scoped `env` is documented in
[OpenAI's MCP configuration reference](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

The canary parent was closed cleanly. The existing desktop parents predate this change and
retain their original startup snapshots. Subsequent Bridge Monitor connections will opt in;
this is not evidence that capture is already active in every existing chat. No desktop-wide
restart was forced. Capture stays bounded by the unchanged 8-item pending queue, 256-row/4-MiB
lifetime budget, two-second rate limit and ten-minute maximum age. The classifier runner is
not scheduled, and Gemma stays stopped.

The disabled startup path was checked again after the canary, and quota survived unchanged;
the reviewed enabled file was then restored. To disable future capture, set only
`route_work_capture.enabled = false` and restart the affected parent connections. Editing
alone cannot stop their current snapshots. Preserve 008, observations, quota and progress.
For an older-code rollback remove the entire new TOML section before using the older strict
loader. Do not run migration 007's destructive rollback.

The active policy remains version `43a200ea7bf9f73a`, hash
`43a200ea7bf9f73aad0cd5f642e5b51230ffd405f275342a81bc911b906770b2`, with four activations.
No policy, routing thresholds, scheduling, pool limits or existing shadow-worker task changed.
