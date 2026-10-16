# LOCAL-00 operation in bridge v2

V2 is the primary bridge. V1 is a manual fallback for a broader v2 failure; LOCAL-00 failure never switches to it. The existing `consult_architect` and `review_implementation` tools retain their original contracts. The new `local_worker` tool performs explicitly selected local work and returns drafts for independent supervisor validation.

## Start and reconnect

Restart the relevant v2 MCP server, or start a new client chat that creates a fresh server, to discover the third tool. Source changes do not update already-imported Python processes. Registration commands/names remain unchanged.

Discovery runs in the background, trying `skynet.local:11434` and then the last validated IP (initial candidate `192.168.0.78`). It validates the exact pilot model/digest before saving the responding peer IP. Discovery failure leaves normal v2 architect tools available.

Call the one new tool with one `request` object:

```json
{"request":{"action":"status"}}
```

The result keeps availability, coordination, and validation separate. To reconnect using the normal discovery sequence:

```json
{"request":{"action":"reconnect"}}
```

To enter an address, pass a plain hostname or IP, without scheme, port or path:

```json
{"request":{"action":"reconnect","address":"192.168.0.78"}}
```

The HTTP port remains 11434. A successful entered address updates the saved IP but retains `skynet.local` as the preferred name. IPv6 literals are supported; loopback, wildcard, multicast and scoped/link-local destinations are rejected for this Windows-to-Skynet integration.

```json
{"request":{"action":"continue_without"}}
```

This disables generation for that MCP server session. An explicit successful reconnect can re-enable it. The stdio server never consumes an address from protocol stdin; GPT/Claude presents the returned address/retry/continue choices conversationally. It does not repeatedly attach the prompt to architect answers.

## Choose and submit a task

Only delegate small isolated repairs, explicit mechanical transformations, or explanations of sufficient supplied code. Keep architecture, broad Java/Spring advice, ambiguous requests and missing context with the supervisor. Source and references are supplied data; the adapter never opens packet paths, runs acceptance commands, or applies generated code to a repository.

Code changes require supervisor-owned compiler and executable behaviour checks. Explanations require independent checks against supplied source and authoritative version-specific references. Supply real project versions where applicable. Proprietary/Broadcom source requires explicit authorization. Source approval references record the supervisor's authorization; they do not prove the human conversation to the adapter.

A synthetic example:

```json
{
  "request": {
    "action": "generate",
    "attempt": 1,
    "packet": {
      "task_id": "example-unique-01",
      "category": "isolated_repair",
      "objective": "Repair stable_unique",
      "constraints": "None and empty return []. Preserve first-occurrence order, do not mutate input, always return a fresh list.",
      "source": "def stable_unique(items): return sorted(set(items))",
      "context": "Standalone function, integer-list input, builtins only.",
      "diagnostic": "None raises TypeError and ordering changes.",
      "versions": {"Python": "3.13"},
      "references": [],
      "output_form": "One complete replacement function.",
      "context_id": "synthetic-v1",
      "approval": {
        "classification": "synthetic",
        "approval_ref": "reference to actual supervisor authorization",
        "audit_content_approved": true
      },
      "checks": [
        {"check_id":"compile","kind":"compile","contract":"py_compile succeeds","method":"independent candidate compilation"},
        {"check_id":"behaviour","kind":"behaviour","contract":"None, empty, stable order, unchanged input and fresh output","method":"independent executable assertions"}
      ]
    }
  }
}
```

Use a unique meaningful task ID and retain it across retries/replayed calls. IDs permit 1–64 ASCII letters, digits, dots, underscores and hyphens, starting with a letter/digit. A changed contract/context/approval under an existing ID is a conflict. The supervisor must not disguise repeat attempts as new tasks.

The full rendered input must fit the verified 3072-token upper bound, reserving 1024 output tokens within 4096. Text size limits are additional input bounds. Unknown tokenizer/template/runtime identity, insufficient proof or oversized retry context returns unavailable without inference. See [the budget proof](LOCAL00_BUDGET_PROOF.md). The current reviewed runtime is Ollama 0.30.9; a changed runtime/template requires reviewed proof evidence, not a blind model update.

## Validate a draft

Inspect the response as a candidate. Run the original independent checks in the approved project environment and record the exact packet/result hashes returned by generation:

