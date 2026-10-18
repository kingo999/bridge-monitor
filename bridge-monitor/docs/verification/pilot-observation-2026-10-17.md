# Bridge Monitor side-by-side pilot observation — 17 October 2026

This review measures the project pilot database as of
`2026-10-17T21:28:15.742414Z` (23:28 Prague time). It does not change routing,
models, gates or the existing bridge registration. The observed cohort excludes
synthetic fixtures. The active Skynet-agent policy is version
`445c3debabdc63c8`, activated at 22:56:34 Prague time, with a 120-second
Qwen execution limit. The bridge reports Qwen
`qwen2.5-coder:14b-instruct-q4_K_M` and Claude `claude-sonnet-5-5` for the
current-policy exchanges.

| Measure | Current agent policy (4 work items) | All observed pilot history (10 work items) |
| --- | ---: | ---: |
| Routed to Skynet | 4 | 5 |
| Explicitly not routed | 0 | 3 |
| Residual `pending` route classification | 0 | 2 |
| Qwen results delivered to Claude | 4 | 4 |
| Claude reported acceptance of delivered Qwen result | 4 | 4 |
| Local answer retained in recorded resolution | 4 | 4 |
| Paid takeover after local dispatch | 0 | 1 |
| Paid direct resolution | 0 | 4 |
| Work item without a recorded resolution | 0 | 1 |

All four current-policy work items followed GPT → Sonnet → Qwen. Their recorded
end-to-end intervals from work creation to resolution were 16.500, 14.510,
20.370 and 26.361 seconds. Each has a Skynet dispatch, a delivered Qwen
result, an `accepted` Claude disposition and a `local_retained` resolution. The retained
[registered-client](../../var/probes/registered-agent-pilot-20261017.audit.json)
and [native desktop](../../var/probes/native-agent-pilot-20261017.audit.json)
audits compare the first two prompts, responses, model choices and work-item
links. Two further native calls tested structured extraction and short Python
code reasoning. Their [body-free audit receipt](../../var/probes/varied-agent-pilot-20261017.audit.json)
checks 12 prompt/response content references; exact bytes remain in PostgreSQL. The
extraction returned the three requested fields correctly and was accepted.
The code answer gave a correct empty-list guard. No result was recorded as
revised or replaced, but the code-task judgment needs review below. GPT's
final acceptance is not a bridge disposition in these exchanges.

For the code task, Qwen supplied the correct guarded expression and said it
avoids division by zero. Claude's delivered response added the exact
`ZeroDivisionError` behavior and a caveat about truthiness of other inputs.
Claude nevertheless reported `accepted`, with a reason acknowledging those
additions. Under the approved contract, a **substantive addition with useful
local substance retained is `revised`**; this looks like a misclassification.
The original disposition and `local_retained` resolution remain intact as
observed data. Thus the dashboard's 4/4 recorded acceptance is **not** an
independently validated 100% acceptance rate. The worker instruction now
states the accepted/revised boundary explicitly for future calls. After the
desktop restart, its new bridge process started later than the source edit;
a model-free status call succeeded with healthy capture. No additional model
call was made merely to test the wording change.

The older historical cohort includes the intentional one-second timeout test:
one routed item proceeded to a recorded paid replacement. Three explicit
pre-dispatch paid decisions were `owner_model_incompatible`; they remain **not
routed** under the approved definition. Two other completed `paid_direct`
items had no delegation decision at all. The present `phase1-v1` metric treats
those two as residual `pending`, even though their work is complete; that
label must not be read as two live queued jobs. One earlier paid-only exchange
returned an answer but failed completion accounting, leaving its work item
unresolved; a later corrected exchange completed successfully.

The database contains 824 events and 613 retained content objects across the
observed history. A read-only byte check matched all 613 stored lengths and
SHA-256 hashes; no body was missing, and no event had an unresolved reference.
At the final operational check, the active policy hash matched its approved
source, the dashboard returned HTTP 200, and there were zero busy jobs, held
slots or source locks. A model-free desktop status call reported healthy
capture, zero known gaps and zero pending spool entries.

The review exposed a history-list display defect: `work_items.resolved_at`
was null even where a `work_resolutions` record existed. The list now takes
the latest resolution timestamp at its requested `as_of`; its detail view and
metric formulas already used the resolution records. A regression failed
before this query fix, then passed with the historical cutoff check. The
related history, metrics and query-bound suites passed **18 tests**, and the
task-owned dashboard was restarted and returned HTTP 200.
The focused Claude adapter suite passed **9 tests** with normal Windows
process rights after the guidance change. Its sandboxed run hit two known
process-tree termination permission failures; those tests passed when rerun
with the process rights used for the live pilot.

These are tiny, selected validation tasks, not a representative workload. They
do not establish cost savings, an acceptance rate for normal traffic, or a
reason to broaden routing. The next measurement should use a broader sample of
real read-only tasks and keep the existing explicit not-routed rule. Before relying
on aggregate `pending`, add a separate count for completed work with no
delegation record so that absence of a routing attempt is visible.

## Follow-up validation after the disposition wording change

