# Bounded Bridge self-tests and worktrees

The existing `run_checks` repository tool accepts `kind="bridge-selftest"` only
for a host-granted repository with a `selftest_profiles` entry. It does not grant
a shell, Git writes, downloads or live-model calls. Build/test code still runs as
the host user; neither worktrees nor command recipes provide an OS sandbox.

Fixed goals: `backend-unit`, `backend-integration`, `dashboard-typecheck`,
`dashboard-build`, `dashboard-e2e`. Backend selectors are comma-separated paths
under `tests/`, optionally followed by simple `::Class::test` node IDs. A goal
cannot add CLI flags. The unit default covers contracts, router and adapters;
the integration default covers integration and query tests. Callers can narrow
these groups within their task budget. E2E selectors are comma-separated existing
`*.spec.ts` basenames under `dashboard/tests/` (for example `selftest-smoke.spec.ts`).

Profiles pin an absolute existing Python executable, optional Node executable,
and optional `test_dsn_file` pointing to operator-protected JSON containing `dsn`.
The DSN must name a local `bridge_monitor_test` or `bridge_monitor_test_*` database.
Passwords in the DSN are rejected: use a protected PostgreSQL passfile. Provision
a dedicated non-superuser database owner; never reuse the operational database.
Only integration/E2E subprocesses receive the test connection settings. Self-test
processes receive a reduced environment and a fresh home directory, excluding
Artifactory/provider variables and default operator passfiles. Windows browser tests
retain `USERPROFILE` for installed-browser discovery; `APPDATA` remains isolated
and Playwright uses a temporary browser data directory. This is not filesystem
isolation. Dependencies and browser
artifacts must be provisioned by the operator before running checks.
An optional `browser_channel` (`chrome` or `msedge`) selects an installed browser
without downloading Playwright's browser bundle.

Pytest JUnit and Playwright JSON reports supply `test_counts` in run receipts.
`verification` is `failed`, `partial` (skips), `completed`, or `unknown` (missing
test evidence). Exit code zero alone is not proof that every test ran.

## Exact, expiring worktree grants

Create a worktree through parent orchestration, not worker Git tools. In a
reviewed runtime copy, register its exact **project subdirectory**, an explicit
write-prefix list, an origin project and a timezone-aware expiration:

```toml
[repository.roots]
bridge-monitor = 'C:\AI\bridge-monitor'
task = 'C:\AI-worktrees\example\bridge-monitor'

[repository.write_prefixes]
bridge-monitor = ['bridge/', 'contracts/', 'service/', 'dashboard/', 'tests/']
task = ['contracts/', 'service/', 'dashboard/', 'tests/']

[repository.worktree_origins]
task = 'C:\AI\bridge-monitor'

[repository.worktree_expirations]
task = 2026-11-02T12:00:00Z # Example: set a fresh, task-bounded expiration.
```

Both Git roots must share the same common directory, be distinct checkouts and
have the same relative project path. Source and task grants intersect; origin
exclusions and source ownership locks remain in force. Exact worktree roots
inherit the origin's check profile; unregistered sibling checkouts do not.
Expirations are checked at binding and during tool operations. Expired task roots
are retired on configuration load, so other roots remain available. Remove task
registrations before archiving worktrees; live absent roots fail closed.

The existing launcher supports `--runtime-config config/<task>.local.toml` with
`--task-json` and `--receipt-json`. The runtime must be an existing project/config
TOML; this selects reviewed capabilities for the same Bridge, not a new router.
Use a new task/work-item identity for each changed checkpoint; reconcile any
uncertain call before replacement dispatch. No active calls are restarted by
this option. Existing desktop MCP processes retain their loaded capabilities
until restarted under the normal ownership/reconciliation procedure.

## Bootstrap evidence (2026-11-01)

User approved a one-time direct Codex fallback to implement this prerequisite.
Worktree: `C:\AI-worktrees\bridge-selftest-support`. Base: `1dcf14a`.
New command/grant tests were observed failing before their implementations.
Repository baseline: 73 passed, one platform skip. Broader integration testing
found 13 existing `test_validation_export.py` failures; the zero/partial snapshot
failure was reproduced at the unchanged base checkout. They are outside this
capability change. A test-only password printed by pytest was rotated and
replaced with passfile authentication; raw-password DSNs are now rejected.

## Review decisions

Enforced read-only review: `d8acbeaf-bba7-4cfa-9c21-749ae975d0ae`; source
snapshots were unchanged. A single regression-test fix pass addressed expired
registrations blocking startup, credential-bearing inherited environment,
verification failures losing receipts, descendant-prefix comparisons, inline
SSL passwords, and failed test counts with exit code zero. Focused E2E selection
was added after the legacy full UI suite hit its bounded timeout.

- Ruling: retain the pre-existing host source/config grants needed for trusted
  Bridge development. Task grants remain narrower; the feature task cannot edit
  the enforcement code or runtime configuration. Activation still requires
  parent review. An overly broad future task grant would require that review.
- Ruling: do not fix the unrelated validation-export baseline failure here.
  General usage/export verification remains limited by that existing failure.
- Deferred: DSN role privileges and the selected loopback port remain operator
  provisioning responsibilities; the supplied task role is not a superuser.
- Deferred: build/test subprocesses can write outside file-tool prefixes and
  their reports are not tamper-proof. OS isolation is separate work.
- Deferred: the fixed default test groups are not the entire project suite;
  third-party pytest plugin autoload remains off. Use explicit allowed selectors
  and the correct test goal, and read coverage/skips in each receipt.
- Deferred: the launcher's pre-existing outside-project task-file traceback is
  unchanged. Runtime capability selection intentionally does not alter the
  active database routing/model policy.

Final prerequisite checks through the fixed recipes: backend unit groups 554
passed / 71 explicitly reported skips; targeted database ownership and parent
acknowledgement integration 19 passed / no skips; dashboard typecheck and build
passed; isolated browser smoke 1 passed. The full legacy browser suite reached
its 180-second bound; existing tests expect the older default Experiments view.
The new focused smoke uses the current Codex/Claude view. No operational DB was
used for fixture mutation, and no dependencies or browser bundles were fetched.
