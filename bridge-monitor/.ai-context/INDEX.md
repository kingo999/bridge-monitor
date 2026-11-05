# Bridge Monitor context

Project root: `C:\AI\bridge-monitor`. Git root: `C:\AI`.
Read the relevant topic before work; update its existing row when it changes.

This index records Bridge Monitor decisions only. Each other explicitly selected
project owns its own local notes in its own `.ai-context/`; do not mirror or
redirect them here. The canonical
[reusable guide](../docs/operations/project-markdown-context.md) covers project
selection, adoption and write grants.

| Topic | Decision | Implementation | Reviewed |
| --- | --- | --- | --- |
| [Scoped Maven project writes](scoped-maven-project-writes.md) | Exact POM grants and nested migration scopes, with a write-only protection for the legacy module | 86 repository checks passed and one symlink check skipped. Fresh launcher issued the MRI task grant. | 2026-11-05 |
| [Live orchestration flow](live-flow.md) | Core tiles always visible, idle connectors grey, optional workers filtered by availability, model and effort labeled from available evidence, parent MCP dispatch concurrent, localhost test login opt-in | Codex live effort capture verified against installed Codex fixture; Last configured fallback, ChatGPT handoff labels and idle Claude display verified live. Claude exit handling reaches reconciliation after telemetry timeout | 2026-11-05 |
| [Outbound network audit](outbound-network-audit.md) | Approved audit-only feature; unsafe always-on installation withdrawn | PostgreSQL history and Audit UI deployed, full-history search/snapshots verified; sync installed; capture tasks stopped/disabled; unelevated ETW stop right observed; retention 30d/72h/5GB selected but not enforced; hardening/enforcement required before activation | 2026-11-04 |
| [Persistent Markdown context](persistent-markdown-context.md) | Approved; project ownership clarified | Implemented; 19 permission fixture tests verified (approval is separate) | 2026-11-01 |
| [Design Mode](design-mode.md) | Opt-in read-only Chat→Codex→Claude→Chat planning; parent-only single owner | Native dashboard Design workflows tab deployed and browser-verified; Core, API, Monitor page, revise-awaiting-brief, admission/reconcile identity guards, per-iteration review budget and prior-review overlap guard (`prior_review_active`) implemented and offline-tested (test receipts: see the Design Mode topic); 013 live, first real review delivered; parent MCP reload reported after budget fix (first review replacement completed), live N≥2 budget path not yet exercised (verification pending). Monitor "Awaiting my decision" filter (owner-bound `design_mode_owner_id`, delivered-only) implemented and accepted by independent review: test_design_mode 67 + launcher behaviour 10 passed; browser: 3 new tests passed, legacy first test still fails at the Edge launch (0x3F0). Filter activated by Dave and live-verified by the parent (local config, server_pid 46712; accepted/failed excluded). Live N≥2 budget path still pending. Decision document return format (`bridge/design_delivery.py` + additive `decision_document`/`delivery_instructions` in retrieve) implemented: red 16 failed/67 passed, green test_design_mode 83 passed, regressions passed; parent MCP reload needed. 13 known `test_validation_export.py` failures unchanged | 2026-11-02 |

For an existing or new ChatGPT conversation, attach or paste this index and the
relevant topic, plus necessary excerpts from linked sources. Include the project
identity, Git revision and any uncommitted changes. ChatGPT has no automatic access
to these local files. Suggestions remain proposals until explicitly approved;
bring approved decisions back to an authorized Codex session to persist them.