A subsequent native GPT → Sonnet → Qwen code check used work item
`c6b9334a-6d22-4452-a1b6-9a1a03da3041`, Claude call
`5df27fe9-9ac4-42c4-a64f-fd790b319b66` and Qwen call
`b2db5a88-d46a-454a-9191-c9e4d775a584`. The captured Claude system
message contained the new accepted/revised guidance. Qwen returned the
correct guarded Python expression, and Claude's delivered response added
substantive explanations of the empty-list exception and `None` behavior.
Despite that, its embedded claim said `accepted`; by the contract, that
answer is a revision with contribution.

Claude also attempted a second local delegation even though the caller
specified exactly one. The second child call
`e2e1aa91-43dc-4b61-86b3-f0311b0df47d` was denied before dispatch as
`root_budget_exhausted`. It is an explicit **not-routed** decision, not a
second Qwen inference or a timeout. Claude's final text placed prose before
the completion JSON. The strict adapter retained those exact bytes as the
delivered answer but did not extract an ambiguous embedded claim. The
completion diagnostic is `missing consumer dispositions`; no Claude
disposition or work resolution was recorded for this item. The original
observation was preserved, with no inferred or backfilled acceptance.

The nine captured prompt/response content objects for this work item matched
their stored lengths and SHA-256 hashes. The focused Claude adapter suite
passed **9 tests** after a narrow instruction change: one-call requests now
forbid test/retry delegations, the whole final response must be one JSON
object, and an unchanged code line with a substantive added explanation must
be reported as `revised`. A post-call operational check found no busy jobs,
held slots or source locks; the dashboard returned HTTP 200 and the approved
policy hash remained active.

A fresh isolated Codex process then validated the updated instruction with
the same bounded code question: task
`0c5be9f8-563b-4819-ace4-78a23bf53063`, work item
`bea7f25f-a49a-48b8-88d4-ecac049bf939`. Its retained
[Codex transcript](../../var/probes/revision-instruction-0c5be9f8-563b-4819-ace4-78a23bf53063.jsonl)
contains one parent MCP call. The captured Claude system prompt contains the
new wording. Claude made exactly one Qwen child call, used the correct code
line, added the requested substantive explanation, and returned a single
structured JSON completion. The bridge recorded `revised` with reason,
linked the Qwen and Claude results, and resolved the work as
`paid_revision_with_contribution`. `accounting_complete` was true, with no
completion diagnostics. All eight prompt/response content objects for this
work item matched their stored lengths and SHA-256 hashes, and no slot
remained held. This verifies one bounded live instance of the corrected
behavior; it does not establish reliability across ordinary traffic. The
desktop app was restarted after this last instruction edit. Its MCP process
started at 23:44 Prague time, after the file's 23:40 edit. A model-free
desktop status call reported healthy capture with zero known gaps and zero
pending spool entries. The dashboard returned HTTP 200 with no busy jobs,
held slots or source locks. The corrected instruction is loaded for future
desktop exchanges; the fresh isolated exchange above is its live behavior
check, so no extra model call was needed after the restart.

## Routing-unknown display clarification

At 23:52 Prague time the observed cohort contained 12 work items: seven
routed, three explicitly not routed and two in the existing
`pending`/unknown classification. Both of those two had `paid_direct`
resolutions but no delegation decision, so they are **not live queued work**.
The query now supplies a separate `resolved_without_delegation_record`
coverage count of two without changing the three approved route numerators.
The dashboard's pending card shows that count, and resolved work-list rows
without a route record say `No route record` rather than `Pending`. The
metric/history suites passed 17 tests, dashboard typecheck and static build
passed, and all eight browser tests passed against the isolated test server.
The project-owned dashboard process was restarted through its guarded stop
command; its watchdog brought it back. The live page, operator session and
metrics endpoint each returned HTTP 200, and the endpoint reported the same
12-work-item routing split and two resolved items without a delegation record.

## Additional data-only routing classification sample

Work item `7c285ddd-2594-48a2-8d4d-62fd22fd2a90` asked Claude to use one
Qwen check to classify two supplied event sequences. The correct answer is
that A is explicitly not routed because it has a paid decision and no Skynet
dispatch, while B is routed because it has an actual Skynet dispatch. Claude
call `c8416721-c9fe-4842-9096-31ace2484864` made one Qwen child call
`88ebd104-822b-49b6-af60-0b0336b9f837`; both models gave the correct
classification. The bridge recorded `accepted`, `local_retained` and complete
accounting, with no held slot. All eight captured prompt/response objects
matched their stored lengths and hashes.

The recorded `accepted` judgment deserves manual review. Qwen's reason for B
was simply that actual Skynet dispatch meets the routing rule; Claude's
delivered answer additionally explained that a later Qwen result and Claude
revision do not change the classification. Under the agreed accepted/revised
boundary, this appears to be a substantive explanation and therefore
`revised`. The model's reason claimed it had only reformatted Qwen's reasons.
This discrepancy remains as originally recorded, with no backfilled change to
the disposition or derived statistics. It reinforces that a valid model
disposition is not an independent quality assessment.

The [disposition quality audit](disposition-quality-audit-2026-10-17.md)
compares all five observed `accepted` Qwen-to-Claude pairs. The dashboard
labels A/R as Claude-reported acceptance to avoid implying an independent
quality check; the underlying metric formula is unchanged.
