# Milestone 1 exit evidence — 16 October 2026

Status: exit checks rerun after the read-only Claude review; awaiting acceptance. This document records only contracts, persistence, synthetic replay and reporting. It does not claim a live GPT/Claude/Qwen flow.

## Environment and scope

- Project: `C:\AI\bridge-monitor`; existing bridge sources and registrations were not edited.
- Python 3.13.7, PostgreSQL 18.0. Installed Python versions are pinned in `requirements.lock`; `.venv`, the pip cache and PostgreSQL data are under this project.
- Dedicated local test databases: `bridge_monitor_test` for the suite and a fresh `bridge_monitor_test_review_exit` for the exit replay. The test instance listens on loopback port 55481 and its data directory is `var/postgres`.
- Applied schema version in the fresh database: `001_event_store`. The migration retains event facts and exact content, relational work/exchange/outcome facts, ordered publication and synthetic dashboard status projections. `events.unresolved_references` is mutable reconciliation state; the observed facts are not changed. No Milestone 2 runtime tables are needed for the synthetic status view.

## Checks and observed results

| Check | Result |
|---|---|
| `python -m pytest tests -q` with `BRIDGE_MONITOR_TEST_DSN` set to the dedicated project database | **46 passed** in 20.04 seconds; one `StarletteDeprecationWarning` from FastAPI's current TestClient dependency |
| Fresh migration in `bridge_monitor_test_review_exit` | Applied only `001_event_store` |
| Demonstration replay in that fresh database, run twice | First: **621 committed**; second: **621 duplicate** |
| Direct fresh-database counts | **100** work items; **621** events; **621** publication rows; **80** exact content objects; **40** attempts, **40** model executions, **40** delivered local results |
| Edge-case replay in fresh `bridge_monitor_test_edge_exit`, run twice | Applied only `001_event_store`; first: **41 committed**; second: **41 duplicate**. Eight stored work items produce seven leaf accounting items: five routed, one explicitly not routed, one pending |
| `scripts/inspect-exchange.py demo-work-061` with a disposable token on stdin | Returned `What is 2 + 2?\n`, `4\n`, and eleven lineage events |
| `scripts/verify-paths.ps1 -ReportOnly` | Every planned runtime/output path resolves within the project |
| `contracts.export.export_schemas(Path('var/schemas'))` | Four schemas exported under project-local `var/schemas` |

The 100 work item fixture reproduces routed **40%**, explicitly not routed **60%**, disposition coverage **90%**, confirmed acceptance **60%**, acceptance among reported **66.7%**, local contribution **75%**, confirmed paid takeover **10%** and local resolution **24%**. Eligibility coverage is **100%** and final resolution coverage for delegated work is **85%** in this demonstration. The 40 delegated items each have retained prompt and response bytes. Separate synthetic events project active, queued, held-uncertain and blocked capacity, plus spool pressure, a known capture gap and recovery, using only the Milestone 1 schema.

Tests also exercise conflicting producer sequences, forged producer identity, over-limit content rejection, exact byte/hash round trips, transactional rollback, late parent reference reconciliation beyond 1,000 stale rows, out-of-order chunks, concurrent publication serialization, simultaneous duplicate event and content delivery, duplicate replay, paid-result exclusion from local response accounting and inspection, and ambiguous dispositions. Synthetic records are excluded from the default observed cohort; empty denominators display N/A.

## Review follow-up

- Malformed projectable facts are checked before storage. A deferred projection conflict is isolated with a `projection_error:*` marker on that event, so later batches continue. Such an event has a committed receipt and publication entry but no relational projection. An operator can find it with `SELECT event_id, unresolved_references FROM events WHERE unresolved_references[1] LIKE 'projection_error:%'`; repair and replay are not automated in this milestone.
- Reconciliation scans every pending page before declaring no progress. Attempt-start events now materialize attempt rows, allowing results, dispositions and resolutions that reference attempts to project.
- Synthetic delegated calls now carry distinct requested and reported model events. The inspector selects the Skynet-dispatched call for local prompt and response fields. Empty or shared API credentials are rejected at startup; content ID replay compares immutable metadata as well as bytes.
- The historical cutoff test now selects a time after the first local result and before its disposition. Concurrent duplicate delivery returns one committed and one duplicate receipt.
- Invalid capacity/capture observations are rejected before SQL projection, so a bad count cannot turn the ingestion endpoint into a database error.
- Delivered results require a call identity on the envelope so their relational lineage is the same whether references resolve immediately or later.
- The separate edge-case fixture is executable and covers retry within one work item, decomposition frontier, disposition supersession, a response that was not delivered, mixed guidance manifests, unknown final lineage and a pending route without changing the 100-item demonstration cohort.

## Review boundaries

No actual model calls, live adapter observations, browser SSE connection or operational throughput were measured in this milestone. The local test database uses loopback trust authentication and a disposable operator token for verification only. Deployment credentials and policy values still require explicit configuration before live admission. The TestClient deprecation warning is in the installed dependency pair. The single-row status projection still uses arrival order, and an `as_of` re-query can change when late events arrive; exported-report freezing and live multi-worker status remain future work.
