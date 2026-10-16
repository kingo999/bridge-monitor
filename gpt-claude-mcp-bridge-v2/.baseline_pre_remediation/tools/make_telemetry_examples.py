"""Produce sanitized example telemetry records using the *fake* Codex CLI.

    .venv\\Scripts\\python.exe tools/make_telemetry_examples.py > docs/telemetry_examples.jsonl

No Codex or model call is made; records come from the scripted test double, so
token numbers are fixture values, not real usage.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge import config, errors  # noqa: E402
from bridge.service import ArchitectService  # noqa: E402
from tests.helpers import World  # noqa: E402


def main() -> None:
    world = World()
    try:
        svc = ArchitectService(world.env())
        svc.execute(config.CONSULT, "fixture question")            # fresh, success
        svc.execute(config.REVIEW, "fixture focus")                # resumed, success
        world.set(exec={"usage": None})
        svc.execute(config.CONSULT, "no usage reported")           # success, usage all null
        world.set(exec={"exit": 1, "events": [
            {"type": "thread.started", "thread_id": "{thread}"},
            {"type": "turn.failed", "error": {"message": "You've hit your usage limit."}}]})
        try:
            svc.execute(config.CONSULT, "rate limited")
        except errors.BridgeError:
            pass
        world.set(auth="api_key", exec={})
        try:
            svc.execute(config.CONSULT, "api key auth")
        except errors.BridgeError:
            pass
        world.set(auth="chatgpt")
        try:
            svc.execute(config.CONSULT, "   ")
        except errors.BridgeError:
            pass
        sys.stdout.write(world.telemetry_text())
    finally:
        world.cleanup()


if __name__ == "__main__":
    main()
