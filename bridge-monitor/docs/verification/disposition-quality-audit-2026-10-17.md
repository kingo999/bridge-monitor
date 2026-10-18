# Pilot disposition quality audit — 17 October 2026

This is a read-only comparison of the five observed Claude `accepted`
dispositions in the side-by-side Bridge Monitor database. It compares each
delivered Qwen answer with the delivered Claude answer and the recorded reason.
The source prompts, complete responses, models, hashes and lineage remain in
PostgreSQL. This audit did not change a disposition, evaluation, policy or
derived statistic.

| Work item | Category | Comparison with recorded `accepted` judgment |
| --- | --- | --- |
| `a5da9c13-861c-4d85-a1f0-b27dcef3c4d4` | Arithmetic | Qwen gave `56`; Claude restated `7 * 8 = 56`. No apparent substantive change. |
| `f9ca3079-aba3-4ec1-b1c7-fdfc53036460` | Arithmetic | Same result and equivalent restatement. No apparent substantive change. |
| `77623ead-fcdd-4304-8de7-0eca1e918d00` | Information extraction | Claude removed a Markdown fence and whitespace from Qwen's JSON; the three extracted values stayed identical. Formatting-only acceptance is supported. |
| `79d4c1c0-c321-4603-a518-ad0476cfb67f` | Code reasoning | Claude added the explicit `ZeroDivisionError` explanation and a truthiness caveat, acknowledged in its own disposition reason. This is a clear `revised` candidate under the contract. |
| `7c285ddd-2594-48a2-8d4d-62fd22fd2a90` | Routing classification | Both classifications were correct, but Claude added an explanation about why later results do not change routing. This is a possible `revised` candidate; the distinction is less clear than the code example. |

The sample is small and selected for pilot validation. One of five recorded
acceptances clearly conflicts with the accepted/revised boundary, and a
second merits review. Neither observation establishes an error rate for
ordinary traffic. The later code check on work item
`bea7f25f-a49a-48b8-88d4-ecac049bf939` correctly recorded `revised`
after adding a substantive explanation, showing that the instruction can
produce the intended judgment but does not do so reliably in every case.

The dashboard now calls A/R **Claude-reported acceptance** and explains that
valid disposition records are not independent quality reviews. The stored
`confirmed_acceptance` API key and A/R numerator and denominator are
unchanged. Any future training example, learned gate or routing update should
use reviewed outcome evidence rather than treating the reported acceptance
rate as ground truth.

The updated dashboard passed TypeScript typecheck, static build and all eight
isolated browser tests. Its project-owned serving process was refreshed; the
live page returned HTTP 200 and the exported client bundle contains the new
label. No model call was made for this UI clarification.
