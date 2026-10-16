# Historical artefacts (pre-remediation) — labelled, preserved, superseded

| File | What it is | Status |
|---|---|---|
| `REVIEW_PACKAGE_pre_remediation.md` | review package of the pre-remediation candidate incl. the sandbox-lock diagnosis narrative | historical report; superseded |
| `../telemetry_live_smoke.jsonl` | real telemetry of the first live smoke run (broken sandbox) | historical report |
| `../telemetry_live_smoke_repaired.jsonl` | real telemetry, repaired sandbox, pre-remediation code | historical report |
| `../telemetry_live_smoke_preflight.jsonl` | real telemetry, preflight active, pre-remediation code | historical report |
| `../telemetry_examples.jsonl` | fake-CLI examples from the pre-remediation candidate | historical fixture output |
| `../v1_v2.diff`, `../v2_inventory.txt` | v1 vs v2 diff/inventory generated for the earlier candidate | regenerated each pass; see root listing |

**Rule:** results above were produced by code that has since changed. They are *context*, not
*evidence for the current source hash*. The live runs in particular (smoke 16/16, attack
13/13) exercised the pre-remediation code and must be re-run if they are to support claims
about the current code (proposed, not executed: REVIEW_PACKAGE.md §7).
