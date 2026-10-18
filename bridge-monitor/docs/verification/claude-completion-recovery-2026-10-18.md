# Claude completion recovery — 18 October 2026

Two earlier live Sonnet replies put prose before their required completion JSON.
The adapter retained those raw replies but could not parse the local-result
judgment, leaving Claude-stage accounting unknown. The adapter now accepts one
complete, unambiguous JSON object at the end of the reply. It still stores the
entire raw reply separately. Multiple completion objects, trailing prose, or
an incomplete embedded object remain unaccounted rather than guessed.

The full Python suite passed against the dedicated `bridge_monitor_test`
database: **270 passed, 2 skipped**, with one upstream Starlette deprecation
warning. The focused Claude adapter suite passed **13 tests**, including its
Windows process-cancellation checks. The first full-suite attempt did not start
because Windows had reserved the project's former PostgreSQL port, 55481.

Windows' dynamic TCP range starts at 49152, and its current exclusion includes
55481. The project-only PostgreSQL task configuration and protected desktop DSN
now use available loopback port **45481**. The original protected DSN was backed
up under `var/secrets/backups/`. The database and dashboard scheduled tasks are
running; the dashboard root and the new pilot detail API returned HTTP 200.

One controlled fresh GPT → Sonnet → Qwen exchange used the unchanged reviewed
policy `445c3debabdc63c876c61ace504b0d1f281f8f52b95cb2b8fbee80ee5e428629`.
For work item `57ad66fa-fb2e-4c2e-8a4e-e79bd811c5cc`, Sonnet reported the Qwen
result as **accepted**; Claude-stage accounting is complete, and the work
resolution is `local_retained`. The GPT parent independently reported
**revised**, with the delivered Claude result's SHA-256 verified. These are
model-reported judgments, not independent proof of final-answer quality. No
worker slot remained held. The pilot receipt and stream are under ignored
`var/probes/`; earlier incomplete reports were not retroactively changed.

The existing bridge sources and reviewed routing policy were not modified.
An already-running Codex desktop MCP process still had the pre-change database
connection; its status call did not return after the port move. A Codex app
restart is needed to launch that process with the updated project DSN. The
fresh controlled pilot and the dashboard both used the updated DSN successfully.
