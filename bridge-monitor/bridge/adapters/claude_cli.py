"""Restricted Claude CLI stream adapter for a bridge-owned execution lease."""

import json
import os
import subprocess
import threading
import uuid
from pathlib import Path

from contracts.events import EventType
from bridge.adapters.base import AdapterResult, ensure_lease


LOCAL_TOOL = "mcp__bridge_monitor__delegate_local_analysis"


def build_claude_command(executable: str, model: str, mcp_config_path: str | Path,
                         *, system_prompt: str | None = None) -> list[str]:
    if not executable or not model:
        raise ValueError("Claude executable and model are required")
    command = [executable, "--print", "--verbose", "--output-format", "stream-json",
            "--include-partial-messages", "--model", model,
            "--restricted", "--strict-mcp-config", "--mcp-config", str(mcp_config_path),
            "--settings", '{"disableAllHooks":true}', "--disable-slash-commands",
            "--permission-mode", "dontAsk", "--permission-prompts", "none",
            "--tools", "", "--allowedTools", LOCAL_TOOL,
            "--disallowedTools", "Bash", "PowerShell", "Edit", "Write",
            "NotebookEdit", "Agent", "Task", "ToolSearch", "WebFetch"]
    if system_prompt is not None:
        command.extend(["--system-prompt", system_prompt])
    return command


def worker_system_prompt(task) -> str:
    scope = json.dumps({"category": task.category, "scope": task.scope,
                        "context": task.context}, ensure_ascii=False, sort_keys=True)
    return ("You are the Claude worker for a scoped bridge task. Task metadata: " + scope + "\n"
        "The only delegated operation is mcp__bridge_monitor__delegate_local_analysis. "
        "Use it for a simple bounded check when useful. If it returns "
        "paid_continuation_required, continue the work yourself without asking for approval. "
        "Return exactly one JSON object with keys answer (string), local_results (array), "
        "fallback_child_call_id (string or null), and work_resolution "
        "(resolved, unresolved, or unknown). For every Qwen result actually delivered to you, "
        "include one local_results item containing result_id, input_sha256, judgment "
        "(accepted, revised, replaced, or unused), and reason. Add repair_effort only if known. "
        "Report what you actually used; do not invent a disposition or result ID. "
        "Use an empty local_results array when no local result was delivered. "
        "Put the useful response to the caller in answer. Do not add Markdown fences.")


class SubprocessRunner:
    def __init__(self, *, cwd: str | Path, timeout_seconds: int,
                 client_tool_timeout_seconds: int,
                 cancel_poll_seconds: float = 0.25,
                 diagnostic_root: str | Path | None = None):
        self.cwd = Path(cwd)
        self.timeout_seconds = timeout_seconds
        self.client_tool_timeout_seconds = client_tool_timeout_seconds
        self.cancel_poll_seconds = cancel_poll_seconds
        self.diagnostic_root = Path(diagnostic_root) if diagnostic_root else self.cwd / "var" / "logs" / "claude-cli"
        self.returncode = None
        self.timed_out = False
        self.last_stderr_path = None
        self.cancelled = False
        self.terminated = False

    def fork(self, *, cwd: str | Path | None = None):
        """Give every concurrent call its own process state and receipt."""
        return SubprocessRunner(cwd=cwd or self.cwd, timeout_seconds=self.timeout_seconds,
            client_tool_timeout_seconds=self.client_tool_timeout_seconds,
            cancel_poll_seconds=self.cancel_poll_seconds,
            diagnostic_root=self.diagnostic_root)

    def stream(self, command: list[str], prompt: str, *, cancel_requested=None):
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)
        env["MCP_TOOL_TIMEOUT"] = str(self.client_tool_timeout_seconds * 1000)
        env["CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS"] = "0"
        diagnostic = self.diagnostic_root / f"{uuid.uuid4()}.stderr.txt"
        diagnostic.parent.mkdir(parents=True, exist_ok=True)
        self.last_stderr_path = diagnostic
        with diagnostic.open("wb") as stderr_sink:
            process = subprocess.Popen(command, cwd=self.cwd, env=env, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=stderr_sink,
                                       text=True, encoding="utf-8", errors="replace")
            timer = threading.Timer(self.timeout_seconds, self._expire, args=(process,))
            timer.start()
            stop_monitor = threading.Event()
            def monitor_cancel():
                while not stop_monitor.wait(self.cancel_poll_seconds):
                    try:
                        requested = cancel_requested()
                    except Exception:
                        continue
                    if requested and process.poll() is None:
                        self.cancelled = True
                        process.kill()
                        return
            watcher = None
            if cancel_requested is not None:
                watcher = threading.Thread(target=monitor_cancel, daemon=True)
                watcher.start()
            try:
                process.stdin.write(prompt)
                process.stdin.close()
                for line in process.stdout:
                    yield line
                self.returncode = process.wait()
                self.terminated = True
            finally:
                timer.cancel()
                stop_monitor.set()
                if watcher is not None:
                    watcher.join(timeout=1)
                if process.poll() is None:
                    process.kill()
                    self.returncode = process.wait()
                self.terminated = process.poll() is not None

    def _expire(self, process):
        if process.poll() is None:
            self.timed_out = True
            process.kill()


