# Skynet task status addendum — 17 October 2026

## Purpose and boundary

Give each delegated Qwen call a request-specific status so the owning Claude
worker does not wait through a dead or stalled request. This extends the
approved phase 1 bridge; it does not change routing decisions, result
dispositions, or the automatic paid fallback contract. Keep Qwen gated until
the status path and its deadline behavior have passed a controlled pilot.

All maintained source remains under `C:\AI\bridge-monitor`. A small agent is
deployed to the Ubuntu Skynet host alongside Ollama. The existing bridge
sources outside this project remain untouched.

## Choice

Use short bridge-initiated status polls for job control and the existing
streamed Ollama output as the source of actual model progress. A Skynet-side
agent owns the Ollama request and correlates it to the bridge child call ID.
Its health response only proves that the agent can answer; it never proves
that the model is making progress. Polling Ollama `/api/ps` alone is excluded:
that endpoint identifies resident models, not individual requests.

The bridge uses its own monotonic clock for all decisions. Skynet timestamps
are retained for diagnosis and never extend a bridge deadline.

## Agent contract

The bridge submits one job with the child `call_id` as `job_id`, the pinned
model name/digest, and the same Ollama generation
options currently used by the Qwen adapter. Submission returns an immediate
acknowledgement after the agent durably records `accepted` and the SHA-256 of
the canonical full request (prompt, model, digest and options); inference starts
in the agent's background worker. Repeating the same `job_id` and request hash
returns the existing job without dispatching another Ollama request. A
conflicting hash is rejected.

Authenticated endpoints:

- `POST /v1/jobs`: submit or idempotently look up a job; return `job_id`,
  `request_sha256`, `state`, `agent_incarnation`, and `accepted_at`.
- `GET /v1/jobs/{job_id}?after_sequence=N`: return `state`, current sequence,
  contiguous output deltas after `N`, `last_ollama_frame_at`,
  `last_output_at`, and any terminal Ollama frame. A missing delta range is a
  capture gap, never a silently complete result.
- `POST /v1/jobs/{job_id}/cancel`: request best-effort cancellation and return
  the current state. A cancellation request or closed bridge connection is
  not proof that Ollama stopped.
- `GET /v1/health`: agent identity and readiness only; no job-liveness claim.

States are `accepted`, `ollama_connecting`, `streaming`, `completed`, `failed`,
`cancel_requested`, and `unknown`. `completed` requires a matching Ollama
`done: true` frame and model identity. `streaming` requires an upstream frame.
Only nonempty model output advances the bridge's progress timer. A periodic
agent heartbeat, if reported, is displayed separately from output progress.

The agent validates the requested model digest against Ollama metadata before
dispatch. It enforces configured prompt, context and output limits and accepts
only the generation option keys and keep-alive value used by the bridge. The
LAN listener checks the client IP and bearer token before parsing a request
body, and rejects advertised bodies above its configured size. The agent keeps active
output deltas in a bounded per-job buffer for
polling; buffer size and receipt retention are configuration values. A status
page includes enough deltas to reconstruct the exact output in order, or
explicitly reports a gap. Sanitized terminal metadata includes reported model,
usage and Ollama timing fields when present. The agent strips response text and
other content fields before writing the terminal receipt.
Finished-job output buffers expire after a configured period; later reads keep
the receipt and report a gap for output that is no longer available.
Its durable journal stores request identity, state transitions, agent
incarnation and terminal metadata, but not prompt or response bodies. The
bridge remains the system of record for exact content. On agent restart,
unfinished jobs become `unknown`; they are never automatically replayed.
The `agent_incarnation` on a receipt identifies the process that accepted the
job; restart detection relies on the durable `unknown` state or an output gap.
Proven completed/failed receipts expire after the configured retention period;
unknown receipts remain for reconciliation. Agent authentication
uses a project-configured bearer token, and only the bridge host may reach the
agent's LAN listener.

## Bridge deadlines and fallback

Initial pilot values are configuration, not code constants: 5 seconds for
submission acknowledgement, 2 seconds between polls, 3 seconds per status
request, two consecutive failed polls, 45 seconds to first nonempty output,
20 seconds without further output, and 120 seconds total Qwen execution.
The existing 30-second bridge queue deadline, 60-second paid fallback
allowance, 30-second delivery margin, and 600-second parent MCP timeout stay
unchanged. The shared local cutoff may end work earlier. A health reply or
agent heartbeat never resets the first-output, idle, or total deadline.

The bridge issues a short best-effort cancel on any failure or deadline. It
then commits the existing one-time fallback transition and returns the paid
continuation signal to the healthy Claude owner without waiting for uncertain
Ollama termination. A late Qwen result is retained but not delivered over the
selected paid route. The local capacity slot remains held until correlated
terminal evidence or operator reconciliation, as in the existing contract.
Polling failures, status sequence gaps, agent restart, conflicting identity,
or a missing terminal frame never become a successful Qwen result.

## Telemetry and dashboard

Persist the submission acknowledgement, agent incarnation, state changes,
poll failures, first-output latency, last-output age, idle/absolute deadline
reason, cancellation request, terminal evidence, and fallback linkage in the
existing call/attempt records and event stream. Keep actual prompt and output
content in the bridge's PostgreSQL capture. The dashboard shows the Qwen
child's state, time since last model output, and whether the agent is reachable;
those three indicators are distinct. Existing routed/not-routed and paid
takeover definitions remain unchanged.

## Verification and rollout

Use a fake Ollama endpoint to test immediate ack, delayed first output,
streaming progress, silent stall, total cap despite heartbeats, poll outage,
duplicate submit, agent restart, late result, and cancellation uncertainty.
Verify a stalled job returns paid continuation within its configured limit
and never releases uncertain local capacity. Then install the agent on
Skynet, check its authenticated health and pinned model without inference,
and run one bounded live task. Compare agent status, bridge events, PostgreSQL
content and dashboard state before considering Qwen ungating or a default
route change.
