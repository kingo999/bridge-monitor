# Controlled Bridge Monitor observation — 18 October 2026

Nine read-only GPT → Claude Sonnet exchanges were submitted between 10:49:30
and 10:53:30 Prague time. Five explicitly requested one Qwen check; four left
delegation optional. All nine completed with a delivered Claude result, complete
Claude-stage accounting, and an explicit GPT parent acknowledgement of
`accepted`. The parent reports describe use of Claude's delivered answer, not
an independent quality assessment. The tasks were deliberately selected and
are not a representative sample for routing policy or cost claims.

| Case | Work item | Route and Claude report | Parent-used answer |
| --- | --- | --- | --- |
| CR1 | `67ac8f23-0e92-42d3-826e-bb3439ab5135` | Qwen dispatched; `revised` | Two complete completion objects leave the raw reply unparsed, so no local disposition is recorded. |
| CR2 | `3dd5d81c-fde6-49ba-9f31-e058c0ee387a` | Qwen dispatched; `revised` | The first `claim_launch` returns true; the second returns false because launch was already claimed. |
| CR3 | `1c82b55e-f73a-4ee7-bcfd-1842425d7500` | Qwen dispatched; `revised` | After launch, `abort_unlaunched` returns false and leaves the job, slot, locks and reservation unchanged. |
| RC1 | `9ce92a0b-d5ce-44ed-bd30-5248b68c68f6` | Direct paid answer; no route decision | A/B/C yield routed 1, not routed 1, pending 1; C's `paid_direct` resolution does not change its route class. |
| MA1 | `d0e09be6-c8ea-46f7-958f-c55b87e3ecf8` | Direct paid answer; no route decision | For R=3, K=1, A=1: coverage 1/3, confirmed acceptance 1/3, acceptance among reported 1/1. |
| IE1 | `811c77c0-37f7-4ffd-b257-cc54c43f5477` | Direct paid answer; no route decision | Config: port 45481, Claude limits 2 global/1 per root, Qwen 1, Sonnet and pinned Qwen profile. |
| OP1 | `fb128a18-0be0-421a-ac44-a78e58d1f566` | Direct paid answer; no route decision | Retained tombstones omit removed bodies; an expired SSE cursor resets to a snapshot without rerunning a model. |
| OP2 | `b9452699-030e-41e0-b7fe-011bb1978e91` | Qwen dispatched; `revised` | Timeout triggers tracked paid continuation; keep the Qwen slot held until correlated terminal evidence. |
| DQ1 | `7ae6053a-5c98-498d-a199-dcdb0453f1e2` | Qwen dispatched; `revised` | Adding the n=0 caveat is a substantive revision, not acceptance. |

The five forced cases produced five Qwen jobs and five delivered results. All
five were marked `revised` by Claude. Four of these five Claude executions
attempted a second local tool call despite the one-call instruction. The bridge
admitted **no second Qwen job**: three further attempts produced paid decisions
with `root_budget_exhausted`, and one failed tool validation before a decision.
These attempts consume some Claude time and add noisy evidence, but do not
consume a second Qwen execution. No slot remained held at the end.
The follow-up bridge change detects a previously delivered local result when
the root call budget is exhausted. A further request then returns
`already_delivered` without admitting a job or recording another paid decision.
This does not limit tasks with remaining call budget or remove the earlier
attempt records. The changed MCP process requires a fresh start before this
behavior is live in the desktop app.

Before this observation, the 21-work-item observed cohort had 15 routed, four
explicitly not routed, and two pending/unknown. Immediately after these nine
exchanges it had 30 items: 20 routed, four explicitly not routed, and six
pending/unknown. The four newly direct answers have `paid_direct` resolutions
but no delegation decision. Under the approved `phase1-v2` definition, a
resolution alone does not prove an explicit routing choice, so they remain in
pending/unknown and in the separate resolved-without-route diagnostic. The
post-observation code change asks Claude to report `route_choice=paid_direct`
and records an explicit paid decision only when the Claude stream positively
reports zero local tool attempts and no child call or decision was observed.
A regression check covers a validation-denied local attempt whose `tool.started`
event is initially queued in the durable spool. It does not relabel these
historical four items or
change the metric formula.

At the Claude consumer stage, 19 Qwen results were delivered across the full
30-item cohort; 16 have effective reported dispositions, nine of which are
`accepted`. Thus coverage is 16/19, confirmed reported acceptance 9/19 and
acceptance among reported 9/16. At the GPT consumer stage, 30 Claude results
were delivered and 11 have valid parent reports, ten `accepted`: coverage
11/30 and reported acceptance 10/30. These are model-reported judgments, not
independent correctness grades. The historical three missing Qwen dispositions
remain unknown rather than being backfilled. Confirmed paid takeover is 1/20
routed items, from the earlier controlled timeout; none occurred in these nine.

Manual comparison identified reason-quality discrepancies without changing
stored judgments. In CR3, Qwen had mentioned the held slot, yet Claude's
`revised` reason says it had omitted it. In OP2, Qwen's suggestion that a slot
may be reused without definitive termination evidence was unsafe; Claude
corrected it. An earlier accepted record (`79d4c1c0-c321-4603-a518-ad0476cfb67f`)
appears to add a substantive exception/truthiness caveat and merits independent
review as possibly `revised`. The earlier `65ff75dd-f7f4-438f-aafa-435e6825460c`
reason wrongly criticizes Qwen for a pending-definition error that is absent
from Qwen's stored answer. These observations are review leads, not automatic
quality labels or gate updates.

All 229 prompt/result-linked content objects in the 30-item cohort were
complete and matched their stored byte lengths and SHA-256 hashes. The nine
new items had 1,745 captured events and zero unresolved references. The five
forced-local cases took a median 34.56 seconds end to end (31.38–40.05); the
four optional/direct cases took a median 9.48 seconds (7.38–10.58). Case
selection differed, so this is not a controlled comparison or a savings
estimate. The active gate and model profiles were unchanged throughout the
observation.
