# BM-07 minimal bounded dispatch handoff

Goal: verify pinned task context and current bounds before handing one explicitly
selected paid call to existing durable ingress/admission. Keep live dispatch
inactive. Catalogue/status remains descriptive and grants no authority.

## Scope and existing authorities

`bridge/adapters/pinned_dispatch.py` is an unregistered opt-in adapter. It is not
imported by the MCP server, scheduler, dashboard or any worker. No service,
launcher, database schema, runtime policy or provider configuration is changed.
It supports the existing paid profile only, explicitly named by the caller; it
does not choose a provider, perform fallback, or implement local guidance.
The existing guidance registry/selection and dispatch-time withdrawal gates are
preserved. Qwen support and runtime adoption remain separately scoped.

`prepare_pinned_prompt` revalidates the bundle, compares its digest with the
caller's independently retained expected digest, checks task/work-item identity
and the caller's current context byte limit, and serializes an instruction plus
the full pinned bundle as deterministic JSON. The whole encoded prompt must fit
the supplied prompt byte limit, including JSON overhead. UTF-8 bytes are exact
through the bundle's JSON representation. Instruction and data fields do not
provide a prompt-injection security guarantee. The context digest does not bind
the separately supplied instruction; ingress captures the entire prompt.

`submit_pinned_context` requires a positive integer token reservation within the
request's stated token budget. It refreshes the existing active policy, requires
the caller's model to match the reviewed paid profile, and checks the whole
prompt against `deployment.payload_limit_bytes`. It then calls the existing
`CodexIngress.submit` with the policy version observed at verification. The
existing admission transaction rejects a concurrent policy version change.

The database remains the authority for remaining root call/token reservations,
queue capacity, call conflicts and admission state. Existing admission can
reject a call after capture; retaining that prompt is audit evidence, not proof
of admission or execution. Exact retries reuse the stored prompt content ID so
the existing idempotent admission does not reserve twice. A changed prompt under
the same call ID is rejected. Concurrent first submissions may produce a conflict
instead of a transparent retry; no automatic replay or fallback is introduced.

The adapter queues only. It never invokes `claim_dispatch`, `claim_launch`, a
provider adapter or process launcher. Existing ownership/launch claim functions
are exercised separately in the disposable test database using synthetic calls;
claiming a database launch flag does not run inference. This slice establishes
the bounded admission handoff, not worker consumption, cancellation, model output
limits or completed execution. Token reservations remain budgets, not measured
usage. The caller's current context cap is an explicit input; its authorization
is not inferred from catalogue fields or the bundle's self-consistency.

## BM-06 review gaps addressed

- Reject lazy inputs without iteration; require plain in-memory lists/tuples.
- Check all supplied raw bodies against per-item and aggregate byte caps before
  `ContentObject` revalidation/hashing, including unselected content.
- Require an explicit UTF-8 encoding label; other source metadata remains unpinned.
- Add a literal version-1 context digest vector and exact JSON round-trips for
  CRLF, BOM, NUL, multibyte and astral content.
- Compare an external expected bundle digest at the handoff, recheck current
  context/prompt limits and preserve existing admission/ownership authority.

## Validation and boundaries

Unit tests cover external digest replacement, task identity, constructed-instance
revalidation, byte boundaries, strict/positive reservations and model mismatch.
Integration tests cover exact captured bytes, retry reservation/prompt identity,
pre-capture failures, changed-instruction retries, existing queue/root limits,
current restart-only payload limits, concurrent policy reload rejection and
existing lease/one-way launch-claim behavior. No provider worker is started.

The retained lab runner at `var/bm07-lab-20261022/run.py` creates a new PostgreSQL
18 cluster per unique run label, listens only on an allocated loopback port,
uses random temporary credentials, verifies data-directory identity, and guards
parent-process psycopg connections against any endpoint except its exact scratch
database. Existing multiprocess ownership tests receive only the scratch DSN.
The runner stops its owned cluster and removes credential files in `finally`.
No configured operational DSN is read or reused. This is test-only startup policy
in an owned disposable cluster, not live policy activation.

Earlier lab attempts exposed an initdb timeout, sandbox-denied loopback access,
a test-harness row-factory incompatibility, and an incorrect soft-reload test
setup. Their receipts remain in scratch. Tests were then rerun with the same
owned-cluster restriction outside the sandbox's network restriction. Product
policy/reload behavior was preserved. Final results and change-boundary checks
are recorded below.

### Actual final results, 2026-10-22

**322 passed in 20.05s; zero failed, skipped or setup errors.** The final run
includes all 16 cases previously skipped during BM-06: eight worker-waiting,
six guidance and two policy-pin cases. It also covers the new adapter and
existing admission, ingress, ownership and guidance dispatch gates.

| Goal | Actual evidence |
| --- | --- |
| Reject replacement context and mismatched task identity | Unit/integration checks passed before capture/admission |
| Recheck byte and reservation bounds | Exact whole-prompt boundary, smaller context limit and positive reservation checks passed |
| Preserve existing durable admission | Exact prompt capture, one reservation on retry, queue/root rejection tests passed |
| Fail closed on policy change | Concurrent reload returned `policy_changed`; no job admitted |
| Reuse ownership without launching providers | Existing lease retry and one-way launch flag tests passed in scratch |
| Close BM-06 gaps | Lazy inputs, supplied-byte caps, encoding label, golden digest and UTF-8 transport tests passed |
| Keep live dispatch inactive | Adapter remains unregistered; no production files/configuration/launchers changed |

The receipt at `var/bm07-lab-20261022/final/receipt.json` records 482 guarded
parent-process database connections, the owned cluster system identifier,
source unchanged during the run, successful owned shutdown and credential-file
removal. Multiprocess test connections are not included in that parent guard
counter. No measured provider usage, cost or savings is inferred.

Reproduce the selected suite with a new unique alphanumeric lab label:

```powershell
.venv/Scripts/python.exe -B var/bm07-lab-20261022/run.py NEWLABEL tests/contracts tests/operations tests/queries tests/adapters/test_pinned_dispatch.py tests/adapters/test_ingress.py tests/integration/test_pinned_dispatch.py tests/integration/test_admission.py tests/integration/test_ownership.py tests/integration/test_guidance.py tests/integration/test_policy_pins.py tests/integration/test_gate_dispatch.py
```

The sandbox denied loopback connectivity; successful lab runs used an approved
execution outside that network restriction while retaining the explicit scratch
endpoint/data-directory guard. No operational credentials or DSN were used.

Before/after comparison of 365 original files across contracts, service, bridge,
config, database, tests and docs confirms 362 unchanged, three intended changes
(`service/queries/pinned_context.py`, its contract tests, and the BM-06 note),
four additions (adapter, unit tests, integration tests and this note), and no
removals. Bytecode caches and test scratch under var are excluded. This proves
this task's source change boundary, not earlier history; Git remains unavailable.
The final receipt's unchanged-source assertion covers the test run itself; this
results section was added afterward without further product-code edits.

Rollback is to stop calling/remove the additive adapter and its tests/note.
BM-06 input hardening is a separate change; reverting it reopens the review gaps.
There is no activation or database rollback step.

Execution is direct Codex under the user's explicit instruction. The Bridge
cannot read this checkout or execute repository changes/tests; its recorder is
used only for execution accounting. Git remains unavailable in this workspace;
source hashes establish this task's change boundary, not earlier history.