```json
{
  "request": {
    "action": "record_validation",
    "report": {
      "task_id": "example-unique-01",
      "attempt": 1,
      "packet_hash": "use the 64-character packet_hash from the actual result",
      "result_digest": "use the 64-character result_digest from the actual result",
      "verdict": "accepted",
      "toolchain_ref": "reference to the applicable toolchain and run",
      "observations": [
        {"check_id":"compile","passed":true,"evidence_ref":"actual compilation record","observed":"compilation succeeded"},
        {"check_id":"behaviour","passed":true,"evidence_ref":"actual executable test record","observed":"all original contract cases passed"}
      ]
    }
  }
}
```

Replace the descriptive hash/reference strings with real results before submitting. The adapter labels validation `supervisor_reported`; it checks evidence structure and task identity but never claims to have executed the checks. Compilation alone cannot accept a code change. Missing tools or uncertain correctness are `unverifiable`, and the supervisor continues the task.

For a failed independent check, record verdict `failed`, the failed check observation, and the exact contradiction in `diagnostics`. Attempt 2 reuses the original packet and supplies `feedback_ref` equal to attempt 1's result digest. The adapter retrieves the recorded failure/prior answer and checks the full retry budget. There is no attempt 3, extended conversation, automatic transport retry or retry after acceptance. `NEEDS_CONTEXT:` responses escalate. Validation corrections are append-only; supervisor edits are recorded separately and never become worker success.

## Shared worker coordination and recovery

All cooperating v2 processes under the same Windows account use `%USERPROFILE%\.gpt-architect-bridge-local00`, regardless of repository, endpoint alias or v2 state override. This contains bounded configuration, shared lock, task ledger, active marker and approved audit. Other accounts/hosts and unrelated Ollama clients are outside this lock; exclude them from the pilot. `/api/ps` lists resident models and is not a request mutex. A different resident model returns busy; the bridge never unloads it.

Default discovery deadline is five seconds per candidate; generation deadline is 180 seconds. Configuration permits discovery 1–15 seconds and generation 30–900 seconds in `config.json`. Keep that separate from the architect `settings.json`; its sandbox-override schema is unchanged. No production setting overrides the local model/profile, worker lock root or fixed port.

A timeout, cancellation, process death or uncertain persistence after possible POST keeps an active marker and blocks further local generation. A reconnect or an empty resident-model list does not establish that an earlier server request ended. Continue with normal v2 while obtaining operator confirmation.

Native administration, from the v2 directory:

```powershell
.\.venv\Scripts\python.exe -B bridge_admin.py local-worker status --json
.\.venv\Scripts\python.exe -B bridge_admin.py local-worker reconnect
```

Only after an operator with server-side visibility confirms the exact old request has ended:

```powershell
.\.venv\Scripts\python.exe -B bridge_admin.py local-worker resolve-uncertain --request-id ACTUAL_REQUEST_ID --confirmed-idle --reason "operator confirmed the earlier generation ended" --evidence-ref "actual operator observation reference"
```

The request ID must match the pending request. The command records the acknowledgement and retires the attempt without replaying inference or resetting the task's retry history. The MCP tool cannot clear uncertainty. The adapter has no remote-shell or service-management authority.

## Audit and pilot measurements

Generation requires explicit approval to store the supplied source and full request/retained response locally. Content audit lives under the shared worker root's `audit/<request-id>/`; ordinary architect telemetry retains its content-free contract. Records are retained until explicit operator archival/removal. Required persistence failure prevents a POST or leaves an incomplete/uncertain outcome; logging repair never replays inference.

Record actual preparation, inference, validation, review/correction and total-to-acceptance measurements. Human active time needs a measured value and measurement reference; unknown stays null. Premium token/cost claims need receipts. Compare comparable approved tasks before claiming savings; throughput alone is insufficient.

The explicit live checker defaults to metadata only:

```powershell
.\.venv\Scripts\python.exe -B tools/local_worker_live_check.py --metadata-only
```

A deliberately selected synthetic call additionally requires `--generate-synthetic --source-approved --audit-approved --task-id STABLE_TASK_ID`. It uses the production shared worker lock and returns a draft for supervisor inspection; it never executes model output automatically. Unit tests alone use isolated homes and disposable transports.

## Disable or roll back

Use `continue_without` for a session. For source rollback, restore the recorded v2 source baseline and restart v2, retaining audit/uncertain markers and task history. Keep v1 available as a manually selected fallback for broader v2 failures. No automatic fallback, registration edit, service configuration change or router change is performed.
