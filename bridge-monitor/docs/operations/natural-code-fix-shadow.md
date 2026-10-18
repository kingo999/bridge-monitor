# Natural test-backed code-fix shadow cohort

This is an offline measurement workflow. It does not alter the running bridge, its routing policy, or the dashboard's live cohort. The target is 30 distinct new, small, single-file Python fix attempts with trusted tests. The 18 October retrospective probes do not count: their IDs, task hashes, and baseline hashes are excluded by the configured cohort contract, and captured tasks must fall after its start time.

For each real task, retain the original request, broken file, and tests as they existed before the fix. After a paid or independently trusted solution is available, add its passing replacement file as `reference_source`. Save one JSON array containing one case under `var/probes/natural-code-fix-cases/<case-id>.json`:

```json
[{"id":"<unique case ID>","task":"<original request>","baseline_source":"<complete broken Python file>","reference_source":"<complete passing Python file>","tests":[{"function":"<callable>","args":[],"expected":"<expected JSON value>"}],"provenance":{"origin_ref":"<original task or issue ID>","captured_at":"<ISO timestamp with timezone when the task arrived>","baseline_ref":"<baseline Git revision or source snapshot ID>","test_ref":"<trusted test revision or source ID>"}}]
```

The provenance fields are declarations for later manual audit, not proof of natural origin. Record a distinct `origin_ref` for each task; it also determines the reproducible arm order. The verifier runs the reference and broken baseline before any shadow dispatch; it requires the reference to pass and the baseline to fail. Qwen and Sonnet receive the same task and broken source, not the reference or expected outputs. The stronger full-file prompt is in `config/code-fix-natural-shadow.toml`, and its exact content is pinned in each receipt. Keep that config, model profiles, and harness revision fixed for one cohort; mixed protocols are rejected before a new dispatch.

Run one case with an explicit receipt name under the ignored project-local receipt directory:

```powershell
.\.venv\Scripts\python.exe scripts\run-code-fix-shadow.py --settings config\code-fix-natural-shadow.toml --cases var\probes\natural-code-fix-cases\<case-id>.json --receipt var\probes\natural-code-fix\<case-id>.receipt.json
```

Run `--dry-run` first with the same arguments to check config and model pins without dispatch. A failed or uncertain run leaves its receipt marked in progress and is never replayed automatically. Preserve it for manual investigation. Do not rename historical fixtures or copy their outcomes into the natural cohort.

To recompute the cohort report from completed receipts:

```powershell
.\.venv\Scripts\python.exe scripts\summarize-code-fix-cohort.py
```

The report is saved at `var/probes/natural-code-fix-summary.json`. It checks receipt and result digests, includes interrupted attempts, rejects duplicate origin, task, baseline and case ID, and reports verified Qwen delivery, fallback frequency, model and token coverage, arm-order split, and measured worker-path time including verification. Latency and the Sonnet CLI list-cost difference are compared only when both arms pass independent verification; missing or non-list cost leaves the difference unknown. The GPT CLI proxy receives only a task hash and result metadata; its usage is not the real desktop parent's usage. The Sonnet estimate excludes Qwen electricity, verification, and parent cost, so net billed savings remains unknown. Digests detect accidental changes, not deliberate edits by someone able to rewrite a receipt.

At 30 attempted cases with no interrupted receipts, the report becomes **ready for review even if some fixes fail**. That flag means the cohort is due for assessment, not that Qwen passed a quality or savings gate. It never authorizes a live canary. Review the original-task provenance against the real task list, independent tests, failures, pass and fallback rates, arm-order split, latency coverage, and paid-cost evidence. Local receipts alone cannot prove that an omitted or deleted task never existed. A reviewed activation policy and rollback are separate work.
