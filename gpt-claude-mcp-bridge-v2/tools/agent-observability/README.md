# Agent observability dashboard (OBS-01)

A read-only, localhost-only dashboard over the v2 architect bridge's existing telemetry
and in-flight markers. Python 3.13 standard library only. It imports no bridge code,
runs no CLI or model, and never writes bridge state.

## Start and stop

```powershell
python C:/AI/gpt-claude-mcp-bridge-v2/tools/agent-observability/dashboard.py
```

Open <http://127.0.0.1:8089/>. Stop with `Ctrl+C` (the server closes its listener and exits).

| Option | Meaning |
|---|---|
| `--state-dir DIR` | Bridge state directory. Default `~/.gpt-architect-bridge-v2`. |
| `--port N` | Port (default `8089`, `0` = any free port). Only `127.0.0.1` is ever bound; there is no host option. |
| `--snapshot-once` | Print one snapshot as JSON and exit (no server). |

Use `--state-dir` with a fixture directory to try the dashboard without real state.

## What is read

Only `telemetry/calls.jsonl`, `telemetry/calls.1.jsonl` ... `calls.5.jsonl` and
`inflight/*.json` under the state directory. Links, junctions and other reparse points are
refused wherever they appear: on the state directory, on **any ancestor of it** (the absolute
path is checked lexically, without resolving links away), on the `telemetry`/`inflight`
directories and on each file. Non-regular files are refused too. Only an allow-listed
projection of each record is kept: prompts, answers, paths, process identities and unknown
fields are never served.

The source is reported **complete** only when nothing required was refused, unreadable,
truncated, partial or absent: a missing state directory, no telemetry files, no records, a
refused directory or file, a gap, a conflict, an unstable read, an exhausted scan budget,
unreadable or truncated in-flight markers all mark it **incomplete**, with a notice and a
code in `source.incomplete_reasons`. (A merely absent `inflight/` directory is not an error.)

## How to read the numbers

- **Execution** is `run_ms` (the subprocess). **End-to-end** is `ended_at - started_at`.
  **Queue** is `queue_ms`. They are different things and are shown separately.
- **Raw counters (as logged)** are the schema-1 counters exactly as logged. Each row says
  whether the basis is known: for the observed CLI (`0.162.1`) they are *session cumulative*,
  not per call, and must never be added together; another CLI version, a missing CLI version
  or an unsupported schema is labelled *basis unknown*, keeps its raw counters visible and
  shows no derived figures.
- **Estimated for this call** is the difference between two adjacent observations on the
  same repo/thread/model/session. It appears only when the earlier observation is trustworthy:
  an explicitly fresh thread (`resumed=false`, zero baseline) or a preceding valid call,
  monotonic counters, and no intervening inference-started failure, unknown record, conflict,
  rotation gap or parse/truncation gap. Repo, session name, model and thread must all be
  known (unknown identities never match each other, and a row with an unknown identity
  invalidates the chain). The difference must also be coherent: if two valid snapshots differ
  into cached > input or reasoning > output, the estimate is suppressed and only the later
  snapshot reseeds continuity. Even then it is an **interval estimate**: missing writes or
  turns outside this bridge cannot be excluded from these logs alone.
- Missing usage is **unknown**, never zero. Cache and reasoning ratios need a known positive
  denominator. Reasoning output tokens are a subset of output tokens, not additional cost.
- Nothing is billed cost, subscription balance or context-window size.
- Partial-persistence and in-doubt successes carry recovery uncertainty; failed, timed-out
  and cancelled calls are never presented as reviewed work.
- In-flight markers are observed states, not proof of a live process or of agent activity.
  A marker whose call is already in telemetry, or that is old, is flagged as possibly stale.

## Endpoints

`GET /`, `GET /static/app.js`, `GET /static/style.css`, `GET /api/snapshot`. Every other
method or path is refused (matched against the raw request target, no decoding or query).
Requests with a `Host` or `Origin` other than the bound loopback address are refused (403);
repeated `Host`, `Origin` or `Sec-Fetch-Site` headers are refused as ambiguous (400) rather
than first-one-wins. No CORS headers are sent.

## Tests

```powershell
python -B -I -m unittest discover -s C:/AI/gpt-claude-mcp-bridge-v2/tools/agent-observability/tests -v
python -B -I C:/AI/gpt-claude-mcp-bridge-v2/tools/agent-observability/tests/smoke_http.py
```

Tests use disposable fixtures only (`tempfile`, with a plain-`mkdir` fallback inside
`C:/AI/gpt-claude-mcp-bridge-v2/tools/agent-observability/.tmp-obs-tests/` when the temp directory is not writable).
Link and junction cases skip when the platform denies creating them.

## Limits

Read limits (enforced while scanning, newest history first; older files are left `not_read`
and the stream gets an explicit barrier plus an incomplete notice when a budget runs out):

| Limit | Value |
|---|---|
| bytes read per telemetry file | 8 MiB (tail; the head is marked truncated) |
| bytes per line | 256 KiB (longer lines are skipped as gaps) |
| lines visited per snapshot | 200 000 (blank lines included) |
| records parsed per snapshot | 20 000 |
| events (records + barriers) per snapshot | 30 000 |
| in-flight markers | 200, each at most 16 KiB |

Retention for display: the newest 500 calls are served. The schema/CLI allow-lists (`SUPPORTED_SCHEMA`, `SUPPORTED_CLI` in
`obs_accounting.py`) must be extended deliberately after verifying a new CLI's counter
semantics.
