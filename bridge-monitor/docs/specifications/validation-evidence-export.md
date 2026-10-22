# BM-04 validation evidence export

Status: isolated candidate API; no operational consumer or activation. This extends Bridge Monitor's existing records and operation projection. It introduces no routing policy, provider selector, collector, endpoint or schema migration.

## Local API

```python
from service.queries.validation_export import build_validation_export, encode_validation_export
report = build_validation_export(
    isolated_dsn, ['selected-task'], source_ref='reviewed-source-reference',
    allow_export=True, max_rows=2000,
)
body = encode_validation_export(report)  # bytes only; caller chooses storage
```

Disabled by default; enablement must be an actual boolean. IDs and source reference are nonblank strings of at most 256 characters. Select 1–16 unique task IDs. `source_ref` is caller-supplied provenance, not proof of the source deployed when records were captured. No database connection is opened for invalid options.

The report uses one PostgreSQL **REPEATABLE READ READ ONLY** transaction. `snapshot.observed_at` is its current transaction timestamp. Normalized domain tables are mutable: this API does not reconstruct historical state at an arbitrary time. Repeated exports have different timestamps; stable evidence manifests and their deterministic digest can be compared independently.

`max_rows` is a strict integer in 1–10000. It charges every returned query row, including metadata probes, distinct operation IDs and operation history rows read again by the existing projection. Each query fetches at most the remaining budget plus one overflow sentinel. Overflow rejects the entire report with `row_budget_exceeded`; it never returns truncated evidence. At most 16 operations are projected. Encoding uses sorted compact strict JSON, disallows NaN, and rejects output exceeding 16 MiB with `export_too_large`. These are output/fetch bounds, not a claim to bound PostgreSQL query-plan work or all transient Python memory.

## Counter contract

`claude_cli_result.execution.v1` verifies one existing local mapping: terminal result frames from `ClaudeCLIAdapter` become cumulative `usage.reported` observations with `scope=execution` and `provenance=claude_cli_result`. The fixture passes synthetic frames through that real adapter without spawning a CLI or contacting a provider. This verifies capture behavior, not provider identity, invoice semantics or a live release.

The captured fields stay separate:

| Counter | Export treatment |
| --- | --- |
| input_tokens | As captured; no cache fields added |
| output_tokens | As captured |
| cache_read_input_tokens | Separate as captured |
| cache_creation_input_tokens | Separate as captured |

There is no derived `total_tokens`. Strict nonnegative signed-64-bit integers are accepted; missing is null and explicit zero stays zero. One model execution is counted once. Latest cumulative observations replace earlier values; they are never added. Identical observation-ID facts deduplicate. Conflicting ID reuse (including across model owners), ambiguous simultaneous updates, regressing counters, mixed producers/incarnations/origins, unsupported source/scope, noncumulative reports, unresolved ownership or incomplete capture make that execution's usage unknown. This mapping requires explicit matching task, work item, call and attempt correlation; null fields are not silently inferred from the model ID. A valid partial latest snapshot retains only its supplied counters; older counters are not carried forward. Ordering trusts producer `occurred_at`; producer-sequence/clock-skew reconciliation is outside this slice, so observed chronology is not independently verified.

For each counter, `known_subtotal` covers known distinct model executions, `known_executions` states that denominator, and `expected_executions` counts recorded selected model executions. `total` is populated only if this recorded lineage is complete and every recorded model has that counter. An empty cohort has null totals. Even complete recorded coverage does not prove all real activity was captured. Unknown global capture gaps cannot be attributed automatically to selected tasks.

## Exported evidence and denominators

All selected recorded work items, parent/child calls, attempts and model executions are included, including failed, fallback, production and shadow paths. Admission/job state and `launch_claimed_jobs` are operational evidence, not proof a provider ran. Reserved tokens are budgets, never usage. Fallback/recovery links are exported as metadata; missing or cyclic parents, calls without attempts, results with inconsistent attempt ownership, fallback references outside the cohort and unresolved ownership prevent complete lineage totals. Partial or unresolved selected events and conflicted/uncertain/incomplete operation evidence also prevent complete totals. This is conservative: a queued unmodeled call does not establish measured zero usage. Cross-task parents are not recursively fetched. Missing selected tasks are explicit.

