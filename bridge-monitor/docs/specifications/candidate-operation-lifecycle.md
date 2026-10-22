# Candidate operation lifecycle contract

This is an opt-in contract and reader inside Bridge Monitor. It supports compatibility testing without enabling operation producers or changing production readers, routing, persistence or services. Event schema 2 is not another bridge product.

## Reading and serialization

```python
from contracts.event_reader import read_event, encode_event
from contracts.operations import candidate_event_hash

decoded = read_event(candidate_json_bytes, allow_candidate=True)
assert decoded.kind == 'operation_v2'
assert decoded.event.operation_id
identity = candidate_event_hash(decoded.event)
wire_bytes = encode_event(decoded)
```

Only boolean True enables candidate parsing; strings, integers and None are rejected as invalid API arguments. The default read_event(raw) accepts historical names through the unchanged EventEnvelope and rejects operation candidates. Inputs are JSON bytes/text or a dictionary. The frozen DecodedEvent dataclass has kind and event fields and no forwarded legacy-envelope interface. The contained historical EventEnvelope retains its existing mutability; candidate envelopes and nested payloads are frozen, with evidence arrays stored as tuples. Mapping evidence inputs must be ordered lists or tuples; sets, generators and other coercible containers are rejected. Prebuilt payload instances are revalidated and copied, including instances created without validation.

Known historical event names retain their existing positive numeric schema_version acceptance, including 2 or 3. This is legacy validation, not negotiation. Unknown operation names and unsupported candidate versions are rejected. Historical tool.completed remains a content-block/stream boundary and is never translated into operation completion or elapsed time.

## Candidate fields

Required envelope fields are schema_version (strict integer 2), event_type, event_id, producer_id, producer_incarnation, producer_sequence, occurred_at, origin, observation_layer, capture_completeness, operation_id, operation_kind, task_id, work_item_id and payload.

Optional fields are session_id, call_id, attempt_id, decision_id, parent_operation_id, model_execution_id, worker_instance_id, parent_event_id, requested_model, reported_model, config_version and received_at. An attempt or model execution ID requires a call ID. A call itself is optional: direct work must not acquire a fabricated Bridge call. Correlation fields are reported identifiers, not verified database links or permission grants.

Identifiers and references are nonblank strings up to 256 characters; operation_kind, observation_layer, failure_code and reason_code are limited to 128. Identity whitespace is not silently trimmed. producer_sequence is a strict positive integer, excluding bools, strings and floats. Timestamps are aware UTC datetime objects or UTC ISO text; numeric, naive and nonzero-offset values are rejected. JSON and mapping inputs follow the same candidate rules.

origin is observed, imported or synthetic. capture_completeness is complete, partial, unavailable, removed or unknown. Every envelope and payload rejects extra fields.

| Event | Payload |
| --- | --- |
| operation.started | start_basis: observed or inferred |
| operation.completed | Optional result_ref |
| operation.failed | failure_code and strict boolean retryable |
| operation.cancelled | cancelled_by: user, parent, worker or system; reason_code |
| operation.unknown | unknown_reason: lost_contact, process_exit, deadline_exceeded, capture_gap or unconfirmed_effect; UTC last_observed_at |
| operation.verification_recorded | verification_id, verifier_id, verdict: passed/failed/inconclusive, input_sha256: 64 lowercase hex characters, evidence_refs: zero to 32 bounded references |
| operation.delivery_recorded | delivery_id, result_ref, delivery_status: delivered/failed/not_attempted |

Completed, failed, cancelled and unknown are outcome observations. Verification and delivery remain separate facts and do not imply successful execution. Parsing does not require start-first order, resolve conflicting observations, deduplicate stored records or execute an operation.

## Rejections

EventReadError exposes only code and static field/index paths in issues. Raw input values and unknown extra-key names are omitted, and the raw validation exception chain is suppressed.

| Condition, in precedence order | Result |
| --- | --- |
| Non-boolean opt-in argument | TypeError |
| Malformed JSON/encoding or parser recursion limit exceeded | malformed_json |
| Non-object shape or missing/non-string event_type | invalid_envelope |
| Known legacy event | Delegate to unchanged EventEnvelope; validation failure is invalid_envelope in this reader API |
| Unknown event name | unknown_event_type |
| Known operation, opt-in disabled | candidate_disabled |
| Enabled operation, non-integer/non-2/missing version | unsupported_version |
| Envelope/correlation failure | invalid_envelope |
| Payload-only failure | invalid_payload |

When both envelope and payload fail, envelope validation wins. These are expected rejections when asserted by compatibility tests; they are not evidence of an activated production feature.

## Canonical identity

Historical hashes and parsed-model fields are unchanged. The fixture controls were captured with the original EventEnvelope and store hash before adding candidate code.

canonical_candidate_bytes(event) returns the UTF-8 prefix bridge-monitor.operation-lifecycle.v2 followed by a newline, then JSON from the validated candidate model using sorted keys, compact comma/colon separators and ensure_ascii=True. All defaults/nulls remain, excluding only received_at. candidate_event_hash is SHA-256 of those bytes. Wire formatting and receipt time do not change that identity; semantic same-ID changes do. It does not perform durable deduplication or conflict resolution.

## Artifacts and consumer support

The real public schema API is contracts.operations.operation_event_json_schema(). The committed artifact is contracts/schemas/operation_lifecycle_v2.schema.json; tests/fixtures/operation_lifecycle/schema-provenance.json pins its bytes, source bytes, generator entry point and runtime versions. The schema describes structural constraints. UTC-only and call-correlation rules also require the Python validator; JSON Schema alone is not the full acceptance algorithm. The existing four-model schema registry is unchanged.

Versioned examples, eight pinned historical controls and provenance are in tests/fixtures/operation_lifecycle/. Contract, reader and schema tests are under tests/contracts/.

| Consumer | Candidate support |
| --- | --- |
| New reader with explicit opt-in | Validated operation records; historical dispatch preserved |
| Default/new reader and existing legacy parser | Candidate rejected |
| Existing durable spool/emitter | Untyped append can accept an explicitly unwrapped raw candidate model; rejection happens at replay, retaining the head and later entries |
| Declared test-only SQLite recovery adapter | Reads retained bytes with the real candidate reader and acknowledges only after stub receipt; no external execution |
| Production store, reconciliation, projector, HTTP, SSE and dashboard | Not integrated or semantically supported by this slice |

The adapter in test_event_reader.py is an isolated harness, not production spool recovery support. No global parser monkeypatch, live replay, database schema change, routing policy change or provider execution is performed. Existing strict producer authentication and execution authority are unchanged. Full BM-02 compatibility closure and future pipeline activation remain separate work.

Rollback is a scoped revert of the candidate-only commits after checking later dependencies. It requires no operational database/queue/configuration rollback because this slice changes none of them. Never discard queued evidence or replay external effects to repair telemetry.
