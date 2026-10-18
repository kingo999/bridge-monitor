# Local-first evaluation implementation plan

**Goal:** Build a disabled-by-default candidate route and run a fresh matched holdout without changing live bridge behavior.

**Architecture:** A small policy model controls evaluation eligibility. A pure router calls Qwen once, checks its answer with an injected verifier, and falls back to Sonnet on every failure. A project-local runner uses the existing authenticated Skynet agent and restricted Claude CLI, preserving complete receipts outside production telemetry.

**Spec:** `docs/design/local-first-evaluation-2026-10-18.md`

**Suggested execution model: Claude Opus.** The review of pre-Sonnet acceptance is a consequential trust-boundary check. Advisory; the selected implementation model stays unchanged.

## Constraints

- All new files are under `C:\AI\bridge-monitor`; do not edit `C:\AI\gpt-claude-mcp-bridge-v2`.
- No active policy activation, dashboard data write, or production route change.
- The exact-key verifier is confined to the offline holdout; format compliance alone cannot accept an answer.
- Use the pinned Qwen 2.5 profile and Sonnet paid profile already approved for the pilot.

## Tasks and exit checks

1. **Policy and routing core.** Add a validated, disabled-by-default local-first evaluation config and deterministic sampler; test that disabled, ineligible, or verifier-less tasks invoke only Sonnet. Add answer parsing and verification; test pass, wrong, malformed, timeout, and uncertain paths with a fake transport and prove at most one Qwen dispatch. Exit: focused tests red then green.
2. **Holdout runner.** Pre-register new tasks and exact keys. Use the current Skynet agent adapter and restricted Claude CLI without bridge DB writes; record prompts, raw responses, models, usage, latency, route and fallback reasons to a local receipt. Test receipt aggregation and resumed runs. Exit: full unit suite and one completed holdout receipt.
3. **Review and decision.** Compare direct and local-first results, describe contract versus semantic failures and token/latency limits. Obtain read-only Claude Opus review of the candidate and report; apply only justified fixes, then rerun focused and full checks. Exit: reviewable report and no active policy change. A live canary remains a separate review decision because no production verifier is registered here.