Requested and reported model identifiers remain separate. `different_identifiers` does not establish a wrong model: aliases can differ. `provider` stays null. Direct Codex execution records are visible with `usage_state=unsupported_mapping`; this slice does not import session histories or assign session totals to model executions. Their presence prevents a complete model-lineage total for the selected work.

Result deliveries and disposition claims are returned with `acceptance_basis=reported_unverified`. Only a single whole-result GPT-stage claim with a known judgment and no supersession among selected disposition rows contributes to the reported acceptance count. Part-only claims, absent/ambiguous chains, unknown judgments and selected cross-stage supersession remain unknown. Supersession by a disposition on an unselected result is outside this selected-cohort check; this is a reported claim, not authoritative current acceptance. This is neither final-survival verification nor independent quality validation. The existing parent-authentication/content-obligation logic remains the authority for those other questions.

Denominators explicitly count selected/found tasks, recorded work items/calls/attempts/model executions, admitted jobs, jobs with launch claims, source usage observations, orphan usage, delivered results, reported accepted deliveries, unknown acceptance and direct executions. There is no implicit all-actual-requests denominator, inferred quality rate or efficiency/savings claim.

Operation state/timing reuses `operation_snapshot` in the same snapshot. Output includes typed identity, states, counts, event references, capture/origin and timing fields; free-form payload references and verification details are omitted. Timing remains producer-clock evidence. Missing candidate tables produce `unavailable_schema`; the reader never provisions them. Ordinary migration compatibility is preserved.

## Disclosure and integrity boundaries

SQL selects metadata and allowlisted usage fields. Raw content bytes, prompt/answer bodies, job request JSON, gate facts, arbitrary model evidence and disposition prose are not fetched. Recognized router reason codes are exported; other decision reasons appear only as SHA-256 plus redacted/unclassified state. A hash removes the original text but does not conceal a guessable value from dictionary confirmation. Metadata identifiers, judgment labels and caller references remain caller-authorized data; this is not a generic secrets scanner. Do not place secrets in those fields or distribute exports beyond their authorized audience.

`evidence.events` contains selected stored event IDs and payload hashes. Its digest is SHA-256 over the deterministic encoded manifest. It is database provenance, not a signed provider receipt, full normalized-table hash or protection against database tampering. Legacy envelopes are not reconstructed and falsely certified after ingestion normalization. Operation evidence separately receives the existing strict projection's hash/identity checks.

`billed_cost` and `counterfactual_savings` remain null with an explicit unavailable basis. No prices, costs, measured savings, counterfactual routes or zero-cost assumptions are inferred.

## Validation and rollback

Run only against a separately selected disposable database using existing PostgreSQL binaries and frozen source. Tests cover the local adapter mapping, counter reconciliation, redaction, selected-cohort isolation, reported acceptance, global bounds, source event conflicts, direct records, operation reuse, absence of candidate schema, read-only database state and concurrent MVCC visibility. Prior ingest/contracts/lifecycle/recovery/history tests remain the regression boundary. No provider or browser probe is needed for this read-only API.

Rollback: stop calling the opt-in API, then revert the additive BM-04 commits in reverse dependency order (reference/review, reader, contract/reconciliation). No database rollback or operational policy change is required because this slice has no schema or runtime wiring. Exported bytes already saved by an explicit caller are that caller's responsibility. Keep validation receipts for review; stop only the owned scratch PostgreSQL instances and remove their plaintext credentials.

Further bundle work remains separately scoped: BM-05 non-executing catalogue/module status, BM-06 pinned focused context, then BM-07/08 bounded dispatch and cancellation/reconciliation. Add one justified integration and BM-16 paired value comparisons; BM-17 deterministic telemetry advice can build on this export. Real capture coverage and producer adoption, operator/UI consumers and retention coverage for a future study still need their own evidence and activation approval. This export supplies evidence for those decisions; it does not activate them.
