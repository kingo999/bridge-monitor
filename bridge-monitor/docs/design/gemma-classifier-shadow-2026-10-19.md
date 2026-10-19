# Gemma classifier shadow — design

**Suggested execution model: Claude Sonnet.** The hardware and runtime were already demonstrated and the work
follows existing patterns with testable contracts. Advisory only.

## Boundary

Gemma classifies; ordinary code decides. A recommendation is stored in `classifier_observations`
next to (1) the raw model output, (2) a deterministic *would-be-allowed* verdict, (3) the route
the unchanged system took (unknown unless found), and (4) append-only evaluations in
`classifier_evaluations`. The verdict never rewrites the label, so an unsafe recommendation stays a
model miss. The classifier modules import no dispatcher, scheduler, admission, paid-call or worker
module; a test asserts this and that the live router sources never mention the classifier.

## Why separate tables

The event store feeds production metrics, the snapshot and the Qwen cohort, so a new event type
would be one predicate away from inflating them. Separate tables make exclusion structural. Request,
source and response bytes still use the existing protected `content_objects` store and
operator-only content endpoint. Usage is stored on the observation with provenance rather than in
`usage_observations`, which is bound to model executions and calls. Migration 007 changes no existing object.

## Contracts (all versioned)

- **Rubric** `router-rubric-v1`: the benchmark text, byte-identical after normalizing the CRLF that
  the benchmark's `write_text` added on disk (the model received LF). SHA-256 pinned in source.
- **Profile** `gemma-router-profile-v1`: ctx 8192, predict 192, temperature 0, thinking off, top_k 40,
  top_p 0.9, repeat/presence penalties 1/0, seed 42 (replay seeds 42/43). Any change is a new id.
- **Response**: exactly `category`, `route`, `reason` (<=220 chars), known enums, no duplicate keys,
  prose, fence or extra field; terminal frame with `done_reason == "stop"`, the pinned model name,
  no thinking output, bounded bytes. A token-limit stop is incomplete. Nothing is repaired or retried.
- **Facts** `trusted-facts-v1`: strict booleans/enum kept separate from untrusted request/source.
  Availability and permission fail closed when unknown; tests, scope, security and context
  completeness block only when explicitly adverse (unknown becomes a warning), so frozen benchmark
  cases that carry that information only as prose are not penalised.
- **Route map** `actual-route-map-v1`: `paid` -> `paid`, `local` -> `code_local` (the Skynet Qwen
  worker), `pending`/missing -> `unknown`. `general_local` has no project worker.

## Failure semantics

Unavailable endpoint or digest mismatch: nothing is claimed or sent; queued inputs expire. Timeout,
transport loss after send, or a runner crash: `uncertain`, never resent. Malformed output: `failed`
with the raw text kept. Cancellation: `cancelled`. Queue full or oversize: `skipped` with a reason.
CPU/partial offload: recorded, runner halts.

## Lifecycle

Python/ctypes (no PowerShell 7 dependency, 100 ns creation-time identity). The previous helper's
timestamp problem cannot occur because ticks are compared as integers.

## Not done by design

No `route_work` hook, no scheduled task, no migration or policy activation, no dashboard rebuild, no
automatic promotion. These need separate review (see the operations document's rollout list).
