# Bridge Monitor Candidate Operation Reader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. The user selected direct Codex execution. Do not dispatch subagents or re-enter parent Bridge routing for this authorized task.

**Status:** ready for user review; implementation has not started.
**Goal:** create a real candidate operation contract and an opt-in reader for historical/candidate compatibility tests while preserving the operational bridge.
**Architecture:** add two independent contract modules. Legacy records use the unchanged EventEnvelope; operation records use a strict schema-2 discriminated union. An immutable tagged result forces callers to distinguish them. Production consumers keep their existing readers.
**Tech Stack:** existing Python >=3.13, Pydantic 2.14.1 and pytest 9.1.1. No new dependency.
**Spec:** [Approved design](../specs/2026-10-22-candidate-operation-reader-design.md), approved by the user on 22 October 2026 together with direct Codex repository execution.
**Suggested execution model: Claude Sonnet.** The approved interfaces use established validation patterns with focused compatibility tests. Advisory only; the chosen model remains unchanged.

## Global Constraints

- Continue on codex/skynet-task-status; verify branch and preserve unrelated changes before each commit. Baseline HEAD at planning: a76a0cf31223cf2e42518ca2938bff1981ff2878.
- Keep contracts/events.py, contracts/export.py, contracts/__init__.py, all existing bridge/service/dashboard/config/database files, dependency manifests and operational launchers unchanged.
- Add no production imports, configuration switches, migrations, emitters, routes, projectors, schema activation, installations, provider calls, pushes or deployments.
- Use read-only Git discovery with --no-optional-locks. Stage only the files explicitly owned by the completed task; never use git add -A.
- Test scratch outputs, SQLite spools, logs and caches stay in a fresh workspace scratch directory. No PostgreSQL, browser or service is needed.
- Existing production-spool incompatibility remains an expected negative; no queue entry is dropped to make a test pass.
- The contract has seven operation variants, the bounds and correlation rules in the approved spec, explicit opt-in and unchanged historical semantics. No dynamic test-only envelope/enum substitutes.

## Review Focus

1. JSON/mapping parity: numeric booleans, strings and floats must not enter strict integers; ISO timestamps and aware UTC datetime objects must follow the documented boundary. Task 1 tests schema version/sequence and Task 2 tests input parity.
2. Historical higher versions: a known legacy event with schema_version=2 or another positive value still delegates to the old model. Task 2 pins parsed dumps and original store hashes.
3. Deep immutability: mutating an input evidence list after parsing must not change a candidate or its hash. Task 1 tests frozen nested payloads and tuple conversion.
4. Opt-in, error precedence and disclosure: only a boolean flag can enable candidates; disabled candidates, wrong versions and bad payloads produce stable codes without raw values. Task 2 tests the flag, precedence table and a secret-shaped marker.
5. Accidental production admission: the tagged result cannot act as a legacy envelope, and production imports remain untouched. Task 2 tests the pre-database boundary; Task 3 verifies protected source hashes and imports.

## Execution conventions

Before editing implementation files, create a fresh scratch directory under the chat workspace, record session/project/approval metadata through Bridge Monitor, and capture branch, HEAD, index/status plus hashes of all existing tracked product files. Record file paths separately from hashes. Preserve the approved documentation changes. Stop for unexpected source drift; do not reset it.

Use a fresh PowerShell process with these task variables and child-only settings:

```powershell
$repo = 'C:\AI\bridge-monitor'
$py = 'C:\AI\bridge-monitor\.venv\Scripts\python.exe'
# $scratch is a newly created absolute workspace directory for this run.
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:TEMP = Join-Path $scratch 'tmp'
$env:TMP = $env:TEMP
Remove-Item Env:BRIDGE_MONITOR_DSN,Env:BRIDGE_MONITOR_TEST_DSN -ErrorAction SilentlyContinue
Set-Location $repo
```

All test commands below use this prefix, denoted **FOCUSED**:

```powershell
& $py -B -m pytest -q -p no:cacheprovider -p no:stepwise --basetemp "$scratch\pytest" -o "log_file=$scratch\pytest.log"
```

Give each execution its own basetemp/log suffix. Add the named test path or node to FOCUSED. Keep stdout/stderr and exit code in scratch. The new tests must not request test_dsn/runtime_dsn. Existing conftest.py has database fixtures with destructive setup; none is needed or requested.

No tests run while this plan is awaiting review. Red/green evidence belongs to implementation: a missing candidate module is a valid initial red, but dependency/setup failures are not proof that a behavior test works.

## Task 1: Typed operation contract and immutable fixtures

