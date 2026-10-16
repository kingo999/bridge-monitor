"""Produce sanitized example telemetry records using the *fake* Codex CLI.

    .venv\\Scripts\\python.exe tools/make_telemetry_examples.py > docs/telemetry_examples_remediation.jsonl

No Codex or model call is made; records come from the scripted test double, so numbers and
thread ids are fixture values, not real usage.  The set deliberately covers the degraded,
partial and uncertain outcomes introduced by the remediation (F1/F3/F4/F5/F6).
"""

from __future__ import annotations

from pathlib import Path
import sys
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge import config, errors, jobobject, state  # noqa: E402
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
        recs = world.telemetry()[-count:]
        import json

        for r in recs:
            lines.append(json.dumps({"_example": label, **r}, sort_keys=True, separators=(",", ":")))

    # 1. F1 - actual turn hit infrastructure failure: failure (default)
    w = World()
    try:
        w.set(exec={"events": _events({"type": "item.completed", "item": {
            "type": "command_execution", "status": "failed", "aggregated_output": HELPER}})})
        _attempt(ArchitectService(w.env()))
        snapshot(w, "F1 turn_sandbox_setup_failed (answer withheld, pointer not bound)")
    finally:
        w.cleanup()

    # 2. F1 - explicit degraded mode
    w = World()
    try:
        w.set(exec={"events": _events({"type": "item.completed", "item": {
            "type": "command_execution", "status": "failed", "aggregated_output": HELPER}})})
        ArchitectService(w.env(GPT_ARCHITECT_ON_TURN_INFRA_FAILURE="degraded")).execute(config.CONSULT, "q")
        snapshot(w, "F1 degraded (explicit opt-in; answer returned with banner)")
    finally:
        w.cleanup()

    # 3. F5 - pointer save failed: partial success + pending marker
    w = World()
    try:
        def failing(self, *a, **k):
            raise errors.BridgeError(errors.STATE_ERROR, "disk full", code="registry_unwritable")

        with mock.patch.object(state.Registry, "bind_thread", failing):
            ArchitectService(w.env()).execute(config.CONSULT, "q")
        snapshot(w, "F5 success_partial_persistence (pending marker written)")
        _attempt(ArchitectService(w.env()))
        snapshot(w, "F5 refused: pending_persistence blocks a silent replacement thread")
    finally:
        w.cleanup()

    # 4. F3 - termination attempted but unconfirmed, then the session is blocked
    w = World()
    try:
        w.set(exec={"sleep": 60})
        with mock.patch.object(jobobject.ProcessOwnership, "active_processes", return_value=1), \
                mock.patch.object(jobobject.ProcessOwnership, "terminate", lambda self: None):
            _attempt(ArchitectService(w.env(GPT_ARCHITECT_TIMEOUT="2")))
        snapshot(w, "F3 timeout with termination ATTEMPTED but NOT CONFIRMED (session in doubt)")
        w.set(exec={})
        _attempt(ArchitectService(w.env()))
        snapshot(w, "F3 refused: uncertain_termination blocks reuse until reconcile")
    finally:
        w.cleanup()

    # 5. F3 - confirmed termination through the job
    w = World()
    try:
        w.set(exec={"sleep": 60})
        _attempt(ArchitectService(w.env(GPT_ARCHITECT_TIMEOUT="2")))
        snapshot(w, "F3 timeout with termination CONFIRMED by job accounting")
    finally:
        w.cleanup()

    # 6. F6 - repository changed during the review
    w = World()
    try:
        w.set(exec={"mutate": {"path": "touched_while_reviewing.txt", "content": "x"}})
        ArchitectService(w.env()).execute(config.REVIEW, "focus")
        snapshot(w, "F6 repo_changed_during_call (digests only; no paths/content)")
    finally:
        w.cleanup()

    # 7. F4 - registry lost on an established state directory
    w = World()
    try:
        svc = ArchitectService(w.env())
        svc.execute(config.CONSULT, "q")
        (w.state / "registry.json").unlink()
        _attempt(svc)
        snapshot(w, "F4 registry_lost refused before any CLI use / inference")
    finally:
        w.cleanup()

    # 8. F2 - capability schema incompatible: fail closed before inference
    w = World()
    try:
        w.set(features_raw="hooks stable true\nNOT THE TESTED SCHEMA")
        _attempt(ArchitectService(w.env()))
        snapshot(w, "F2 features_schema_incompatible (fail closed)")
    finally:
        w.cleanup()

    sys.stdout.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
