"""Capture v1 tool schemas and fresh/resume subprocess behaviour as fixtures.

Read-only with respect to v1: the v1 module is imported from its path (run
with ``python -B`` so no bytecode is written next to it), subprocess.run is
replaced by a recorder, and HOME/USERPROFILE point at a temporary directory so
v1's session file lands there instead of in the real ``~/.gpt-architect-bridge``.
No Codex process is started and no model call is made.

Usage:  python -B tools/capture_v1_fixtures.py [--v1 PATH] [--out DIR]
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

DEFAULT_V1 = Path(r"C:\AI\gpt-claude-mcp-bridge\architect_bridge.py")


def _load_v1(path: Path):
    spec = importlib.util.spec_from_file_location("architect_bridge_v1_capture", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _normalise(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        for needle, token in replacements.items():
            value = value.replace(needle, token)
        return value
    if isinstance(value, list):
        return [_normalise(v, replacements) for v in value]
    if isinstance(value, dict):
        return {k: _normalise(v, replacements) for k, v in value.items()}
    return value


def capture(v1_path: Path, out_dir: Path) -> None:
    work = Path(tempfile.mkdtemp(prefix="v1_capture_"))
    home = work / "home"
    repo = work / "repo"
    (repo / ".git").mkdir(parents=True)
    home.mkdir()
    os.environ["USERPROFILE"] = str(home)
    os.environ["HOME"] = str(home)
    os.environ["CLAUDE_PROJECT_DIR"] = str(repo)
    for name in ("GPT_ARCHITECT_MODEL", "GPT_ARCHITECT_EFFORT", "GPT_ARCHITECT_TIMEOUT"):
        os.environ.pop(name, None)

    v1 = _load_v1(v1_path)

    # ---- tool schemas -----------------------------------------------------
    tools = asyncio.run(v1.mcp.list_tools())
    schemas = [
        t.model_dump(mode="json", exclude_none=True)
        for t in sorted(tools, key=lambda t: t.name)
    ]

    # ---- subprocess behaviour --------------------------------------------
    recorded: list[dict[str, Any]] = []
    thread_id = "11111111-2222-3333-4444-555555555555"

    def fake_run(command, **kwargs):  # noqa: ANN001
        out_index = command.index("--output-last-message") + 1
        Path(command[out_index]).write_text("FIXTURE ANSWER", encoding="utf-8")
        recorded.append(
            {
                "argv": list(command),
                "kwargs": {k: v for k, v in kwargs.items() if k != "input"},
                "stdin_is_prompt": kwargs.get("input") is not None,
                "stdin_prefix_len": len(kwargs.get("input") or ""),
            }
        )
        events = [
            {"type": "thread.started", "thread_id": thread_id},
            {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}},
        ]
        return subprocess.CompletedProcess(
            command, 0, stdout="\n".join(json.dumps(e) for e in events), stderr=""
        )

    v1.subprocess.run = fake_run
    v1.shutil.which = lambda name: r"C:\fake\npm\codex.cmd"

    scenarios: list[dict[str, Any]] = []

    def run_scenario(label: str, fn, *args):  # noqa: ANN001
        before = len(recorded)
        result = fn(*args)
        scenarios.append({"label": label, "result": result, "calls": recorded[before:]})

    run_scenario("consult_fresh", v1.consult_architect, "What is the layering?")
    run_scenario("consult_resume", v1.consult_architect, "And the persistence?")
    run_scenario("review_resume", v1.review_implementation, "persistence semantics")
    session_files = sorted(p.name for p in (home / ".gpt-architect-bridge").glob("*.json"))
    session_record = json.loads(
        next((home / ".gpt-architect-bridge").glob("*.json")).read_text(encoding="utf-8")
    )

    v1.MODEL = "other-model"
    run_scenario("consult_after_model_change_fresh", v1.consult_architect, "q")

    replacements = {str(repo): "<REPO>", str(work): "<TMP>", tempfile.gettempdir(): "<TMP>"}
    behaviour = {
        "note": "Captured from v1 with subprocess.run mocked; no Codex or model call.",
        "v1_constants": {
            "MODEL": "gpt-6.1-sol",
            "EFFORT": v1.EFFORT,
            "MAX_SECONDS": v1.MAX_SECONDS,
            "prompt_limit_chars": 60000,
        },
        "session_file_count": len(session_files),
        "session_record_keys": sorted(session_record),
        "scenarios": _normalise(scenarios, replacements),
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "v1_tool_schemas.json").write_text(
        json.dumps(schemas, indent=2, sort_keys=True), encoding="utf-8"
    )
    (out_dir / "v1_subprocess_behavior.json").write_text(
        json.dumps(behaviour, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(f"wrote {out_dir / 'v1_tool_schemas.json'}", file=sys.stderr)
    print(f"wrote {out_dir / 'v1_subprocess_behavior.json'}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--v1", type=Path, default=DEFAULT_V1)
    parser.add_argument(
        "--out", type=Path, default=Path(__file__).resolve().parent.parent / "tests" / "fixtures"
    )
    ns = parser.parse_args()
    capture(ns.v1, ns.out)
