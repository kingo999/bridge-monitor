# Controlled router probes — 19 October 2026

Six controlled work items exercised the active shadow policy `75116df3dad408a444406b224c51c2cbc20d6451f7e5c347bf87b5d67bc78ec2`. Reusable inputs and host-held test vectors are in `tests/fixtures/router-routing-probes-20261019.json`. These are synthetic probes, not natural pre-solution work or evidence of paid savings. All four code-fix baselines failed the pinned Docker verifier before submission. Every parent received a delivered `claude-sonnet-5-5` answer; Qwen shadows, when queued, remained undelivered.

| Probe | Expected | Audited routing | Official Qwen evaluation | Separate diagnostic |
| --- | --- | --- | --- | --- |
| `easy-clamp-upper-bound` | Shadow | Queued and completed | `fail / output_format_invalid` | Extracted source passed trusted tests; paid source passed |
| `easy-first-nonblank` | Shadow | Queued and completed | `fail / output_format_invalid` | Extracted source passed trusted tests; paid source passed |
| `no-tests-no-verifier` | Paid only | `verifier_missing`; no shadow job | None | No trusted tests supplied |
| `outside-python-policy` | Paid only | `verifier_missing`; no shadow job | None | Multi-file TypeScript is also outside the configured category, but the earlier missing-verifier gate is the recorded reason |
| `maybe-positive-integer-parser` | Eligible; quality uncertain | Queued and completed | `fail / output_format_invalid` | Extracted source passed trusted tests; paid source passed |
| `maybe-merge-adjacent-intervals` | Eligible; quality uncertain | Queued and completed | `fail / output_format_invalid` | Extracted source passed trusted tests; paid source passed |

Qwen returned a fenced `json` block containing an `answer` string for each of the four shadows. The current prompt requests exactly one JSON object without Markdown, and the strict worker parser rejects the fences. The diagnostic manually extracted each `answer` string and ran it against the same trusted tests in the pinned Docker image; all four passed. This diagnostic **does not change** the stored official failures or establish safe live delivery. It isolates a response-envelope mismatch in these probes, while the preceding controlled pilot also showed a case whose extracted code retained the bug.

The authenticated dashboard cohort now shows seven controlled attempts in total (these six plus the preceding pilot), five completed shadows with five official `output_format_invalid` failures, and two paid-only decisions with `verifier_missing`. There are no delivered Qwen results. Provider-reported token counts exist, but they are not comparable billing evidence; avoided paid calls and billed savings remain unknown.

These results support a narrow next experiment: review a versioned prompt or strict, auditable handling of one exact fenced JSON wrapper, then rerun the same fixtures and keep the original failures in the audit. Do not weaken the Python verifier or enable live Qwen delivery from this small synthetic set. The first 30 reconciled natural, test-backed attempts remain the decision point for routing changes.
