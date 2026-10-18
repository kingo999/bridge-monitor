# Local-first evaluation path

## Purpose and boundary

Test whether sending a bounded GPT task to Skynet before invoking Sonnet reduces paid-worker usage without lowering answer quality. The existing Claude-first bridge, active policy, dashboard, and original bridge sources remain unchanged during evaluation. A task label, JSON syntax, or Qwen confidence is never sufficient evidence that an answer is correct.

## Candidate route

An evaluation runner reads a project-local TOML file on each run. Its default mode is disabled. In evaluation mode, a task is eligible only when its category is allowed, a registered independent verifier exists, the Qwen model profile and digest match the approved deployment, and the task passes a stable configured sampling gate. Ineligible tasks go straight to Sonnet. Eligible tasks go to the existing authenticated Skynet agent under its lease and deadline rules. The runner parses one JSON answer object (allowing a single enclosing Markdown JSON fence), then applies the verifier. Malformed, incorrect, unavailable, uncertain, or timed-out Qwen outcomes go to Sonnet automatically. It never retries an uncertain Qwen job.

The first verifier is an exact answer key for an offline, pre-registered holdout only. No live category receives a production verifier from this design. Adding one requires a separate review demonstrating that it checks semantics independently and cheaply enough to preserve the savings case. Model choice, prompt, raw and parsed responses, verification reason, fallback, usage, elapsed time, and outcome are saved in project-local experiment receipts. Those receipts are separate from production Postgres metrics and are retained for review.

## Holdout and decision

Use fresh, self-contained, read-only tasks with fixed answer keys written before model calls. For each task, measure Sonnet direct and the candidate local-first path. Count paid-worker calls and token categories per accepted answer; report total time and format failures separately from substantive errors. CLI cost estimates are list equivalents, not observed bills. Parent GPT/Codex usage is unavailable, so do not claim end-to-end paid savings.

The candidate stays offline until a reviewed, meaningful production verifier exists. A later live canary uses a separately reviewed configuration, small sampled share, automatic Sonnet fallback, and an explicit rollback to Claude-first. No current policy activation or dashboard cohort is changed by this work.

**Suggested execution model: Claude Opus.** Reviewing a pre-Sonnet route changes the answer-quality trust boundary. Advisory only; the user's chosen implementation model remains in use.
