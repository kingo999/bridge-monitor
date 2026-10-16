# Remediation checklist (written before implementation)

Scope: isolated v2 candidate only. v1, global Codex config/login/CLI and desktop processes
are out of bounds. No registration, activation, commit, or live model call in this pass.
Pre-remediation candidate snapshot: `.baseline_pre_remediation/` (44 files, combined sha256
`cb02922b…4235f`, see its `MANIFEST.json`).

## Evidence gathered first (installed CLI 0.162.1, no model calls)

| Fact | Source | Consequence |
|---|---|---|
| `codex features list --disable X …` prints the **effective** state (`multi_agent`, `image_generation`, `plugins` → `false`) | CLI run | feature gates can be **resolved-config verified** model-free |
| `--strict-config` is not accepted by `features list` / `mcp list`; unknown `-c` keys are silently accepted | CLI run | a `-c` key cannot be validated by the CLI → "requested", not "enforced" |
| `Config.web_search ∈ {disabled, cached, indexed, live}`, `Config.tools.web_search`, `ConfigRequirements.allowedWebSearchModes` exist | `codex app-server generate-json-schema` | `web_search="disabled"` is a documented key (not invented) but its effect on the `web__run` tool is unverified |
| Feature flags `multi_agent`, `multi_agent_v2`, `image_generation` exist | `features list` | delegation + image-generation controls exist and are used |
| `debug prompt-input` text still mentions `spawn_agent`/sub-agents with delegation disabled | CLI run | prompt text is not evidence of tool removal; only a live tool inventory is |
| A failed command in a real turn is a `CommandExecution` item: `status:"failed"`, `aggregated_output:"Failed to create unified exec process: helper_unknown_error: setup refresh had errors"` | rollout of the earlier failed smoke thread | F1 detector keys on command/tool items, never on agent narration |
| `codex-windows-sandbox-service.exe` is a Windows service; sandboxed commands may start outside our process tree | process list | a Job Object cannot confirm termination of service-spawned helpers → stated limit |

## Finding → work item → proof

| # | Work items | Proof (automated, no inference) |
|---|---|---|
| **F1** | actual-turn infra-failure detector on command/tool items (setup, process creation, repo access); fail closed by default, explicit `degraded` mode; preflight gains a source-file read stage; docs: preflight ≠ exec, `git rev-parse` ≠ readable sources | regression fixture helper_unknown_error + turn.completed + plausible answer ⇒ failure; ordinary nonzero exit ⇒ success; narration containing the phrase ⇒ success; degraded mode banner |
| **F2** | exact-version baseline; strict schema parsing; required controls fail closed; resolved-state verification of feature gates; delegation + image generation + web separated and reported by evidence level; allow-list visible in telemetry **and** returned warning; no web exception mechanism (policy pending) | tests for each fail-closed path, evidence-level report, allow-list warning |
| **F3** | Windows Job Object ownership (KILL_ON_JOB_CLOSE, TerminateJob, active-process accounting); in-flight marker; `uncertain` state blocks reuse until `reconcile`; attempted vs confirmed termination wording; never kills unrelated processes | real process-tree tests: timeout, cancel, lingering descendant after parent exit, **bridge hard-kill reaps children**, uncertain-state blocks reuse |
| **F4** | `instance.json` + `registry_established`; loss ≠ first init; `registry.prev.json`; maintenance flag + quiescence (lock probes) for quarantine/reinit; in-flight bind after quarantine cannot repopulate; lock-scope docs | tests: lost registry refuses, first init allowed, quarantine blocked by active call, no repopulation |
| **F5** | explicit outcomes (`success_partial_persistence`, interrupted, uncertain); pending-reconcile marker blocks silent new thread; `adopt`/`reset`; journal with thread IDs, no content; honest "Codex history may contain turn" wording | tests for save failure → marker → refuse → adopt; wording assertions |
| **F6** | repo fingerprint (HEAD + status + diff hash + untracked stat) before/after with `--no-optional-locks`; changed-during-review warning; telemetry hashes only | tests: change during call detected, no content in telemetry |
| **F7** | per-attack matching (kind+path), specific rejection regex, exact counts per turn, resumed thread identity, auth+compat before live inference, labels (production argv vs service path) | negative-oracle unit tests: missing, generic failure, duplicate, wrong thread, changed files |
| **F8** | new `REVIEW_PACKAGE.md`; old one archived and labelled superseded; source fingerprints; evidence bound to hashes/commands/CLI/backend/policy; evidence classes; bounded read-only claim; unverified list | `tools/fingerprint_sources.py`, `docs/evidence/*.json` |

## Explicitly NOT done (needs human or live authorization)

D1/D3/D8 decisions, activation, registration, any live model call (including the live
attack stages), changes to v1 or global Codex configuration.
