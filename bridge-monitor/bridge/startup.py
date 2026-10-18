"""Construct the project-local bridge from active policy and runtime capabilities."""

import hashlib
import os
import re
import sys
import tomllib
import uuid
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from bridge.adapters.claude_cli import ClaudeCLIAdapter, SubprocessRunner
from bridge.adapters.qwen_runtime import QwenAdapter
from bridge.adapters.qwen_agent import QwenAgentAdapter
from bridge.launch import create_child_mcp_config
from bridge.mcp_server import BridgeCoordinator
from bridge.runtime.policy_startup import active_policy
from bridge.runtime.scheduler import cancellation_requested
from bridge.telemetry.emitter import TelemetryEmitter
from bridge.telemetry.spool import DurableSpool
from service.ingest.store import ingest_batch


class ClaudeCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")
    executable: str = Field(min_length=1)
    context_tokens: int = Field(ge=0)
    reported_models: list[str]
    client_timeout_verified: bool


class QwenCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")
    context_tokens: int = Field(ge=0)
    template_overhead_tokens: int = Field(default=128, ge=0)
    byte_upper_bound_validated: bool
    client_timeout_verified: bool


class RuntimeCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cancellation_poll_ms: int = Field(default=250, ge=25, le=5000)
    workspace_max_bytes: int = Field(default=104857600, ge=1)
    sources: dict[str, str] = Field(default_factory=dict)
    claude: ClaudeCapabilities
    qwen: QwenCapabilities


class DirectCollector:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def ingest_batch(self, producer, events, content):
        return ingest_batch(self.dsn, producer, events, content)


def build_coordinator(runtime_config_path: str | Path, *,
                      audience: Literal["parent", "claude"],
                      parent_call_id: str | None = None,
                      parent_lease_id: str | None = None,
                      project_root: str | Path | None = None,
                      python_executable: str | Path | None = None) -> BridgeCoordinator:
    project = (Path(project_root) if project_root else Path(__file__).resolve().parents[1]).resolve(
        strict=True)
    config_path = Path(runtime_config_path).resolve(strict=True)
    if not config_path.is_relative_to(project):
        raise ValueError("runtime configuration must be inside project")
    with config_path.open("rb") as source:
        capabilities = RuntimeCapabilities.model_validate(tomllib.load(source))
    for source_id, source_root in capabilities.sources.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]+", source_id) or not Path(source_root).is_absolute():
            raise ValueError("source bindings require simple IDs and absolute roots")
    dsn = os.environ.get("BRIDGE_MONITOR_DSN")
    if not dsn:
        raise ValueError("BRIDGE_MONITOR_DSN is required")
    if audience == "parent" and not os.environ.get("BRIDGE_MONITOR_SYSTEM_TOKEN"):
        raise ValueError("BRIDGE_MONITOR_SYSTEM_TOKEN is required for verified recovery")
    policy = active_policy(dsn)
    if not policy:
        raise ValueError("reviewed policy must be active before bridge launch")
    secret_name = policy.deployment.postgres_dsn_ref.removeprefix("env:")
    if os.environ.get(secret_name) != dsn:
        raise ValueError("active policy database reference differs from bootstrap DSN")
    executable = Path(python_executable or sys.executable).resolve(strict=True)
    if not executable.is_relative_to(project):
        raise ValueError("bridge Python executable must be inside project")
    if audience == "claude" and (not parent_call_id or not parent_lease_id):
        raise ValueError("Claude child requires a bound parent call and lease")
    key = ("parent" if audience == "parent" else
           "child-" + hashlib.sha256(f"{parent_call_id}:{parent_lease_id}".encode()).hexdigest()[:24])
    spool = DurableSpool(project / "var" / "spool" / f"{key}.sqlite",
                         limit_bytes=policy.deployment.spool_limit_bytes)
    emitter = TelemetryEmitter(spool, DirectCollector(dsn), producer_id=f"bridge-monitor-{key}",
                               incarnation=str(uuid.uuid4()))
    emitter.replay()
    claude_adapter = None
    qwen_adapter = None
    if audience == "parent":
        runner = SubprocessRunner(cwd=project,
                                  timeout_seconds=policy.limits.claude_execution_seconds,
                                  client_tool_timeout_seconds=policy.deployment.client_timeout_seconds,
                                  cancel_poll_seconds=capabilities.cancellation_poll_ms / 1000,
                                  diagnostic_root=project / "var" / "logs" / "claude-cli")
        claude_adapter = ClaudeCLIAdapter(runner,
            executable=capabilities.claude.executable,
            model=policy.fallback.paid_model_profile,
            cancellation_probe=lambda call_id: cancellation_requested(dsn, call_id),
            mcp_config_factory=lambda lease: create_child_mcp_config(
                project, lease, python_executable=executable))
    else:
        deployment = policy.deployment
        common = dict(
            endpoint=policy.deployment.qwen_endpoint,
            model=policy.deployment.qwen_model_profile,
            expected_digest=policy.deployment.qwen_model_digest,
            context_tokens=capabilities.qwen.context_tokens,
            output_tokens=policy.deployment.qwen_output_tokens,
            timeout_seconds=policy.limits.qwen_execution_seconds,
            keep_alive=policy.deployment.qwen_keep_alive,
            temperature=policy.deployment.qwen_temperature,
            cancellation_probe=lambda call_id: cancellation_requested(dsn, call_id))
        if deployment.qwen_agent_endpoint:
            secret_name = deployment.qwen_agent_token_ref.removeprefix("env:")
            token = os.environ.get(secret_name)
            if not token:
                raise ValueError("configured Skynet agent token is unavailable")
            qwen_adapter = QwenAgentAdapter(httpx.Client(),
                **{**common, "endpoint": deployment.qwen_agent_endpoint},
                token=token, first_output_seconds=deployment.qwen_first_output_seconds,
                idle_output_seconds=deployment.qwen_idle_output_seconds,
                ack_seconds=deployment.qwen_ack_seconds,
                poll_seconds=deployment.qwen_poll_seconds,
                poll_timeout_seconds=deployment.qwen_poll_timeout_seconds,
                poll_failures=deployment.qwen_poll_failures)
        else:
            qwen_adapter = QwenAdapter(httpx.Client(), **common,
                cancel_poll_seconds=capabilities.cancellation_poll_ms / 1000)
    return BridgeCoordinator(dsn, policy, policy.version, emitter,
        claude_adapter=claude_adapter, qwen_adapter=qwen_adapter,
        source_roots=capabilities.sources,
        workspace_base=project / "var" / "workspaces",
        workspace_max_bytes=capabilities.workspace_max_bytes,
        parent_call_id=parent_call_id, parent_lease_id=parent_lease_id,
        local_capability=capabilities.qwen.model_dump(),
        paid_capability=capabilities.claude.model_dump())
