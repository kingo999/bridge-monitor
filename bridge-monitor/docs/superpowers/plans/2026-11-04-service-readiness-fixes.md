# Service readiness review fixes

Goal. Correct the accepted Bridge review findings while preserving node/edge removal.

Scope. service/queries/service_availability.py, its unit/integration tests, browser polling and service-flow tests, plus the existing live-flow context note.

Execution. Continue the operator-approved direct Codex execution. The user accepted the review findings and requested implementation. Preserve unrelated checkout changes.

Suggested execution model: Claude Sonnet (advisory). The changes use standard bounded database reads, independent probe state and explicit recovery tests.

- [x] Isolate exceptions and validate HTTP/agent endpoints. One failed service must not change another service's result.
- [x] Replace blocking cache refresh with independent single-flight background probes. HTTP requests read cached state immediately. Each probe has a six-second result deadline; a stuck probe cannot create more threads for that key. Bound policy and evidence database reads.
- [x] Evaluate enabled Claude models separately. Authentication/billing and known account-wide quota evidence affect the account. Model rate/server failures and scoped quota affect relevant models. Ignore unrelated failures. Preserve failures until relevant successful evidence, without timed guesses of recovery.
- [x] Add regression tests for service exceptions, a blocked probe, concurrent cache readers, model aliases and cross-model recovery. Check browser pending-to-ready polling and node/edge visibility.
- [x] Build/test, deploy and verify the actual status endpoint; update context and audit completion.

Ruling. The graph's Claude node represents the enabled model group. It is visible when at least one enabled model passes readiness. Individual model readiness is returned by the status endpoint. Unknown quota window names are not assumed account-wide.

Ruling. Configuration needed for a service is read with bounded database settings by that service's own probe. Gemma can refresh independently of a policy/database failure affecting the other probes.

Verification. Final backend checks: 25 passed (including scoped quotas, overload, account recovery and exact model version semantics). Production build/typecheck passed; 22 browser checks passed. Deployed dashboard restart verified by process identity and live API: unauthenticated 401, authenticated 200, Claude ready, Skynet/Gemma unavailable; background polls 1.42–1.71 ms.

Review. Bridge call 9219eff2-ba7f-5843-bbe9-7a0533ccae05 requested sonnet and selected/reported claude-opus-5-5. No review source/Git changes. Review accepted and acknowledged; final local follow-up reduces history to latest relevant account/model signals in PostgreSQL and handles 529 overload. Its Python regressions passed after review; that follow-up was not re-delegated for another paid review.

Limits. A slow dependency may fail the six-second result deadline. A truly stuck probe cannot multiply threads and remains timed_out until return or restart. Metadata readiness does not assert successful inference. Quota provenance retains the existing trusted producer boundary.
