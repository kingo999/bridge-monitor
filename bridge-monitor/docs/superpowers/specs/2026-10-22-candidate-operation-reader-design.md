# Bridge Monitor Candidate Operation Contract and Reader

Status: design approved by the user on 22 October 2026. Implementation awaits review of the associated implementation plan.

The goal is to create real, importable candidate artifacts for the pending future-reader compatibility matrix. This is the first contract-and-reader slice of the Bridge Monitor operation-lifecycle effort. It preserves the operational bridge, historical event identity and existing routing policy. It introduces no legacy-bridge dependency, provider route or execution authority.

## Recommended boundary and alternatives

Add a separate operation contract and a reader with explicit candidate opt-in. The reader understands historical records through the existing EventEnvelope and candidate operation records through the new contract. Normal emitters, spools, HTTP ingestion, database reconciliation, projectors, publications and dashboard continue using their current paths. Merely importing the new modules activates nothing.

Widening the existing EventType would be a smaller edit but would change the legacy parser's acceptance boundary and still leave reconciliation and projections incomplete. Wiring a new lifecycle through storage, producers and the dashboard immediately would provide broader behavior, but exceeds the contract-and-reader slice and needs separate persistence and rollout decisions. The separate candidate is recommended because it supplies testable artifacts while keeping those boundaries explicit.

## Product modules and public API

Add contracts/operations.py and contracts/event_reader.py, focused contract tests and versioned JSON fixtures under tests/fixtures/operation_lifecycle/. Add a short contract reference and schema artifact with explicit source provenance. Existing dependency pins and the four-schema export registry remain unchanged; the new module exposes its own operation_event_json_schema().

The reader API is read_event(raw: bytes | str | dict[str, object], *, allow_candidate: bool = False) -> DecodedEvent. DecodedEvent is an immutable tagged wrapper with kind equal to legacy or operation_v2, and event containing the validated model. It does not expose a legacy-envelope duck-typing interface. Callers explicitly inspect kind and event. encode_event(decoded) returns JSON wire bytes for the matching model; it does not translate between contracts.

For known legacy event names, delegate to the unchanged EventEnvelope. This preserves its existing numeric schema_version >= 1 behavior, including known legacy names with version 2. That behavior remains legacy validation, not negotiated new semantics. The returned legacy model's fields and model_dump output must be identical to direct legacy parsing.

For the seven operation names below, require allow_candidate=True and schema_version exactly 2. A candidate with another version, a new/unknown operation name, a malformed envelope or an unsupported extra field is rejected. The default reader rejects operation candidates. Version 2 denotes an event schema inside Bridge Monitor, not another bridge product.

Expose EventReadError with stable codes candidate_disabled, unsupported_version, unknown_event_type, invalid_envelope, invalid_payload and malformed_json. Error details identify field paths and categories without automatically logging raw values. Existing production exceptions and endpoints are unaffected.

## Candidate envelope

Use a distinct frozen Pydantic model family with extra fields forbidden. Keep EventEnvelope and EventType unchanged. Concrete candidate models bind a fixed event_type and a typed payload; a discriminated union supplies parsing and JSON Schema.

Required envelope fields: schema_version=2, event_type, event_id, producer_id, producer_incarnation, producer_sequence, occurred_at, origin, observation_layer, capture_completeness, operation_id, operation_kind, task_id and work_item_id.

Optional correlation fields: session_id, call_id, attempt_id, decision_id, parent_operation_id, model_execution_id, worker_instance_id, parent_event_id, requested_model, reported_model, config_version and received_at. An attempt_id or model_execution_id requires call_id. call_id itself is optional so operations associated with direct Codex work do not need a fabricated Bridge call. decision_id and parent_operation_id are observations, not proof of authorization or a resolvable database reference.

Identifiers are nonblank strings with a 256-character maximum, operation_kind is a nonblank identifier of at most 128 characters, and observation_layer is nonblank with a 128-character maximum. Reject whitespace-only strings without silently trimming identities. producer_sequence is a strict positive integer; booleans and fractional/string values are rejected. Timestamps must be timezone-aware UTC; JSON and mapping inputs must have equivalent acceptance. Origin and capture completeness retain the existing literal sets.

Each payload is also frozen and forbids unknown fields. Lists of evidence references use immutable tuples internally and JSON arrays on the wire. Contract immutability does not rely on a shallow frozen wrapper around mutable dictionaries.

## Operation facts and payloads

All events carry the envelope correlation fields, so out-of-order records are individually interpretable. This reader validates facts; it is not a lifecycle reducer or external operation executor.

| Event name | Required payload | Optional payload |
| --- | --- | --- |
| operation.started | start_basis: observed or inferred | None |
| operation.completed | None | result_ref: nonblank reference |
| operation.failed | failure_code: nonblank identifier; retryable: strict boolean | None |
| operation.cancelled | cancelled_by: user, parent, worker or system; reason_code: nonblank identifier | None |
| operation.unknown | unknown_reason: lost_contact, process_exit, deadline_exceeded, capture_gap or unconfirmed_effect; last_observed_at: UTC timestamp | None |
| operation.verification_recorded | verification_id; verifier_id; verdict: passed, failed or inconclusive; input_sha256: 64 lowercase hex characters; evidence_refs: array of references | None |
| operation.delivery_recorded | delivery_id; result_ref; delivery_status: delivered, failed or not_attempted | None |

