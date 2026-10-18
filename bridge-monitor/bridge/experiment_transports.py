"""Pinned worker and parent transports used only by offline experiments."""

import json
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from bridge.adapters.qwen_agent import QwenAgentAdapter
from bridge.local_first import ModelReply
from bridge.shadow_accounting import parse_codex_jsonl


PROJECT = Path(__file__).resolve().parents[1]
ISOLATED_ROOT = PROJECT / "var" / "shadow" / "isolated"


class Sink:
    def emit(self, *_args, **_kwargs):
        return True


def sonnet_call(prompt: str, *, model: str, settings) -> ModelReply:
    command = ["claude", "--print", "--output-format", "json", "--model", model,
        "--no-session-persistence", "--restricted", "--strict-mcp-config",
        "--settings", '{"disableAllHooks":true}', "--disable-slash-commands",
        "--permission-mode", "dontAsk", "--permission-prompts", "none", "--tools", "",
        "--disallowedTools", "Bash", "PowerShell", "Edit", "Write", "NotebookEdit",
        "Agent", "Task", "ToolSearch", "WebFetch", "--max-budget-usd",
        str(settings.paid_max_budget_usd)]
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    started = time.monotonic()
    try:
        ISOLATED_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="bridge-paid-", dir=ISOLATED_ROOT) as folder:
            process = subprocess.run(command, input=prompt, text=True, capture_output=True,
                encoding="utf-8", errors="replace", cwd=folder, env=env,
                timeout=settings.paid_timeout_seconds)
        elapsed = round(time.monotonic() - started, 3)
        if process.returncode:
            return ModelReply(state="failed", requested_model=model,
                uncertainty=f"claude_cli_exit_{process.returncode}", elapsed_seconds=elapsed)
        frame = json.loads(process.stdout)
        model_usage = frame.get("modelUsage") or {}
        reported = next(iter(model_usage)) if len(model_usage) == 1 else None
        usage = frame.get("usage") or {}
        counts = {key: usage[key] for key in ("input_tokens", "cache_read_input_tokens",
                  "cache_creation_input_tokens", "output_tokens")
                  if type(usage.get(key)) is int and usage[key] >= 0}
        model_record = model_usage.get(reported, {}) if reported else {}
        list_cost = frame.get("total_cost_usd")
        return ModelReply(state="failed" if frame.get("is_error") else "completed",
            raw=frame.get("result"), requested_model=model, reported_model=reported,
            usage=counts, elapsed_seconds=elapsed,
            list_cost_usd=list_cost if isinstance(list_cost, (int, float)) else None,
            cost_basis=model_record.get("costBasis"))
    except (subprocess.TimeoutExpired, ValueError, OSError) as exc:
        return ModelReply(state="uncertain", requested_model=model,
            uncertainty=type(exc).__name__,
            elapsed_seconds=round(time.monotonic() - started, 3))


def qwen_call(prompt: str, *, adapter: QwenAgentAdapter) -> ModelReply:
    call_id = str(uuid.uuid4())
    lease = SimpleNamespace(call_id=call_id, lease_id=str(uuid.uuid4()),
                            fencing_token=1, policy_version="offline-shadow")
    started = time.monotonic()
    result = adapter.run(lease, prompt, Sink())
    return ModelReply(state=result.state, raw=result.answer,
        requested_model=adapter.model, reported_model=result.reported_model,
        usage=result.usage, uncertainty=result.uncertainty,
        elapsed_seconds=round(time.monotonic() - started, 3))


def make_qwen_adapter(client, *, policy, capabilities: dict,
                      output_tokens: int, max_seconds: float) -> QwenAgentAdapter:
    deployment = policy.deployment
    return QwenAgentAdapter(client, endpoint=deployment.qwen_agent_endpoint,
        token=_agent_token(deployment.qwen_agent_token_ref),
        model=deployment.qwen_model_profile,
        expected_digest=deployment.qwen_model_digest,
        context_tokens=capabilities["qwen"]["context_tokens"],
        output_tokens=min(output_tokens, deployment.qwen_output_tokens),
        timeout_seconds=min(max_seconds, policy.limits.qwen_execution_seconds),
        first_output_seconds=min(deployment.qwen_first_output_seconds, max_seconds / 2),
        idle_output_seconds=min(deployment.qwen_idle_output_seconds, max_seconds / 2),
        ack_seconds=deployment.qwen_ack_seconds,
        poll_seconds=deployment.qwen_poll_seconds,
        poll_timeout_seconds=deployment.qwen_poll_timeout_seconds,
        poll_failures=deployment.qwen_poll_failures,
        keep_alive=deployment.qwen_keep_alive,
        temperature=deployment.qwen_temperature)


def _agent_token(reference: str) -> str:
    token = os.environ.get(reference.removeprefix("env:"))
    if not token:
        path = PROJECT / "var" / "secrets" / "skynet-agent-token"
        token = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    if not token:
        raise ValueError("agent credential unavailable")
    return token


def codex_parent_call(prompt: str, *, model: str, timeout_seconds: float) -> dict:
    started = time.monotonic()
    try:
        ISOLATED_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="bridge-parent-", dir=ISOLATED_ROOT) as folder:
            command = ["codex", "exec", "--json", "--sandbox", "read-only",
                       "--skip-git-repo-check", "-m", model, "-C", folder, "-"]
            process = subprocess.run(command, input=prompt, text=True, capture_output=True,
                encoding="utf-8", errors="replace", cwd=folder, timeout=timeout_seconds)
        elapsed = round(time.monotonic() - started, 3)
        if process.returncode:
            return {"state": "failed", "requested_model": model,
                "reported_model": None, "usage": None, "elapsed_seconds": elapsed,
                "reason": f"codex_cli_exit_{process.returncode}"}
        return parse_codex_jsonl(process.stdout, requested_model=model,
            elapsed_seconds=elapsed)
    except (subprocess.TimeoutExpired, ValueError, OSError) as exc:
        return {"state": "uncertain", "requested_model": model,
            "reported_model": None, "usage": None,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "reason": type(exc).__name__}
