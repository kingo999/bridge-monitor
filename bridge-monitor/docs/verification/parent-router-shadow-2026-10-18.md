# Parent router shadow implementation proof — 18 October 2026

The GPT-side router is implemented as an opt-in parent MCP operation. Its default policy mode is `disabled`. No operational migration, policy activation, worker start or live canary was performed during this implementation. The current operational policy hash was observed as `445c3debabdc63c876c61ace504b0d1f281f8f52b95cb2b8fbee80ee5e428629`; it has no router section and therefore retains the disabled default. The running operational backend still returned 404 for `/api/v1/router/cohort` before its backend update. The static dashboard tolerates that staged backend state.

## Isolated proof

The dedicated `bridge_monitor_test` database and a fake Skynet adapter exercised the full path: a registered pre-solution broken module and trusted failing test, original parent prompt, one delivered paid result, one separately queued Qwen shadow result, exact raw and parsed response content, and an independent evaluation. The shadow result has no delivery timestamp, delivered content reference, parent disposition obligation or result-delivered event. Production capacity and route metrics exclude the shadow call; the separate cohort and per-task audit retain it. A repeated parent request returns the stored authoritative paid answer without another paid call.

The failure cases cover malformed/oversized model output, wrong code, verifier unavailability, cancellation, capture gaps, changed verifier settings, timeout/uncertain agent state, and failure to persist an evaluation. A launched uncertain job remains fenced with its slot held; no inference replay occurs. Raw Qwen output, parsed candidate, evaluation and completed job transition share one database transaction. The frozen case carries hashes for its baseline source, tests and full verifier settings, and mismatched settings make it ineligible. Retry tests also stop the process after parent-decision capture, after paid queue admission, and after paid completion but before shadow enqueue; each resumes without a duplicate paid model call.

## Verification and limits

- Python tests: 375 passed, 2 skipped in 280 seconds on the dedicated test database after the final review corrections.
- Dashboard TypeScript typecheck and static Next.js build: passed.
- Playwright browser tests: 22 passed in the final run, including router cohort isolation and a staged old-backend 404 fallback.
- The controlled proof uses a fake agent and verifier in the isolated test database. Docker access to `npipe:////./pipe/dockerDesktopLinuxEngine` was denied, so this run did not establish a live Docker-verifier result. No new Skynet traffic was generated.
- Natural router cohort: zero operational attempts because the router remains disabled. The 30 reconciled natural-attempt review is a future trigger, not a fabricated implementation exit check.

Before any live canary, follow [the operational runbook](../operations/parent-router-shadow.md): back up and migrate the operational database, restart its backend, review an exact shadow policy hash, then start the dedicated worker. Keep the original bridge source tree untouched.
