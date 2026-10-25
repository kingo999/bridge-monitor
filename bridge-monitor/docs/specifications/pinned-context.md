# BM-06 minimal pinned focused context

Goal: preserve exactly the caller-selected, bounded context for one task/work
item and one described module, and detect substitution or drift against supplied
expected content pins. Selection is explicit; relevance ranking is outside this
slice. Module catalogue/status remains descriptive.

## Existing systems and scope

The existing `bridge/guidance/registry.py` already registers immutable guidance
packs and content hashes; `bridge/guidance/selection.py` performs policy-aware
guidance selection and prompt assembly. This slice leaves them in place. It does
not add a registry, retrieval service, router, policy mechanism or deployment
binding, or change `TaskPacket.context` and runtime prompt assembly.

`contracts/pinned_context.py` adds frozen, extra-forbidden contracts for an
expected item pin, its exact UTF-8 bytes and a verified bundle. It reuses the
existing Identifier/Sha256 and BM-05 manifest types.
`service/queries/pinned_context.py:build_pinned_context` reuses the existing
`ContentObject` integrity validator, reconstructing mutable source instances so
copied or constructed invalid instances are checked again. It consumes supplied
objects only, with no file/network/database access or dispatch.

## Inputs and behavior

The caller supplies task/work item IDs, a module manifest, an independently
expected manifest SHA-256, ordered expected `ContextPin` entries, supplied
`ContentObject` instances and a byte budget. Expected pins must come from the
caller's reviewed snapshot. Computing fresh expected hashes from changed source
and accepting them is a new selection, not verification of the original snapshot.

Each pin binds content ID, exact byte length and SHA-256. The manifest hash covers
its entire normalized model JSON with sorted keys, compact separators and ASCII
escaping, encoded as UTF-8. Module ID/version alone does not establish immutability.
The version-1 context digest covers task/work item IDs, manifest digest and ordered
item pins using the same canonical JSON encoding. It transitively binds item
bytes. Source whitespace/newlines are preserved; UTF-8 content is not normalized.

The returned bundle carries the manifest, exact bytes and all pins. Model
validation checks item hashes/lengths, manifest digest and bundle digest, including
after JSON round-trip. Frozen models prevent normal assignment; consumers must
validate objects received from untrusted sources rather than bypassing Pydantic
with `model_construct` or treating an unchecked `model_copy` as verified.

Bounds are 1–32 selected items, 1–65,536 bytes per selected item, 262,144 bytes
total and at most 256 supplied objects. The caller's smaller positive byte limit
is enforced. Missing, incomplete, non-UTF-8, duplicate, changed or oversized
selected content raises an error. Nothing is silently substituted or truncated.
Unselected supplied objects are validated but do not appear in output; their
metadata does not become bundle provenance. Source collection order does not
affect output; selected order does.

BM-07 review follow-up: inputs must now be plain lists or tuples; lazy iterables
are rejected without consumption. All supplied bodies, including unselected
ones, are capped at 65,536 bytes each and 262,144 bytes aggregate before source
revalidation/hashing. Supplied encoding labels must explicitly denote UTF-8
(`utf-8`, `UTF-8`, or `utf8`). Role/origin and other source metadata remain outside
the content pin. Tests now include a fixed version-1 context digest and exact
JSON round-trips for CRLF, BOM, NUL, accented and astral text. These checks do not
turn arbitrary caller Python objects or model contents into a security sandbox.

Digests establish identity against trusted expected pins, not authenticity,
authorization, safety, relevance, current source state or model delivery. The
bundle digest is not a signature; downstream consumers must compare it with their
independently retained expected digest if they require the original selection.
This slice does not prove a worker consumed the bundle. Historical source lookup,
status evidence inclusion, persistence and runtime adoption remain separately scoped.

## BM-05 review constraints

1. `CatalogueStatus` now rejects true/false without source and timestamp, unpaired
   provenance and freshness claims on absent evidence. Observed `unknown` remains
   valid with paired provenance. Freshness is computed only by the projection;
   the model alone cannot validate a caller's clock or age rule.
2. Manifest content receives its own expected digest in BM-06. Reusing a version
   with changed summary fails verification. The BM-05 note explicitly distinguishes
   identity labels from content pins.

## Validation

Reproduce with the existing virtual environment from the project root:

```powershell
.venv/Scripts/python.exe -B -m pytest tests/contracts/test_catalogue.py tests/contracts/test_pinned_context.py -q -p no:cacheprovider
.venv/Scripts/python.exe -B -m pytest tests/contracts tests/operations tests/queries tests/integration/test_guidance.py tests/integration/test_policy_pins.py -q -rs -p no:cacheprovider --basetemp=var/bm06-final-tests-20261022
```

The new catalogue tests first exposed seven provenance-validation failures; the
initial BM-06 tests failed collection because the new module did not yet exist.
Implementation followed those failures.

Actual final results on 2026-10-22: **84 focused tests passed in 0.22s**
(38 catalogue/provenance and 46 pinned-context cases). The selected regression
run reported **256 passed, 16 skipped in 1.11s**. All skips require a dedicated
`BRIDGE_MONITOR_TEST_DSN`: eight worker-waiting cases, six guidance cases and two
policy-pin cases. No database or runtime integration success is claimed.

| Goal check | Actual result |
| --- | --- |
| Manifest ID/version cannot substitute for a content pin | Changed summary under same version rejected |
| Preserve exact selected bytes | Hash/length checked; whitespace/newline tests passed |
| Bind focused selection to task and order | Task/work item, content ID and selected-order tests passed |
| Detect drift and incomplete inputs | Missing, modified, incomplete and non-UTF-8 inputs rejected |
| Enforce bounds without truncation | Per-item, aggregate, selection count and supplied-object caps passed |
| Retain integrity across transport | JSON round-trip passed; tampered bundle and nested instance tests rejected |
| Require status provenance | Seven previously failing incomplete-provenance cases now pass |
| Keep materialization pure | File/socket/process traps passed; inputs preserved |

Change verification retained a before-hash baseline in tool memory, avoiding the
temporary-file gap from BM-05. Across contracts, service, bridge, config, database,
tests and docs (excluding bytecode caches), 361 original files were checked:
358 are unchanged and three changed as intended (`contracts/catalogue.py`,
`tests/contracts/test_catalogue.py`, `docs/specifications/module-catalogue.md`).
Four files were added: the pinned-context contract, projection, tests and this
note. No original files were removed. This is a comparison of this task's
starting workspace, not proof about the earlier BM-05 change boundary. Git still
fails while resolving `C:/AI`; no commit/diff verification is claimed. Test scratch
files under `var` are outside this source hash comparison.

Rollback: stop importing the opt-in pinned-context API and remove its additive
contract/projection/tests/note. The catalogue provenance validation is a separate
hardening change; reverting it reopens the reviewed gap. There are no migrations
or activation steps.

Execution: direct Codex, explicitly authorized by the user. The Bridge worker
cannot read this checkout or execute repository changes/tests. Monitor recording
is accounting only and is not an execution route.