class ClaudeCLIAdapter:
    def __init__(self, runner, *, executable: str, model: str,
                 mcp_config_path: str | Path | None = None,
                 mcp_config_factory=None, allowed: bool = True,
                 cancellation_probe=None):
        if (mcp_config_path is None) == (mcp_config_factory is None):
            raise ValueError("exactly one child MCP configuration source is required")
        self.runner = runner
        self.executable = executable
        self.mcp_config_path = mcp_config_path
        self.mcp_config_factory = mcp_config_factory
        self.model = model
        self.allowed = allowed
        self.cancellation_probe = cancellation_probe

    def run(self, lease, task, prompt: str, emitter, *, workspace=None) -> AdapterResult:
        ensure_lease(lease)
        if not self.allowed:
            emitter.emit(EventType.ADMISSION_BLOCKED, call_id=lease.call_id,
                         payload={"reason": "claude_capability_denied"})
            return AdapterResult(state="denied")
        common = {"call_id": lease.call_id, "task_id": task.task_id,
                  "work_item_id": task.work_item_id, "config_version": lease.policy_version,
                  "attempt_id": f"{lease.call_id}:attempt:1"}
        model_execution_id = f"{lease.call_id}:model:1"
        system_prompt = worker_system_prompt(task)
        emitter.emit(EventType.MODEL_SELECTED, requested_model=self.model,
                     model_execution_id=model_execution_id,
                     payload={"lease_id": lease.lease_id, "fencing_token": lease.fencing_token}, **common)
        emitter.emit(EventType.MESSAGE_SENT, body=system_prompt.encode("utf-8"), role="system",
                     direction="out", requested_model=self.model, **common)
        emitter.emit(EventType.MESSAGE_SENT, body=prompt.encode("utf-8"), role="user",
                     requested_model=self.model, **common)
        reported = None
        model_reported = False
        final = None
        message_id = str(uuid.uuid4())
        chunk_number = 0
        tool_by_index = {}
        runner = self.runner.fork(cwd=workspace) if isinstance(self.runner, SubprocessRunner) else self.runner
        try:
            config_path = (self.mcp_config_factory(lease) if self.mcp_config_factory
                           else self.mcp_config_path)
            command = build_claude_command(self.executable, self.model, config_path,
                                            system_prompt=system_prompt)
            if isinstance(runner, SubprocessRunner) and self.cancellation_probe is not None:
                lines = runner.stream(command, prompt,
                    cancel_requested=lambda: self.cancellation_probe(lease.call_id))
            else:
                lines = runner.stream(command, prompt)
            for line in lines:
                if not line.strip():
                    continue
                frame = json.loads(line)
                if not isinstance(frame, dict):
                    raise ValueError("invalid Claude stream frame")
                frame_model = None
                if frame.get("type") in ("system", "result") and isinstance(frame.get("model"), str):
                    frame_model = frame["model"]
                if frame.get("type") == "assistant":
                    message = frame.get("message") or {}
                    if isinstance(message.get("model"), str):
                        frame_model = message["model"]
                if frame_model:
                    if reported and frame_model != reported:
                        raise ValueError("Claude reported a different model within one execution")
                    reported = frame_model
                    if not model_reported:
                        emitter.emit(EventType.MODEL_REPORTED, requested_model=self.model,
                                     reported_model=reported, model_execution_id=model_execution_id,
                                     payload={"evidence": "claude_cli_stream"}, **common)
                        model_reported = True
                if frame.get("type") == "stream_event":
                    event = frame.get("event") or {}
                    event_type = event.get("type")
                    if event_type == "content_block_delta":
                        delta = event.get("delta") or {}
                        if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                            chunk_number += 1
                            emitter.emit(EventType.RESPONSE_DELTA, body=delta["text"].encode("utf-8"),
                                         role="assistant", direction="in", requested_model=self.model,
                                         reported_model=reported, message_id=message_id,
                                         chunk_sequence=chunk_number, **common)
                    elif event_type == "content_block_start":
                        block = event.get("content_block") or {}
                        if block.get("type") == "tool_use":
                            name = block.get("name")
                            if name != LOCAL_TOOL:
                                raise ValueError("Claude emitted a tool outside its granted capability")
                            tool_by_index[event.get("index")] = (block.get("id"), name)
                            emitter.emit(EventType.TOOL_STARTED, payload={"tool_id": block.get("id"),
                                          "tool_name": name}, **common)
                    elif event_type == "content_block_stop":
                        tool = tool_by_index.pop(event.get("index"), None)
                        if tool:
                            emitter.emit(EventType.TOOL_COMPLETED,
                                         payload={"tool_id": tool[0], "tool_name": tool[1]}, **common)
                if frame.get("type") == "result":
                    final = frame
        except Exception as exc:
            if "lines" in locals() and hasattr(lines, "close"):
                lines.close()
            emitter.emit(EventType.ATTEMPT_UNCERTAIN,
                         payload={"reason": "claude_stream_lost", "exception_type": type(exc).__name__},
                         **common)
            return AdapterResult(state="uncertain", reported_model=reported,
                                 uncertainty="execution_may_have_run",
                                 terminated=getattr(runner, "terminated", False),
                                 cancelled=getattr(runner, "cancelled", False))
        if (getattr(runner, "cancelled", False) or getattr(runner, "timed_out", False)
                or getattr(runner, "returncode", 0)):
            emitter.emit(EventType.ATTEMPT_UNCERTAIN,
                         payload={"reason": "claude_exit_unconfirmed", "returncode": runner.returncode},
                         **common)
            return AdapterResult(state="uncertain", reported_model=reported,
                                 uncertainty="execution_cancelled" if getattr(runner, "cancelled", False)
                                             else "execution_outcome_unconfirmed",
                                 terminated=getattr(runner, "terminated", False),
                                 cancelled=getattr(runner, "cancelled", False))
        if not final or final.get("is_error") or not isinstance(final.get("result"), str):
            emitter.emit(EventType.CALL_FAILED, payload={"reason": "no_successful_result"}, **common)
            return AdapterResult(state="failed", reported_model=reported,
                                 terminated=getattr(runner, "terminated", False))
        raw_answer = final["result"]
        answer = raw_answer
        completion_claim = None
        try:
            parsed = json.loads(raw_answer)
            if (isinstance(parsed, dict) and isinstance(parsed.get("answer"), str)
                    and isinstance(parsed.get("local_results", []), list)):
                answer = parsed["answer"]
                completion_claim = parsed
        except (ValueError, TypeError):
            pass
        usage = final.get("usage") if isinstance(final.get("usage"), dict) else {}
        usage = {name: usage[name] for name in ("input_tokens", "output_tokens")
                 if type(usage.get(name)) is int and usage[name] >= 0}
        if usage:
            emitter.emit(EventType.USAGE_REPORTED, model_execution_id=model_execution_id,
                         payload={"observation_id": str(uuid.uuid4()), "scope": "execution",
                                  "is_cumulative": True, "provenance": "claude_cli_result", **usage},
                         **common)
        raw_content_id = str(uuid.uuid4())
        emitter.emit(EventType.RESPONSE_COMPLETED, body=raw_answer.encode("utf-8"),
                     content_id=raw_content_id,
                     requested_model=self.model, reported_model=reported, **common)
        return AdapterResult(state="completed", answer=answer, reported_model=reported,
                             usage=usage, raw_answer=raw_answer,
                             raw_content_id=raw_content_id,
                             completion_claim=completion_claim)