Reference and reason identifiers follow the nonblank/256-character limit; failure/reason codes have a 128-character limit. evidence_refs permits at most 32 entries, each a bounded nonblank reference. A verification can have no evidence references, including an inconclusive verdict; validation does not manufacture assurance or establish that a reported verdict is true.

Completed, failed, cancelled and unknown are terminal outcome observations. Verification and delivery remain separate facts and cannot implicitly turn an outcome into success. A later observation after unknown is permitted at parsing level; conflict/reconciliation semantics are deferred. No parser assertion requires a start record to arrive first.

Historical tool.completed continues to mean its recorded stream/content-block boundary. It is never converted into an operation completion or duration. No inferred latency, usage, routing saving, contribution or tool permission is created.

## Identity and serialization

Legacy canonical bytes and _event_hash are unchanged. Golden tests pin representative historical parsed dumps and hashes using the existing store function, including omitted/defaulted fields, receipt timestamps and known legacy names carrying higher positive version values.

For candidate events, define canonical_candidate_bytes(event) as UTF-8 sorted compact JSON of the validated candidate model, excluding only received_at, with the prefix bridge-monitor.operation-lifecycle.v2 followed by a newline. candidate_event_hash(event) is SHA-256 of those bytes. Candidate defaults are fixed for this schema version; changing their canonical meaning requires a new reviewed contract version.

Equivalent wire formatting must yield the same canonical candidate identity. Changing a semantic field while reusing event_id must change the hash. received_at alone must not. Domain separation distinguishes the contract's identity namespace; it is not a claim that cryptographic collisions are impossible. These APIs do not insert events or enforce durable deduplication.

## Existing consumer boundary

No candidate object is passed to legacy ingest_batch, and no candidate producer is enabled. The old HTTP parser and spool continue rejecting candidate event names. The existing poison-head behavior remains: parsing can raise before the collector error handler, preserving the queue. This change does not repair or relabel that behavior.

The new wrapper is deliberately not accepted as a legacy event object. Tests use a sentinel collector/database boundary to verify that default/unsupported paths do not perform I/O. There is no implicit adapter that converts an operation record into a tool event or untyped payload.

Isolated matrix tests can use the real candidate reader against retained fixture bytes and queued candidate records. A capable-reader harness must declare any injected spool/collector adapter. Such a harness does not prove production spool, storage, reconciliation, SSE or UI support. Those combinations remain unsupported until deliberately integrated and tested. Full BM-02 closure is not implied by this artifact implementation.

## Acceptance evidence

- Public modules and versioned fixtures are real repository artifacts, not dynamically created test-only enum/model classes.
- The default reader preserves historical validation and parsed-model/hash behavior and rejects candidate records.
- Explicit opt-in accepts every valid candidate variant and rejects unknown versions/types, missing correlation, forbidden fields, invalid scalar/timestamp/reference values and payloads for the wrong event type.
- Mapping and JSON readers behave consistently; round-trip preserves semantics and canonical identity.
- Candidate parsing requires no live database, service, provider, network access or tool execution.
- Historical hashes, original event model, production ingestion/spool/projector/client files and dependency pins remain unchanged.
- Tests distinguish expected rejection from unexpected failure and report actual execution separately from proposed coverage.
- Configuration and migrations are unnecessary for this slice. No operational activation, install, push or deployment is included.

## Review and rollback

Implement on the existing codex/skynet-task-status continuation branch, preserving unrelated work. Use focused tests with scratch-only outputs and temporary databases if any are needed. A later implementation plan will name exact test cases and Git boundaries. Revert the candidate-only modules, fixtures, schema and documentation commit if necessary; no operational database, queue or schema changes need reversal.

The user has approved implementation, configuration and Git changes. The existing Bridge worker supports supplied-context analysis/drafts but cannot read arbitrary checkouts, apply edits or run repository tests. The user explicitly approved direct Codex repository execution on 22 October 2026. The design is approved; the implementation plan remains subject to user review under the writing-plans skill.

Suggested execution model: Claude Sonnet. The work uses established validation patterns and focused compatibility tests. Advisory only; the user's chosen model remains unchanged.

## Design evidence

Design drafting was Bridge-delegated using requested profile sonnet, reported model claude-sonnet-5-5. Call a473cc09-b199-5226-a91b-9c2f44e65cf9; decision 592e758a-d7f8-5fb7-bf27-b2bd6d0ad282; result 9433a236-b949-4a81-8627-7ae94f9695e7. Codex independently revised the draft to add historical dispatch, allow call-less direct-work correlation, pin payload values, distinguish artifact from pipeline support and correct capability/hash overclaims. The user approved these design choices on 22 October 2026; no implementation tests have run.

