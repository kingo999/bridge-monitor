# LOCAL-00 independent review and disposition

Reviewer: fresh-context gpt-6-astra, read-only, no inference. Review occurred against pre-review source; this is not a claim of a second review after fixes. Four Important findings, one Minor, no Critical.

1. Queued reconnect could undo a newer continue-without choice. Reproduced with a gated startup and queued action. Fixed by capturing revision at action dispatch and distinguishing automatic startup from explicit reconnect. Tests cover queued reconnect and continue-before-startup.
2. Generation could use an obsolete selected endpoint. Reproduced old-host POST after shared snapshot selected new-host. Fixed by validating endpoint and selected connection revision under the worker lock before network preparation. Same-host revision changes also refuse.
3. A JSON array/null/scalar witness could escape startup with AttributeError. Fixed strict witness object/schema checks convert corruption to WorkerError; real stdio tests retain initialization, ping and ordinary architect calls for malformed worker state.
4. Replaying an older validation report could reverse a later correction. Fixed by treating duplicates as reads of the current effective verdict, with verdict/history saved atomically. Regression covers accepted -> failed -> replay accepted, ledger reload and generation replay.

All four findings had reproducing RED tests before fixes and GREEN results afterward. Related regression run: 31 tests passed (review-fixes-regression.log). The final complete staged suite is recorded separately.

## Deferred minor

Completed attempt-2 replay ignores a changed or missing feedback_ref, returning the existing recorded result without another POST. A future refinement should persist and compare the relevant attempt request identity. This does not reset the retry budget or generate another answer.

## Review scope rulings

External inference clients and changes after the final compatibility check remain outside the cooperating-host lock guarantee; retain the approved pilot exclusion policy. Supervisor/operator evidence remains an assertion with provenance rather than authenticated human approval. The real model clarification is unverifiable, with a supervisor correction kept separate; no worker-success or savings claim follows. Final full-suite/activation evidence is owned by the executing agent and distinguishes a fresh server from already-running clients.
