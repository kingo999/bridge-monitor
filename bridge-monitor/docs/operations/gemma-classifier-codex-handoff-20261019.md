# Codex handoff — Gemma classifier shadow (state as of 19 October 2026)

Paste everything below the line into Codex. It is self-contained.

---

You are picking up the Gemma 4 12B **advisory classifier shadow** in `C:\AI\bridge-monitor`. Read this
whole prompt, then `docs/design/gemma-classifier-shadow-2026-10-19.md`,
`docs/operations/gemma-classifier-shadow.md` and `docs/verification/gemma-classifier-shadow-20261019.md`.
Verify state read-only before acting; do not assume this text is still current.

**Suggested execution model: Claude Sonnet for the read-only and evidence work below; Claude Opus only if
you take on the `route_work` hook (a live trust-boundary change).** Advisory only; your own model choice stands.

## Hard boundaries (unchanged)

- Gemma only *recommends* (`tools`, `general_local`, `code_local`, `paid`, `clarify`). It must never choose,
  dispatch, admit, reject, reserve capacity, change policy/budgets, or alter the authoritative answer.
- Keep three things separate: the raw recommendation, the deterministic would-be-allowed verdict, and the route the
  unchanged system actually took. Never overwrite an unsafe raw label with a corrected one and score it as a success.
- Do not touch `C:\AI\gpt-claude-mcp-bridge-v2`, the `BridgeMonitor-RouterShadow` task, Skynet models/services,
  or the live policy. No paid calls. No other model or global runtime installs.
- Ask the user before: restarting Bridge Monitor services, applying schema, activating policy, editing
  `route_work`/MCP code, creating scheduled tasks, or pushing.
- The shared checkout is committed by another process under the message "shadow policy"; run `git status` first
  and commit only your own paths with explicit pathspecs.

## What exists and is running

- **Runtime (project-local, 127.0.0.1:11435, RX 7900 XT):** `scripts\gemma-start.cmd` / `scripts\gemma-stop.cmd`
  (double-click) or `scripts\gemma-runtime.py start|status|stop`. Pinned portable Ollama 0.30.9 and
  `gemma4:12b` (manifest `4eb23ef1…b05c`); ownership-checked stop; `status` reports `ready` only when the pinned digest,
  the discrete `ROCm/gfx1100` device in the log and full VRAM residency (`/api/ps`) are all shown.
  It is **stopped on purpose when the user games**. Do not leave it running or start it without a reason.
- **Code:** `bridge/classifier/` (rubric `router-rubric-v1`, client, strict validation, `policy.py`
  would-be-allowed checks, `runner.py` single-flight, `ingest.py`, `store.py`, `replay.py`, `lifecycle.py`),
  `contracts/classifier.py`, `service/queries/classifier.py` + `/api/v1/classifier/cohort|observations/{id}`
  (operator auth), dashboard panel `ClassifierCohort.tsx`, `scripts/classifier-shadow.py` (capture-case,
  capture-json, run, queue, evaluate, replay), config `config/gemma-classifier.example.toml` (disabled by default).
- **Operational database:** migration `007_classifier_shadow` is applied (two new tables, additive; rollback in
  `database/rollbacks/`). Backup `var/backups/bridge-monitor-20261019T153010Z.dump`. One live smoke observation was recorded
  (controlled case `easy-first-nonblank`, recommended `code_local`, allowed, GPU-resident).
- **Dashboard:** static export rebuilt (old copy in `var/backups/dashboard-out-pre-classifier-20261019`); backend restarted via
  `scripts\stop-monitor.ps1` and the `BridgeMonitor-Dashboard` supervisor. The panel was rendered in a browser on a throwaway
  instance over the isolated demo database and sits apart from the production cards, which stayed at zero.
- **Live policy:** `43a200ea7bf9f73a…` (activated 19 Oct 15:52). `docs/operations/parent-router-shadow.md` still names the
  older `75116df3…`; reconcile that doc with the user, not silently. Active policy, jobs, calls, decisions and the
  event count were unchanged by all of this work.
- **Tests:** `tests/classifier` (92 tests) need the dedicated DB:
  `.\.venv\Scripts\python.exe var\probes\gemma-classifier-shadow-20261019\run-tests.py tests\classifier`.
  That helper points pytest at `bridge_monitor_test` only. Related suites also passed (110 together with router queries, metrics, auth).

## Measured findings to preserve accurately

- Original 32 cases, seeds 42/43, new client on the 7900 XT: contract valid 64/64; **raw** route 30/32 per pass;
  **policy-allowed** 30/32; failures C08 and T08 (recommended `code_local`/`general_local` against unavailable workers), both blocked by policy
  and both kept as regression fixtures. All 64 responses were identical to the 18 Oct AMD responses. Warm median 1.556 s, p95 1.715 s
  (history 1.575 / 1.729; not thresholds).
- Held-out 18 cases (labels frozen first): raw 13/18 per pass; all 5 misses blocked by policy. The model ignored the stated availability fact in
  6 of 7 availability-adverse cases across both sets, so availability and permissions must stay enforced in code.
- Same input and seed gave a different route once between replay and the runner (cache state). Recommendations are not deterministic.
- 7900 XT: model 7.80 GiB fully in VRAM; about +8.8 GiB dedicated GPU and +7.6 GiB RAM while loaded; first load ~6.3–7.0 s
  (includes setup, not pure disk time). Gaming/render/IDE contention was not tested.
- No paid savings, avoided calls or electricity cost are established. Keep them "unknown". The 18 Oct mixed-benchmark runner
  exited with an error after the server was stopped before its final metadata query (all workflows and hashes were saved; a separate audit passed).
  Do not describe that runner as having exited successfully.

## What is left (in suggested order)

1. **Review only:** confirm the live panel and endpoints with the real operator login (the authenticated HTTP cohort endpoint was not exercised
   live; the panel was verified on a throwaway instance). Do not paste or store the operator token.
2. **Evidence:** run more *manual* captures of real pre-solution router cases (`capture-case` before `route_work`, with `--work-item-id` so the actual route
   links) and record reference labels with `evaluate`. The cohort now also shows linked Qwen shadow evaluations and paid-result dispositions as
   context (not as correctness). Report counts with their limits; do not claim reliability from small samples.
3. **Design for approval, do not implement unprompted:** (a) an optional, default-off, try/except-wrapped capture call in `route_work` (needs MCP
   server restarts and review; rollback = remove the call); (b) whether the runner should be supervised, given that Gemma is
   intentionally off while gaming (the runner already does nothing when the endpoint is down and unprocessed inputs expire after 10 minutes);
   (c) moving the enable switch from a local TOML into the reviewed policy.
4. **Explicitly not authorised:** letting Gemma choose or change any route, automatic promotion, a Gemma scheduled task, Skynet as a Gemma
   fallback, or any policy activation. Each needs a separate design and review.

## Quick checks

```powershell
git -C C:\AI status --short -- bridge-monitor
.\.venv\Scripts\python.exe scripts\gemma-runtime.py status
.\.venv\Scripts\python.exe var\probes\gemma-classifier-shadow-20261019\checkpoint.py <label>   # read-only live-state snapshot
```

Evidence (git-ignored): `var/probes/gemma-classifier-shadow-20261019/` — replay receipts (`replay-32`, `replay-heldout`), checkpoints,
parity file, E2E demo result, status receipts. Compare any new checkpoint with `checkpoint-final.json`.

Finish by reporting what you verified, what you changed, the exact commands you ran, live-state before/after, and anything left undone.
