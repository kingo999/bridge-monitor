# Controlled read-only pilot checklist

Record each run under `var\reports` with the policy hash, runtime capability
hash, model profiles, temporary MCP path, time window and operator. No source
write capability or normal-client registration change is part of this pilot.

1. Confirm project paths, secrets, PostgreSQL target, active reviewed policy,
   model digest, and dashboard access. Back up the database and check that a
   separate restore works.
2. Start with one active Claude task under an exact reviewed policy. Run one read-only GPT → Claude → Skynet task and
   capture exact prompt, local response, model choices, disposition and result.
   Run a controlled local timeout and verify automatic paid continuation, held
   local capacity, routed/not-routed classification and later local result
   handling. Do not alter a held reservation without correlated evidence.
3. If the first policy had one slot, activate the reviewed two-Claude capacity
   setting through the policy controller. Run separate read-only root tasks concurrently and compare
   visible workers, per-root/global limits and queue behavior.
4. Exercise capture reconnect and dashboard cursor reset with synthetic data;
   confirm no inference starts during replay. Verify stop/restart resumes only
   queued paid recoveries and that cancellation/uncertainty remains visible.
5. Record counts of routed, not routed, accepted, revised, replaced, pending,
   paid takeover, timeout and unknown outcomes. Compare each against stored
   prompts/responses and operator observations. Report measurement windows and
   actual latency, spool and query sizes; do not claim savings from a small pilot.
6. Review unknowns and the exact proposed switch and rollback commands with
   the user before any broader routing or client registration change.