**Files to create:** contracts/operations.py; tests/contracts/test_operations.py; tests/fixtures/operation_lifecycle/candidate-v2.json; tests/fixtures/operation_lifecycle/legacy-inputs.json; tests/fixtures/operation_lifecycle/legacy-golden.json.

**Existing approved documentation to include in this task's commit after plan approval:** docs/superpowers/specs/2026-10-22-candidate-operation-reader-design.md and docs/superpowers/plans/2026-10-22-candidate-operation-reader.md. Preserve their reviewed bytes; planning leaves them uncommitted.

**Interfaces produced:**
- OperationEvent: discriminated union of StartedEvent, CompletedEvent, FailedEvent, CancelledEvent, UnknownEvent, VerificationRecordedEvent and DeliveryRecordedEvent.
- Each concrete event binds its exact operation.* name and typed payload from the approved table.
- _validate_operation_event(raw: bytes | str | dict[str, object]) -> OperationEvent, used by the later reader; private helper with equivalent wire/mapping behavior.
- operation_event_json_schema() -> dict[str, object].
- canonical_candidate_bytes(event: OperationEvent) -> bytes and candidate_event_hash(event: OperationEvent) -> str.
- candidate-v2.json is a JSON array with exactly one valid example per event type. At least one has no call_id; others cover optional correlation fields. Tests deep-copy examples before mutation.

- [ ] **Step 1: Capture historical controls before adding candidate code.** Write synthetic legacy-inputs.json with task.created, tool.completed and direct_task.recorded examples, plus known legacy names at schema versions 2 and 3. Include omitted versus explicit null/default fields, a Unicode/nested payload and two received_at values. A one-off scratch helper uses only the unchanged EventEnvelope and service.ingest.store._event_hash to write legacy-golden.json: original input, parsed JSON dump, canonical bytes and SHA-256. Save source hashes and interpreter/package versions beside the capture receipt in scratch. No production records or database connection. Golden values are not regenerated by the tests.
- [ ] **Step 2: Write the failing contract tests.** Parameterized names and required assertions:
  - test_all_seven_operation_variants: each example validates independently to its concrete class and exact payload, without requiring a prior start; start/terminal/verification/delivery remain distinct names. Parameterize every approved start/cancellation/unknown/verdict/delivery literal, not just one fixture value.
  - test_origin_and_capture_literals: accept observed/imported/synthetic and complete/partial/unavailable/removed/unknown respectively; reject new or malformed literals.
  - test_required_fields_and_bounds: remove each required field; test each 256/257 identifier boundary, 128/129 kind/layer/code boundary and blank-only value. Valid identities retain their original text.
  - test_scalar_and_timestamp_validation: reject producer_sequence 0, -1, True, 1.5 and "1"; schema_version accepts integer 2 only for candidates, rejecting True, 2.0, "2", 1 and 3. Reject naive/non-UTC/numeric timestamps; accept UTC ISO text and aware UTC datetimes consistently.
  - test_correlation: no call_id is valid; attempt_id/model_execution_id without a call fail; task/work/operation IDs remain required.
  - test_payload_validation: unknown envelope/payload keys, payload from another variant, wrong literals/strict booleans, invalid lowercase SHA-256 and 33 evidence references fail. Empty evidence_refs is valid.
  - test_candidate_immutability: envelope and nested payload mutation fails; evidence_refs is a tuple and is unaffected by later mutation of the source list.
  - test_candidate_identity: receipt time does not change hash; wire whitespace/key order does not; semantic same-ID mutation does; canonical bytes have exactly b"bridge-monitor.operation-lifecycle.v2\n" followed by JSON using sort_keys=True, separators=(",", ":"), ensure_ascii=True and all defaults/nulls included except received_at. Pin a Unicode fixture to make the byte convention unambiguous.

  Representative assertions (helpers read the named fixture):
  ```python
  event = _validate_operation_event(valid_started)
  assert event.schema_version == 2
  assert event.event_type == "operation.started"
  assert canonical_candidate_bytes(event).startswith(b"bridge-monitor.operation-lifecycle.v2\n")
  assert candidate_event_hash(event) == candidate_event_hash(event_with_new_received_at)
  ```
- [ ] **Step 3: Run FOCUSED tests/contracts/test_operations.py; record a red, then implement operations.py.** Do not subclass or modify EventEnvelope. Use frozen, extra-forbidden models and immutable nested fields. Apply strict scalar constraints explicitly so JSON and mapping behavior agree; reject a float/string schema discriminator rather than relying solely on Literal equality. The helper can normalize accepted representations, but must never trim identity strings, invent fields or translate legacy events. Candidate hashing must not alter or import/reimplement the legacy store hash path.
- [ ] **Step 4: Run the same command to green, then commit only the five Task 1 files and the two approved documentation files listed above.** Expected: exit 0, no skips and no I/O beyond fixture reads/scratch test artifacts. Commit message: feat: add candidate operation lifecycle contract. Preserve failing receipts separately; do not claim green until output is checked.

