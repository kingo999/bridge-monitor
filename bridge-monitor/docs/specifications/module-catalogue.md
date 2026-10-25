# BM-05 minimal descriptive module catalogue

Goal: make versioned module descriptions and the evidence for their reported
statuses inspectable without granting authority or invoking a module.

BM-06 follow-up: an ID/version is a caller label, not an immutable content pin.
The opt-in pinned-context API separately verifies a digest of manifest content.
Public `CatalogueStatus` now requires paired source/timestamp provenance for any
observed status, including an observed `unknown`. An absent status has value
`unknown` and no provenance or freshness claim. The projection remains the
authority for computing freshness from its supplied clock and age rule; directly
constructed statuses do not independently establish temporal accuracy.

`contracts/catalogue.py` defines a frozen manifest with module ID, positive
integer version and bounded summary. Status evidence is separate, keyed by the
exact module/version pair. Each of discovered, installed, probed, enabled,
healthy and eligible independently carries a strict boolean or `unknown`, a UTC
observation timestamp and a source reference. Source references are caller
assertions, not authenticated receipts. No real modules are asserted by default.

`service/queries/catalogue.py:project_catalogue` is an opt-in in-memory API.
It reuses the existing operation Identifier and UTC timestamp types. It returns
immutable rows sorted by module ID/version. Missing evidence remains unknown;
equal statuses are permitted. No status implies another. Eligibility remains an
external observation with no effect on routing or permission.

Callers supply `now`. Without `max_age`, freshness is unknown. With a nonnegative
timedelta, evidence is stale only when its age is strictly greater than that
limit; values, timestamps and sources survive unchanged. Future evidence is
rejected. Duplicate manifest or evidence identities and evidence referring to an
absent manifest version are rejected. Callers must select one evidence record per
module/version; historical reconciliation is outside this slice.

The existing operation lifecycle, routing policy, classifier scenario catalogue,
deployment and limits contracts retain their roles. There are no changes to those
systems, SQL, runtime consumers or provider selection. No operation effects,
deployment bindings, probes, dispatch or execution fields are introduced.
Rollback is to stop importing the opt-in API and remove these additive files.

## Actual validation, 2026-10-22

Run from the project root using the existing virtual environment:

```powershell
.venv/Scripts/python.exe -m pytest tests/contracts/test_catalogue.py -q
.venv/Scripts/python.exe -m pytest tests/contracts tests/operations tests/queries -q --basetemp=var/bm05-regression-tests-20261022
```

Focused result: **30 passed in 0.19s**. Combined result: **202 passed,
8 skipped in 1.12s**. All eight skips require a dedicated
`BRIDGE_MONITOR_TEST_DSN`; no database integration success is claimed.

| Goal check | Observed result |
| --- | --- |
| Absent evidence is unknown | Passed for all six statuses |
| Healthy and disabled remain independent | Passed; eligibility stays unknown |
| Independent provenance and freshness | Passed with different sources and ages |
| Explicit freshness boundary | Passed at 59, 60 and 61 seconds for a 60-second rule |
| Deterministic, immutable view | Passed with reversed manifest order |
| Invalid identity/input rejection | Passed for duplicates, wrong versions, coercions and constructed invalid instances |
| Descriptive fields only | Passed rejection checks for command, dispatch, execute, policy and enabled manifest fields |
| Pure projection | Passed with file open, socket creation and subprocess launch blocked; inputs preserved |

The initial focused run failed collection because the new contract did not yet
exist; implementation followed that failure. An initial broader run produced
188 passes and two temporary-directory setup errors. Selecting a writable local
pytest base directory resolved those errors without changing product code.

Verification limits: this workspace has no `.git` directory and Git status fails
while resolving its parent path. A pre-change hash baseline of 159 existing
files was written to a temporary directory that was not visible to subsequent
tool calls, so before/after hash verification could not be completed. The edits
performed for this task add only the contract, projection, tests and this note.
Representative I/O traps and source inspection support purity; they are not an
exhaustive sandbox guarantee. No live module readiness or authorization is proven.

Execution was direct Codex under the user's explicit instruction. The Bridge
worker cannot read this checkout, execute repository changes or run these tests;
the Bridge metadata recorder was used only for execution accounting.
