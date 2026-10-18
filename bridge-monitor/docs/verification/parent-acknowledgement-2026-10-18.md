# Parent acknowledgement implementation verification — 18 October 2026

The project-local parent MCP operation now records a GPT-stage report about a
delivered Claude result. It stores the submitted answer bytes separately and
labels the judgment as parent-reported. The result reply and status path expose
the delivered content hash to the owning parent process, so its report can name
the exact Claude result it received. Missing or rejected reports remain unknown.

## Controlled native exchange

An isolated Codex parent process used the active reviewed policy
`445c3debabdc63c876c61ace504b0d1f281f8f52b95cb2b8fbee80ee5e428629`
with Sonnet and `qwen2.5-coder:14b-instruct-q4_K_M`. Preflight found Skynet
ready and no held slots. Task `a8c839b6-205c-4111-94fe-f8be5d2fce8b` delegated
one arithmetic check to Claude. Qwen answered 518; Claude delivered a response
with result `ab9263b2-4eea-4283-8c59-cf48b34152fa` and SHA-256
`1188e802804bb5d939f60e73d9fa8330314b8ebfcb7a8220a3103536a6b6f773`.
Codex then called `acknowledge_claude_result` with that hash and reported
`accepted`, submitting `37 × 14 = 518` as its intended answer. The parent report
is `0d9de2ae-a6ae-43d0-a32e-1b4c297797aa`; its output result is
`77c4b682-a393-52ff-8671-02a41730f8cb`.

Database inspection found the GPT obligation, matching input hash, one
`disposition.recorded` event, and an `accepted_contribution` link. The pilot
process exited successfully with no held slot. Its one-result GPT cohort had
R=1, K=1, A=1. The running dashboard returned `phase1-v2`, and its detail API
showed the parent actor and submitted-output reference. After the stricter
validity query was added, a direct operational query still counted this report.
The parent judgment is a claim about use of Claude's response, not independent
proof of the final user-visible Codex answer or its model identity.

The same live exchange exposed a separate worker output-contract failure:
Sonnet prefixed prose to its required final JSON. The bridge preserved the raw
response but did not parse its Qwen disposition or record a work resolution;
Claude-stage accounting remained incomplete. A second controlled attempt
again produced prose before JSON, and Codex sent a malformed parent call
without the required `report` object. That call was rejected; its GPT
disposition remained unknown. Neither attempt left a held Qwen slot. These
failures are visible in the local pilot receipts rather than counted as
acceptance. The receipts and JSONL streams are under ignored `var/probes/`.

## Review and checks

Claude's enforced read-only review first landed in an unrelated default
repository and could not inspect this project. A second review explicitly
added the `C:\AI` Git root, inspected the implementation, and verified the
source/Git snapshots unchanged. Its concrete findings led to focused fixes:
effective lineage on supersession, exact replay identity for unused answers,
transactional event publication, valid GPT-stage metrics and work-list
judgments, and unambiguous inspector behavior with sibling Claude results.
The follow-up review found no blocking issue; a final correction made the
detail API expose the same GPT-report validity to the inspector.
The event now joins the disposition and publication stream in the same
database transaction; a focused rollback test confirms that a failed event
write leaves no parent disposition.

The final Python suite passed: **267 passed, 2 skipped**, with one upstream
Starlette deprecation warning. Dashboard typecheck and static build passed;
the Chrome-channel Playwright suite passed **20 tests**. An earlier full-suite
run hit a load-sensitive two-second synthetic fallback window; the test-only
window was increased to eight seconds, and the final complete run passed.
The active routing policy and model pins were not changed. The existing bridge
has no tracked or staged diff from this work.