## Task 2: Explicit reader and old/candidate compatibility boundary

**Files to create:** contracts/event_reader.py; tests/contracts/test_event_reader.py; tests/contracts/candidate_helpers.py.

**Interfaces consumed:** Task 1 OperationEvent, private validation helper, canonical functions and JSON fixtures.

**Interfaces produced:**
- DecodedEvent(kind: Literal["legacy", "operation_v2"], event: EventEnvelope | OperationEvent): frozen dataclass tagged wrapper. Constructor validation forbids a mismatched tag/model; no forwarded producer_id/event_id/payload/model_dump duck interface. The enclosed legacy model retains its existing mutability; candidate envelopes and payloads remain frozen.
- read_event(raw: bytes | str | dict[str, object], *, allow_candidate: bool = False) -> DecodedEvent.
- encode_event(decoded: DecodedEvent) -> bytes: serialize the enclosed validated model without contract translation.
- EventReadError(code: str, issues: tuple[tuple[str | int, ...], ...]): public exception with stable code and field paths; no raw input/values in str, repr or exposed issues.
- candidate_helpers.py contains only fixture loading and opt-in test guards. It is not imported by product modules or enabled for unrelated tests.

**Reader decision order:** reject non-boolean allow_candidate values with TypeError before parsing; a truthy string or integer must not enable candidates. Malformed JSON -> malformed_json; syntactically valid non-object JSON or another unsupported input shape -> invalid_envelope. Missing/non-string event_type -> invalid_envelope. A known legacy name delegates to the unchanged model irrespective of its positive numeric version. A known operation name with opt-in off -> candidate_disabled; with opt-in on and non-integer/non-2 version -> unsupported_version. Unknown names -> unknown_event_type. For an enabled candidate with the correct version, any envelope/correlation error -> invalid_envelope; otherwise payload errors -> invalid_payload. If envelope and payload both fail, invalid_envelope wins. Legacy validation failures become invalid_envelope in this new API without changing the existing legacy model's API.

- [ ] **Step 1: Write tests before the reader implementation.**
  - test_legacy_delegation_matches_goldens: for every pinned historical input, assert kind=legacy, exact current legacy dump, identical original _event_hash and unchanged tool.completed payload/name. Include positive higher versions, omitted/explicit defaults and receipt-time controls.
  - test_reader_dispatch_matrix: defaults accept legacy and reject each operation variant with candidate_disabled; opt-in accepts all seven; unknown operations/types, wrong versions and invalid envelopes/payloads match the precedence table. Non-boolean opt-in values including 1, "false" and None raise TypeError. Valid non-object JSON yields invalid_envelope, not malformed_json.
  - test_mapping_string_bytes_parity: ISO-string mappings, equivalent JSON strings and bytes return the same validated meaning/hash or same error code/path. Native aware UTC datetimes in mappings are also accepted; do not change legacy datetime/coercion rules.
  - test_round_trip_preserves_contract: read -> encode -> read preserves both kinds, payload, correlation, dump and canonical identity. Legacy events are never rewritten into operations.
  - test_error_redaction: invalid fields containing a unique secret-shaped marker do not expose it in exception str/repr/issues or a formatted traceback. Sanitized exceptions suppress the raw Pydantic exception chain with from None and do not retain it in public attributes.
  - test_wrapper_is_not_a_legacy_envelope: no forwarded legacy attributes; passing it to the unchanged store fails before a sentinel psycopg.connect can be reached. This is a negative boundary test, not supported ingestion.
  - test_parser_has_no_external_effects: monkeypatch psycopg connection entry points, socket connection helpers and subprocess execution to raise before invoking import/read/encode operations. No provider or service stub may receive a call.
  - test_old_spool_preserves_candidate_and_tail: in tmp_path only, append an actual candidate model followed by a historical model to the real DurableSpool; old replay rejects before the stub collector and preserves both rows/gap counters; drain lock reacquires.
  - test_declared_candidate_recovery_adapter: under the real spool lock, a test-only adapter reads raw entries from that same scratch SQLite file, uses the real read_event(..., allow_candidate=True), gets stub acknowledgements and calls acknowledge. Assert ordered decoded kinds operation_v2/legacy and an empty spool only after acknowledgement. Declare this adapter in receipts; it does not add production spool support or replay external execution.

  Representative checks:
  ```python
  legacy = read_event(legacy_input)
  assert legacy.kind == "legacy"
  assert _event_hash(legacy.event) == pinned_legacy_hash
  with pytest.raises(EventReadError) as error:
      read_event(valid_started)
  assert error.value.code == "candidate_disabled"
  assert read_event(valid_started, allow_candidate=True).kind == "operation_v2"
  ```
