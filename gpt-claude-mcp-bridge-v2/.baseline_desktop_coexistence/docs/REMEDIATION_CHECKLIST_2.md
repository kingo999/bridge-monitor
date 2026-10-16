# Remediation checklist 2 (written before implementation)

Trigger: GPT's review of the first remediation, `docs/gpt_review/gpt_review_20261012T135823Z.md`
(verdict: F1-F8 PARTIALLY CLOSED; N1-N15 new). Authorised by the human: remediate N1-N15 and
include P3 (remove `degraded` from the production success path); then **prepare** (not send) a
verification prompt. Same boundaries: isolated v2 only, no live calls, no registration.
Diff base: `.baseline_post_remediation1/` (state GPT reviewed).

## Facts established first (model-free, installed CLI 0.162.1 / git 2.51.2)

| Fact | Consequence |
|---|---|
| `git --attr-source=<empty tree>` is supported | host-side git can ignore `.gitattributes`-selected filters/textconv/diff drivers (N1) |
| `-c mcp_servers={}` has no effect on `codex mcp list` | **no supported MCP deny-all exists** (N8): residual config-change race can only be narrowed (re-read right before exec, report post-turn change) and must be documented |
| Rollout has **no separate nested `apply_patch` records**: only the `exec` wrapper call + its output | N9 cannot be fixed by "authoritative nested records"; fix = static checks on the wrapper + comparison of the attempted patch text + honest evidence label ("wrapper-attested") |
| `CREATE_SUSPENDED` + job assign + `NtResumeProcess` works; grandchild created after resume is a job member | assignment window (N3) is closable; refuse inference if ownership cannot be established |

## Work items

| Finding | Change | Proof |
|---|---|---|
| N1 | `bridge/gitsafe.py`: single place for host git argv/env (`--no-optional-locks`, `--attr-source=<empty tree>`, `core.fsmonitor=false`, `core.attributesFile=<null>`, `diff.external=`, `--no-textconv --no-ext-diff`, env scrub); used by fingerprint and preflight file selection | control test: a repo whose `.git/config`/`.gitattributes` define textconv, clean filter and fsmonitor helpers; helper fires with plain git, **does not** fire through gitsafe; recursive `.git` before/after |
| N2 | `validate_state_dir` before every admin state access (v1 overlap unconditional; repo overlap when a repo is known); status stops probing a rejected dir | tests for each admin command with state dir = repo / = v1 / inside v1 |
| N3 | `CREATE_SUSPENDED` handshake; runner **refuses** inference when ownership is unavailable (child killed before any prompt byte is written); timeout/cancel with unobservable sandbox-service children ⇒ session in doubt until `reconcile` (P2) | tests: assignment failure + normal exit refuses; timeout/cancel require reconcile |
| N4 | whole post-`Popen` lifecycle exception-safe; stale-marker auto-reconcile requires verified owner death (pid + process creation time) and an unfinished/unbound phase check | tests: setup exception after attach; live owner; pid reuse |
| N5 | exclusive maintenance (OS lock + `O_EXCL`-style flag, owner-only clear); `new/reset/adopt/reconcile` refuse during maintenance and hold the session lock | tests with barriers: two maintenance owners, mutation during maintenance |
| N6 | in-flight marker must be written before inference; kept until pointer committed; `completed_unbound` phase; pending/uncertain marker failures keep blocking state; `_ensure_instance` errors become `BridgeError`; banner fixes (verified "already references") | crash/failure injection tests |
| N7 | top-level `error` events and oversized lines classified; no 8 KiB prefix blind spot; wider repo-access signatures; uninterpretable evidence handled | fixtures |
| N8 | allowed servers from the **final** readout; re-read immediately before exec; post-turn re-read warns; documented residual race; resolved evidence labelled as snapshot-scoped; allowed servers reported `uncontained` | config-mutation fixtures |
| N9 | static wrapper checks (single `tools.apply_patch(` call, no `throw`/`Error`/literal rejection text in input), attempted patch text compared with expected, evidence label | forged-wrapper negative fixtures |
| N10 | instance identity: absent / unreadable / invalid are distinct and fail closed; backup schema+identity validated on restore | tests |
| N11 | separate `--cached` and worktree diff streams (unborn-safe); truncation/incomplete read ⇒ unavailable | tests |
| N12 | remove generic `Failed to write file` as conclusive; stage A: per-hunk attacks in separate runs, timeout/launch failure inconclusive | oracle tests |
| N13 | source probe uses `findstr` (no shell reparse), unsafe names excluded + reported, source-extension preference, accurate wording | tests incl. `a&ver` |
| N14 | thread ids validated at ingress; telemetry nested fields validated against fixed formats | tests with path/source-like ids |
| N15 | unavailable fingerprint / skipped probe return a banner + warning | tests on returned text |
| P3 | `degraded` mode removed; warnings prepended as a flag line + details | tests |
| GPT §3 | duplicate feature rows rejected; evidence wording ("implementer-executed"); README/§refs/rollback wording | doc review |

## Not done / not authorised

Live model calls (including sending the verification prompt), registration/activation, D1/D3/D8.

## Addendum - found while implementing and while preparing the package (not in the plan above)

| Item | What was found | Change |
|---|---|---|
| Exit accounting | `reaped_descendants:1` appeared intermittently (1 run in 6) on a normal exit: the Job Object count lags process teardown by ~4 ms (measured 40/40 runs) | `EXIT_ACCOUNTING_GRACE_S = 0.5` before a member is called a survivor; two tests |
| N12 stage A | the model-free stage still passed on "non-zero exit + names the path", which a disk-full failure also satisfies | `judge_os_attack` additionally requires an OS access-denial code |
| N4 owner liveness | `process_identity` returned None for ANY `OpenProcess` failure, so an access-denied query read as "owner dead" | only "no such process" or an exited process is dead; unreadable = alive; tests incl. PID 4 |
| Spawn failure | `spawn_failed` (no process ever existed) marked the session `uncertain/runner_failed` | treated like `ownership_unavailable`: nothing ran, session not blocked; test |
| N1 control | the first control test used `;` inside a git config value, which git treats as a comment, so the helpers never fired and the test skipped | helper script outside the repo; control now required to fire; mutation test added |
| Messages | `registry_identity_unverifiable` recovery text was "inspect" | actionable quarantine/reinit/adopt text |
