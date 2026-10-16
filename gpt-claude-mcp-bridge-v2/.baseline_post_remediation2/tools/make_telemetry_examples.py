"""Produce sanitized example telemetry records using the *fake* Codex CLI.

    .venv\\Scripts\\python.exe tools/make_telemetry_examples.py > docs/telemetry_examples_remediation.jsonl

No Codex or model call is made; records come from the scripted test double, so numbers and
thread ids are fixture values, not real usage.  The set covers the partial, uncertain and
refused outcomes of both remediation passes (F1/F3/F4/F5/F6, N3/N6/N7/N8/N14/N15).
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge import config, errors, fingerprint, jobobject, state  # noqa: E402
from bridge.service import ArchitectService  # noqa: E402
from tests.helpers import World  # noqa: E402

HELPER = "Failed to create unified exec process: helper_unknown_error: setup refresh had errors"


def _events(*extra):
    return [{"type": "thread.started", "thread_id": "{thread}"}, *extra, {"type": "turn.completed"}]


def _attempt(svc, tool=config.CONSULT, text="fixture question"):
    try:
        svc.execute(tool, text)
    except errors.BridgeError:
        pass


def main() -> None:
    lines: list[str] = []

    def snapshot(world: World, label: str, count: int = 1) -> None:
        for r in world.telemetry()[-count:]:
            lines.append(json.dumps({"_example": label, **r}, sort_keys=True, separators=(",", ":")))

    def scenario(label: str, setup, act, count: int = 1) -> None:
        w = World()
        try:
            setup(w)
            act(w)
            snapshot(w, label, count)
        finally:
            w.cleanup()

    # 1. F1 - a failed command item reports the sandbox failure: answer withheld
    scenario("F1 turn_sandbox_setup_failed (answer withheld, pointer not bound)",
             lambda w: w.set(exec={"events": _events({"type": "item.completed", "item": {
                 "type": "command_execution", "status": "failed", "aggregated_output": HELPER}})}),
             lambda w: _attempt(ArchitectService(w.env())))

    # 2. N7 - the same failure reported as a pre-completion error event
    scenario("N7 pre-completion error event with an infrastructure signature (answer withheld)",
             lambda w: w.set(exec={"events": _events({"type": "error", "message": HELPER})}),
             lambda w: _attempt(ArchitectService(w.env())))

    # 3. P3 - the removed degraded mode is a configuration error, before inference
    def p3(w):
        try:
            ArchitectService(w.env(GPT_ARCHITECT_ON_TURN_INFRA_FAILURE="degraded")).execute(config.CONSULT, "q")
        except errors.BridgeError:
            pass
    scenario("P3 degraded mode removed: invalid_configuration, no turn started", lambda w: None, p3)

    # 4. F5/N6 - pointer save failed: partial success + pending marker, then refusal
    def partial(w):
        def failing(self, *a, **k):
            raise errors.BridgeError(errors.STATE_ERROR, "disk full", code="registry_unwritable")

        with mock.patch.object(state.Registry, "bind_thread", failing):
            ArchitectService(w.env()).execute(config.CONSULT, "q")
        snapshot(w, "F5 success_partial_persistence (pending marker written)")
        _attempt(ArchitectService(w.env()))
    scenario("N6 refused: pending_persistence blocks a silent replacement thread", lambda w: None, partial)

    # 5. N3 - ownership cannot be established: refused before the child runs
    def no_ownership(w):
        with mock.patch.object(jobobject.ProcessOwnership, "attach", lambda self, proc: False):
            _attempt(ArchitectService(w.env()))
    scenario("N3 ownership_unavailable (child killed before any prompt byte; no inference)",
             lambda w: w.set(exec={"sleep": 60}), no_ownership)

    # 6. F3 - termination attempted but unconfirmed, then the session is blocked
    def unconfirmed(w):
        with mock.patch.object(jobobject.ProcessOwnership, "active_processes", return_value=1), \
                mock.patch.object(jobobject.ProcessOwnership, "terminate", lambda self: None):
            _attempt(ArchitectService(w.env(GPT_ARCHITECT_TIMEOUT="2")))
        snapshot(w, "F3 timeout with termination ATTEMPTED but NOT CONFIRMED (session in doubt)")
        w.set(exec={})
        _attempt(ArchitectService(w.env()))
    scenario("F3 refused: uncertain_termination blocks reuse until reconcile",
             lambda w: w.set(exec={"sleep": 60}), unconfirmed)

    # 7. P2 - job members confirmed gone, sandbox-service children unobservable
    scenario("P2 timeout: job members CONFIRMED terminated, service children unobservable (session in doubt)",
             lambda w: w.set(exec={"sleep": 60}),
             lambda w: _attempt(ArchitectService(w.env(GPT_ARCHITECT_TIMEOUT="2"))))

    # 8. F6 - repository changed during the review
    scenario("F6 repo_changed_during_call (digests only; no paths/content)",
             lambda w: w.set(exec={"mutate": {"path": "touched_while_reviewing.txt", "content": "x"}}),
             lambda w: ArchitectService(w.env()).execute(config.REVIEW, "focus"))

    # 9. N15 - consistency check unavailable is reported, not silent
    def unavailable(w):
        with mock.patch.object(fingerprint, "repo_fingerprint",
                               return_value={"ok": False, "head": None, "digest": None, "reason": "timeout"}):
            ArchitectService(w.env()).execute(config.CONSULT, "q")
    scenario("N15 repo_fingerprint_unavailable (bannered in the answer and recorded)", lambda w: None, unavailable)

    # 10. F4/N10 - registry lost on an established state directory
    def lost(w):
        svc = ArchitectService(w.env())
        svc.execute(config.CONSULT, "q")
        (w.state / "registry.json").unlink()
        _attempt(svc)
    scenario("F4 registry_lost refused before any CLI use / inference", lambda w: None, lost)

    # 11. F2 - capability schema incompatible: fail closed before inference
    scenario("F2 features_schema_incompatible (fail closed)",
             lambda w: w.set(features_raw="hooks stable true\nNOT THE TESTED SCHEMA"),
             lambda w: _attempt(ArchitectService(w.env())))

    # 12. N8 - an MCP server appeared between the audit and exec
    quiet = [{"name": "idea", "source": "config"}]
    scenario("N8 mcp_changed_before_exec (configuration drift refused before inference)",
             lambda w: w.set(mcp_sequence=[quiet, quiet, quiet + [{"name": "appeared", "source": "config"}]]),
             lambda w: _attempt(ArchitectService(w.env())))

    # 13. N14 - malformed thread id is dropped and never echoed or persisted
    scenario("N14 bad_thread_id (malformed identifier dropped at ingress)",
             lambda w: w.set(exec={"events": [{"type": "thread.started", "thread_id": "C:/private/path/x.py"},
                                              {"type": "turn.completed"}]}),
             lambda w: _attempt(ArchitectService(w.env())))

    sys.stdout.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
