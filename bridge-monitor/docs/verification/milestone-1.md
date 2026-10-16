# Milestone 1 exit evidence — 16 October 2026

Status: exit checks met for review. This document records only contracts, persistence, synthetic replay and reporting. It does not claim a live GPT/Claude/Qwen flow.

## Environment and scope

- Project: `C:\AI\bridge-monitor`; existing bridge sources and registrations were not edited.
- Python 3.13.7, PostgreSQL 18.0. Installed Python versions are pinned in `requirements.lock`; `.venv`, the pip cache and PostgreSQL data are under this project.
- Dedicated local test databases: `bridge_monitor_test` for the suite and a fresh `bridge_monitor_test_exit` for the exit replay. The test instance listens on loopback port 55481 and its data directory is `var/postgres`.
- Applied schema version in the fresh database: `001_event_store`. The migration creates immutable events/content, relational work/exchange/outcome facts, ordered publication and synthetic dashboard status projections. No Milestone 2 runtime tables are needed for the synthetic status view.

## Checks and observed results

| Check | Result |
|---|---|
| `python -m pytest tests -q` with `BRIDGE_MONITOR_TEST_DSN` set to the dedicated project database | **31 passed** in 11.08 seconds; one `StarletteDeprecationWarning` from FastAPI's current TestClient dependency |
| Fresh `python -m database.migrate BRIDGE_MONITOR_TEST_DSN` | Applied `001_event_store` |
| `python scripts/replay-fixtures.py tests/fixtures/demonstration.json`, run twice | First: **501 committed**; second: **501 duplicate** |
| Direct fresh-database counts | **100** work items; **501** events; **501** publication rows; **80** exact content objects; **40** delivered local results |
| `python scripts/inspect-exchange.py demo-work-061` with an operator token supplied on stdin | Returned `What is 2 + 2?\n`, `4\n`, and eight lineage events |
| `scripts/verify-paths.ps1 -ReportOnly` | Every planned runtime/output path resolves within the project |
| `contracts.export.export_schemas(Path('var/schemas'))` | Four schemas exported under project-local `var/schemas` |

The 100 work item fixture reproduces routed **40%**, explicitly not routed **60%**, disposition coverage **90%**, confirmed acceptance **60%**, acceptance among reported **66.7%**, local contribution **75%**, confirmed paid takeover **10%** and local resolution **24%**. Eligibility coverage is **100%** and final resolution coverage for delegated work is **85%** in this demonstration. The 40 delegated items each have retained prompt and response bytes. Separate synthetic events project active, queued, held-uncertain and blocked capacity, plus spool pressure, a known capture gap and recovery, using only the Milestone 1 schema.

Tests also exercise conflicting producer sequences, forged producer identity, over-limit content rejection, exact byte/hash round trips, transactional rollback, late parent reference reconciliation, out-of-order chunks, concurrent publication serialization, duplicate replay, paid-result exclusion from the local response denominator, and ambiguous dispositions. Synthetic records are excluded from the default observed cohort; empty denominators display N/A.

## Review boundaries

No actual model calls, live adapter observations, browser SSE connection or operational throughput were measured in this milestone. The local test database uses loopback trust authentication and a disposable operator token for verification only. Deployment credentials and policy values still require explicit configuration before live admission. The TestClient deprecation warning is in the installed dependency pair; all tests passed.
