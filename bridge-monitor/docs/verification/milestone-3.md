# Milestone 3 exit evidence — 16 October 2026

Status: **live dashboard exit checks met in the isolated pilot**. This records the approved Milestone 3 scope only. It does not enable multi-worker scheduling, change a persistent Codex client registration, or advance to Milestone 4.

## Exit checks

| Check | Evidence |
|---|---|
| Consistent snapshot and resumable stream | Integration tests cover commit-ordered snapshot/replay, duplicate publications, retained-history gaps, reset, and unauthorized stream/content access. A browser watcher connected at cursor 1791 before the new pilot call, saw work item `eb2ffdca-957c-4e09-b408-0067e1535db5` while Sonnet was active, then saw its `paid_direct` final state at cursor 1835. Receipts: `var/reports/dashboard/live-watch.{ready,observed,final}.json`; screenshots: `live-inflight.png`, `live-final.png`. |
| Historical exchange after restart | After rebuilding and restarting the static-serving service, browser checks opened the earlier Qwen-accepted work item `983ce1f1-82fe-4707-8316-548d1130081f` and the new paid-direct item from PostgreSQL. Their original/effective prompts and delivered responses were inspectable on demand. Screenshots: `pilot-history.png` and `pilot-paid-direct.png`. |
| Synthetic statistics and history | The 100-item sample displayed routed 40/100, not routed 60/100, confirmed acceptance 24/40, and paid takeover 4/40, matching the backend definitions. Browser checks verify that switching to History or filtering a metric leaves the current topology labelled as live. |
| Static, authenticated browser behavior | Next.js exported `/` as a static route. The same-origin service supplied the API and opaque operator session. Browser checks cover login, unauthorized body access, reconnect, paused display retaining events, selection/scroll stability, and side-by-side inspection. Desktop and narrow screenshots are `desktop.png` and `narrow.png`. |
| Verification | `124 passed, 1 skipped, 1 warning` from the full Python suite against `bridge_monitor_test` (84.31 s); the skip is the opt-in live harness. Typecheck and static build passed. The six Playwright browser tests passed twice in one run: `12 passed` (14.0 s). The warning is Starlette TestClient's `httpx` deprecation. |
| Existing sources | `git diff --exit-code -- gpt-claude-mcp-bridge-v2` and the staged equivalent were empty. `tools/claude-bridge/implementer_bridge.py` retained SHA-256 `790B2CE34538A8D32A74AC581A834814D2A94A2EDBBF6ECC7377210193830A97`. Existing untracked `__pycache__` directories were left untouched. |

The new pilot used the same bounded `7 * 8` Codex → Sonnet → Qwen delegation scenario as Milestone 2. Sonnet's requested profile was `sonnet` and its reported model was `claude-sonnet-5-5`. The pre-existing uncertain Qwen reservation was still held, so this run could not dispatch a new local inference. The bridge recorded an initial local decision followed by a paid decision with reason `local_queue_timeout:local_deadline_or_uncertain_result`, delivered Sonnet's answer, and resolved the work as `paid_direct`. No Qwen result or local disposition was invented. The UI consequently shows **not routed** for this work item and **GPT disposition unknown**. The new live path proves browser observation and paid fallback, while the earlier historical path supplies the side-by-side Qwen response evidence.

The opt-in `run-codex-pilot.py --case normal --output-stem codex-m3-live-normal-20261016` process returned exit 1 because that harness's normal-case assertion specifically expects `local_retained`; its captured Codex CLI call itself exited 0, the bridge result was `completed`, and accounting was complete. The receipt is `var/probes/codex-m3-live-normal-20261016.receipt.json`. No Qwen slot was force-released or reconciled for the dashboard check.

## Commands used

From `C:\AI\bridge-monitor`:

```powershell
$env:BRIDGE_MONITOR_TEST_DSN='host=127.0.0.1 port=55481 dbname=bridge_monitor_test user=bridge_monitor'
.\.venv\Scripts\python.exe -m pytest -q --basetemp C:\AI\bridge-monitor\.pytest_tmp_m3
```

From `C:\AI\bridge-monitor\dashboard`:

```powershell
npm run typecheck
npm run build
$env:PLAYWRIGHT_BROWSERS_PATH='C:\AI\bridge-monitor\var\playwright-browsers'
npm run test:e2e -- --repeat-each=2
```

The browser suite inherited the dedicated test DSN and started its isolated server on loopback port 8874. The opt-in historical check used `node tests\pilot-check.mjs` against the separately served pilot database on loopback port 8873. Operator and producer credentials were supplied through environment variables and are omitted here.

Operational data and screenshots remain under `C:\AI\bridge-monitor\var`. Browser sessions are process-local and require login again after service restart. The held Qwen reservation remains a Milestone 2 uncertainty and was displayed as `1 held uncertain`; the dashboard only reads it.
