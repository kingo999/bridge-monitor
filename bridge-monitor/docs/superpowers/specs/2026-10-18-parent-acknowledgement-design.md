# Parent acknowledgement for Claude results — design

Date: 18 October 2026. Status: proposed for review.

## Purpose and boundary

Phase 1 already records Claude's disposition of a delivered Qwen result. It does
not know whether the GPT/Codex parent subsequently used Claude's answer. The
parent MCP `complete_work` operation is bound to a Claude worker execution and
must not be reused to impersonate a GPT consumer. This design adds an explicit
parent-side report while preserving **unknown** when no valid report exists.

The parent report is an assertion from the project-local MCP caller, not an
independently observed Codex final turn. The bridge does not authenticate a
particular Codex chat, observe the full main conversation, prove which GPT
model generated its answer, or prove that submitted text was shown unchanged
to the user. The dashboard and exports must label this evidence
**parent-reported**. They must not call it verified final use.

This work stays under `C:\AI\bridge-monitor`. It changes no existing bridge,
Skynet policy, model selection, routing decision, or Claude/Qwen disposition.

## Chosen approach

Expose a dedicated parent MCP acknowledgement operation, distinct from worker
completion. It is available only to the parent process that owns the Claude
call in its current MCP session. Its input identifies the delivered Claude
result, exact delivered-content SHA-256, judgment (`accepted`, `revised`,
`replaced`, or `unused`), a reason, and the parent-submitted answer text when
there is an answer to retain. For accepted, revised, and replaced, the answer
text is required so a parent output result and contribution relationship can
be recorded. For unused, the text is optional and cannot claim a contribution.
The operation returns a durable receipt or a precise rejection. It does not
invoke a model or change work resolution.

The bridge derives the parent actor ID from its owned call and producer
identity. It does not accept a client-supplied actor ID or reported GPT model
as authority. A restart loses the current process's in-memory call ownership;
an acknowledgement submitted after that boundary remains unavailable in this
increment. A future independently authenticated client integration may add
durable caller identity without weakening this rule.

An alternative passive Codex transcript observer would give stronger proof of
the final user-visible text, but no supported hook is established for this
pilot. Manual retrospective review would be simpler to store but cannot
capture the parent's live consumption decision. Neither is part of this
increment.

## Data flow and validation

1. When a Claude result is persisted as delivered to its GPT caller, create a
   GPT-stage disposition obligation for that result, its exact content hash,
   and the synthetic parent call already linked to the work item. A failed or
   undelivered Claude result creates no such obligation. Repeated completion
   must not create duplicate obligations.
2. The parent operation checks current-process call ownership, result/call
   ancestry, delivery state, obligation, and content hash in one transaction.
   It rejects an unrelated work item, Qwen result, conflicting hash, forged
   actor, impossible output reference, and a disposition for a result that was
   not recorded as delivered to this parent.
3. Store the exact bytes of the parent-submitted answer separately from the
   Claude and Qwen bodies. Associate them with a result on the synthetic GPT
   caller record, with no claimed model execution or user-delivery timestamp.
   Its capture layer identifies a parent MCP submission. A submitted answer is
   not treated as an observed final transcript.
4. Store the GPT-stage disposition against the Claude result. Accepted and
   revised judgments link that result to the parent-submitted output as a
   reported contribution; replaced records a reported replacement relationship.
   Unused has no contribution link. The operation verifies reference and
   judgment consistency using the existing disposition contract.
5. An identical receipt is idempotent. A changed report for the same
   result requires explicit supersession of the currently effective record;
   conflicting or cyclic lineage is rejected without partial writes. Invalid
   reports leave the obligation pending and do not trigger another inference.

The acknowledgement may precede the parent's final user-facing answer. The
record therefore says what the parent submitted to the bridge, even if the
later visible answer differs. The UI must present the capture boundary beside
the text. No automatic claim of correctness, savings, or end-to-end local
survival follows from this self-report.

## Statistics and dashboard

Consumer-stage denominators follow the result delivered to that consumer:
Claude stage counts delivered Qwen results; GPT stage counts delivered Claude
results. The current GPT-stage query incorrectly retains the Qwen denominator
and must be corrected with this change. Each stage separately reports valid
effective-disposition coverage, accepted, accepted-or-revised contribution,
and unknown. A missing, rejected, or conflicted acknowledgement is unknown,
never implicitly unused or accepted. A superseded disposition counts once.

Keep existing routing, not-routed, paid takeover, and Claude-stage work
resolution figures unchanged. A parent-reported accepted/revised judgment can
be shown in the exchange inspector and GPT-stage filter, with its provenance
and submitted answer beside Claude's delivered response. It does not silently
upgrade the existing `local_retained` resolution into verified final survival.
Historical reports continue to pin definition version, stage, cohort, and
as-of cutoff; the corrected GPT-stage definition gets a new version identifier
so earlier exports are interpretable.

## Checks and rollout

Use synthetic database-backed cases for accepted, revised, replaced, unused,
missing, invalid, replayed, superseded, and cross-call reports, including
several Claude workers in one parent process. Verify exact UTF-8 bytes and
hashes, independent stage denominators, no Qwen/Claude count regression, and
the parent-reported label in the dashboard. Test a parent process restart: its
old call cannot be acknowledged by an unrelated new process. Run the normal
Python, TypeScript, build, and browser checks, then inspect one controlled
native acknowledgement without changing routing policy. Existing records
without GPT reports remain unknown; no backfill is inferred.
