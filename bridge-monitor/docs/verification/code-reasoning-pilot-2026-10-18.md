# Code-reasoning pilot check — 18 October 2026

These two deliberately selected, read-only exchanges checked the current
`phase1-v1` routing arithmetic. They are diagnostic observations, not a
representative workload or evidence for a gate or model change. No existing
bridge source, active policy, stored disposition, or metric formula was changed.

The task supplied three work items: `A` dispatched to Skynet, `B` given a paid
decision, and `C` resolved `paid_direct` without a delegation record. From the
actual query formulas, routed = 1 (`A`), not routed = 1 (`B`), pending = 1
(`C`), and resolved without a delegation record = 1 (`C`). A resolution alone
does not remove `C` from the `pending` routing classification.

| Work item | Task call budget | Recorded route | Resolution | Local result |
| --- | ---: | --- | --- | --- |
| `19f22851-36b9-42b4-b151-d116ade7f450` | 1 | Two paid decisions, `root_budget_exhausted` | `paid_direct` | None |
| `65ff75dd-f7f4-438f-aafa-435e6825460c` | 2 | One local decision and its dispatch-recheck audit decision | `paid_revision_with_contribution` | One Qwen result |

The first budget reserved the single permitted call for Sonnet, leaving no
child call for Qwen. Sonnet answered correctly itself. It attempted a second
local delegation despite the one-call instruction; both attempts were denied
at the root budget and there was no Qwen job. This is a task-envelope error in
the diagnostic, not evidence that Skynet failed. The parent call was
`395c968c-8519-4e11-9a50-5ea81d1e8246`.

The corrected task used a call budget of two. Its parent call was
`6f97d6ab-088a-4a23-a91e-6ec010a65905`; Qwen call
`f77207c7-e46d-4cc1-85a7-5069ce5d8c2c` was dispatched at 00:24:22.697
Prague time and delivered its result at 00:25:19.654, about 57 seconds later.
Requested `sonnet` reported `claude-sonnet-5-5`; requested and reported local
model was `qwen2.5-coder:14b-instruct-q4_K_M`. Qwen's stored response gave the
correct four counts and explained that `C` has neither a paid decision nor a
dispatch. Sonnet retained those counts and marked the result `revised`.

Sonnet's disposition reason says it corrected Qwen's implication that pending
depends on resolution. The stored Qwen response already defines pending as
work neither dispatched nor not routed, so that claimed correction is not
supported by the text. Sonnet did add an explicit statement that pending does
not read the resolution map and suggested excluding resolved items from
pending. That suggestion conflicts with the approved `phase1-v1` definition
and was **not** adopted. The `revised` record remains an honest report of the
worker's judgment, not an independently confirmed quality label. Sonnet also
reported an extra empty-prompt local delegation attempt; the database has one
Qwen job and one Qwen result for this work item, with no second admitted job.

The corrected exchange reported Qwen usage of 161 input and 820 output tokens.
The Sonnet CLI reported six ordinary input, 6,973 cache-read input, 2,985
cache-creation input and 1,138 output tokens. These provenance-specific counts
do not establish a cost or savings estimate. Both parent calls completed with
accounting complete. The active policy remained `445c3debabdc63c8`, no
runtime slot was held, and the parent status reported healthy capture with no
known gaps or pending spool entries after the diagnostic.