- [ ] **Step 2: Run FOCUSED tests/contracts/test_event_reader.py and retain the expected missing-reader red.**
- [ ] **Step 3: Implement only the declared reader API.** Preserve the legacy object/dump by direct delegation, not by embedding its fields into a widened model. Validate the candidate discriminator before the payload; wrap Pydantic errors into code/path-only diagnostics. Do not expose candidate objects to production ingestion. No global monkeypatch or new production import.
- [ ] **Step 4: Run FOCUSED with test_operations.py, test_event_reader.py and existing tests/contracts/test_events.py.** Expected: all collected assertions pass, zero skips; no PostgreSQL/network/subprocess activity. Commit only the three Task 2 files with message feat: add explicit candidate event reader.

## Task 3: Schema provenance, contract reference and final preservation

**Files to create:** contracts/schemas/operation_lifecycle_v2.schema.json; tests/fixtures/operation_lifecycle/schema-provenance.json; tests/contracts/test_operation_schema.py; docs/specifications/candidate-operation-lifecycle.md.

**Interfaces consumed:** operation_event_json_schema(), OperationEvent and public reader APIs. No new product API.

- [ ] **Step 1: Write test_operation_schema.py before generating the artifact.** test_schema_matches_public_adapter compares parsed JSON directly with operation_event_json_schema(). Assert discriminator covers exactly the seven event names, schema_version is required/constant 2, all concrete envelope/payload objects forbid extra fields, required payload fields and bounds match the approved table, and evidence_refs has maxItems=32. test_schema_provenance checks SHA-256 of artifact and operations.py plus actual Pydantic/package metadata; provenance is scoped to schema generation, not a deployed-build attestation. Initial absence of the schema/provenance files is the expected red.
- [ ] **Step 2: Generate schema into scratch from the real public function, inspect it, then copy only the declared schema artifact and provenance JSON into their repository paths.** Record generator entry point, source hash, schema hash, Python/Pydantic versions and timezone-aware generation time. Hash source bytes, not a Git commit that does not yet exist. Do not alter contracts/export.py or regenerate operational artifacts.
- [ ] **Step 3: Write the short contract reference.** Include the exact opt-in example, literal/bound/correlation table, error precedence, legacy numeric-version behavior, canonical hash specification, candidate fixture locations and generated-artifact identity. State the real consumer matrix: parser supported; old spool rejected/preserved; explicit test adapter only; production store/projector/HTTP/SSE/UI unsupported. Do not describe parsing, raw storage or a verification report as semantic pipeline support.
- [ ] **Step 4: Run one final FOCUSED invocation selecting the three new test modules and existing test_events.py.** All must pass with zero skips and no guard denials. Compare every pre-existing tracked product file hash with baseline; only approved documentation additions and candidate-owned new files may differ. Check that candidate imports occur only in new contract modules/tests, and existing production entry points are unchanged. Verify schema provenance, report outcomes/limitations, then commit only the four Task 3 files with message docs: publish candidate operation contract schema.

## Completion and rollback

Success for this slice requires checked red/green receipts, all final focused tests passing, immutable artifact identities, original source/hash preservation and a bounded Git diff. Runtime pipeline activation and full BM-02 closure remain pending.

Retain scratch fixtures/logs and actual Bridge Monitor observation receipts; do not submit guessed model usage or costs. Review the exact task commits/diff before handoff. An independent review may analyze the authorized changes in enforced read-only mode, but no external reviewer may silently apply edits. No new delegation or model switch is authorized by this plan.

Rollback after implementation is an explicit revert of the candidate-only commits after checking for later dependencies; never reset the branch, delete retained operational evidence or discard queued records. No operational service, database, configuration or policy rollback should be necessary because none is changed.

## Plan review and execution method

Direct Codex execution is already approved. This plan does not request that approval again. After the user reviews this plan, implement natively in this session using executing-plans, preserve the selected model and apply test-driven development and verification-before-completion. No subagent method selection is needed.

Planning itself writes only this plan and the approved specification. No tests, candidate modules, fixtures, schema artifacts, configuration changes, commits or services are created/run until plan review is complete.

