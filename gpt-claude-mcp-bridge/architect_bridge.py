"""Local, read-only Claude Code MCP -> ChatGPT-authenticated Codex bridge.

Requires:
    pip install mcp
    codex login     # select ChatGPT, NOT an API key

The bridge does not access an OpenAI API key. Codex usage is charged against
whatever authentication is active in the Codex CLI.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("GPT Architect via Codex")
_call_lock = threading.Lock()  # one Codex turn at a time in this MCP process

MODEL = os.environ.get("GPT_ARCHITECT_MODEL", "gpt-6.1-sol")
EFFORT = os.environ.get("GPT_ARCHITECT_EFFORT", "high")
MAX_SECONDS = int(os.environ.get("GPT_ARCHITECT_TIMEOUT", "900"))

BASE_INSTRUCTIONS = """You are the GPT ARCHITECT reviewing a local software project.
Your responsibility is architecture, contracts, consistency and critical review.
Claude Code is the implementer. You are read-only: NEVER write, delete or edit
repository files, and do not execute commands that change the repo or system.
Read source and architecture documents only when relevant; prioritize AGENTS.md,
architecture/*.md and known implementation contracts when they exist.
Be precise: cite relevant repository paths/symbols; distinguish verified facts
from design recommendations and unknowns. Do not invent code or test results.
For any change to an agreed design, present the proposal and ask for the human
architect's approval; do not treat Claude's request as that approval.
For deterministic systems: preserve deterministic data interpretation and rules.
Keep answers focused on the actual implementation question.
"""


def _project_root() -> Path:
    raw = os.environ.get("CLAUDE_PROJECT_DIR")
    root = Path(raw if raw else os.getcwd()).resolve()
    if not root.is_dir():
        raise RuntimeError(f"Project directory does not exist: {root}")
    if not (root / ".git").exists():
        raise RuntimeError(
            f"Expected a Git repository at {root}. "
            "Launch Claude from the project root (or configure CLAUDE_PROJECT_DIR)."
        )
    return root


def _session_file(root: Path) -> Path:
    key = hashlib.sha256(os.path.normcase(str(root)).encode("utf-8")).hexdigest()[:24]
    folder = Path.home() / ".gpt-architect-bridge"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{key}.json"


def _load_session(path: Path) -> str | None:
    if not path.exists():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    return record.get("thread_id") if record.get("model") == MODEL else None


def _save_session(path: Path, thread_id: str) -> None:
    path.write_text(
        json.dumps({"thread_id": thread_id, "model": MODEL}, indent=2),
        encoding="utf-8",
    )


def _run_codex(prompt: str) -> str:
    if not prompt.strip():
        raise ValueError("A non-empty question is required")
    if len(prompt) > 60_000:
        raise ValueError("Question too long; pass references to repo files instead")

    with _call_lock:
        root = _project_root()
        session_path = _session_file(root)
        existing_id = _load_session(session_path)
        codex = shutil.which("codex") or shutil.which("codex.cmd")
        if not codex:
            raise RuntimeError("Codex CLI not in PATH. Install @openai/codex and sign in with ChatGPT.")

        with tempfile.TemporaryDirectory(prefix="gpt_architect_") as tmp:
            last_message = str(Path(tmp) / "answer.txt")
            if existing_id:
                # The resume subcommand doesn't reliably inherit --sandbox.
                # Force the sandbox mode via config on EVERY resumed call.
                command = [
                    codex, "exec", "resume", existing_id,
                    "--json", "-c", 'sandbox_mode="read-only"',
                    "-c", f'model_reasoning_effort="{EFFORT}"',
                    "--output-last-message", last_message, "-",
                ]
            else:
                command = [
                    codex, "exec", "--json", "--sandbox", "read-only",
                    "--model", MODEL,
                    "-c", f'model_reasoning_effort="{EFFORT}"',
                    "--output-last-message", last_message, "-",
                ]

            try:
                result = subprocess.run(
                    command,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=str(root),
                    timeout=MAX_SECONDS,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"Codex timed out after {MAX_SECONDS}s") from exc

            thread_id = None
            turn_completed = False
            error_messages: list[str] = []
            for line in result.stdout.splitlines():
                try:
                    event: dict[str, Any] = json.loads(line)
                except json.JSONDecodeError:
                    continue
                typ = event.get("type")
                if typ == "thread.started":
                    thread_id = event.get("thread_id")
                elif typ == "turn.completed":
                    turn_completed = True
                elif typ in ("turn.failed", "error"):
                    error_messages.append(str(event.get("error", event.get("message", typ))))

            if result.returncode != 0 or not turn_completed or error_messages:
                detail = "; ".join(error_messages) or result.stderr[-1400:]
                raise RuntimeError(f"Codex failed (exit {result.returncode}): {detail}")
            if not thread_id:
                raise RuntimeError("Codex did not emit a thread ID; session was not saved")
            if existing_id and thread_id != existing_id:
                # Important: some CLI versions silently start a new session
                # if an old session ID is missing. Do not conceal that.
                raise RuntimeError(
                    "Codex unexpectedly started a different session. "
                    "Check your Codex history and reset the bridge's local session file."
                )

            answer_file = Path(last_message)
            if not answer_file.exists():
                raise RuntimeError("Codex completed but returned no final-answer file")
            answer = answer_file.read_text(encoding="utf-8").strip()
            if not answer:
                raise RuntimeError("Codex returned an empty answer")
            _save_session(session_path, thread_id)
            return answer


@mcp.tool()
def consult_architect(question: str) -> str:
    """Ask the GPT architecture agent about repository design, contracts, and trade-offs.
    GPT reads this repository in a read-only sandbox; it cannot approve changes
    to an agreed design on the human architect's behalf.
    """
    return _run_codex(BASE_INSTRUCTIONS + "\nArchitecture consultation:\n" + question)


@mcp.tool()
def review_implementation(focus: str) -> str:
    """Ask GPT to review the current Git changes for architectural regressions.
    GPT should check actual diffs/source and return findings with paths and
    suggested tests, without modifying files.
    """
    return _run_codex(
        BASE_INSTRUCTIONS
        + "\nReview the currently staged and unstaged Git diffs, and relevant source. "
          "If no changes exist, say so. Prioritize correctness, regression risk, "
          "architecture contract violations, security and missing negative tests. "
          "Do not apply fixes.\nSpecific focus:\n"
        + focus
    )


if __name__ == "__main__":
    # stdio is owned by the MCP protocol: never print debug output to stdout.
    mcp.run(transport="stdio")
