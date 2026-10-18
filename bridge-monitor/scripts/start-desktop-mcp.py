"""Launch the side-by-side parent MCP with project-local operator settings."""

import json
import os
import re
import secrets
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
SETTINGS = PROJECT / "var" / "secrets" / "desktop-mcp.json"
RUNTIME = PROJECT / "config" / "runtime-capabilities.proposal.toml"


def configure_agent_token(settings: dict, policy, project: Path) -> None:
    """Load only the agent credential named by the active policy."""
    if not policy.deployment.qwen_agent_endpoint:
        return
    token_ref = policy.deployment.qwen_agent_token_ref
    name = token_ref.removeprefix("env:")
    if not token_ref.startswith("env:") or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError("agent policy has an invalid environment reference")
    token_file = settings.get("agent_token_file")
    if not isinstance(token_file, str) or not token_file.strip():
        raise ValueError("agent route requires a project secret file")
    secret_root = (project / "var" / "secrets").resolve(strict=True)
    candidate = Path(token_file)
    path = (candidate if candidate.is_absolute() else project / candidate).resolve(strict=True)
    if not path.is_relative_to(secret_root) or not path.is_file():
        raise ValueError("agent token must be a project secret file")
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError("agent token file is empty")
    os.environ[name] = token


def main() -> int:
    try:
        settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
        if (not isinstance(settings, dict) or
                set(settings) not in ({"dsn"}, {"dsn", "agent_token_file"}) or
                not isinstance(settings["dsn"], str)):
            raise ValueError("desktop MCP settings require a DSN and optional agent token file")
        if not settings["dsn"].strip():
            raise ValueError("desktop MCP DSN is empty")
        if not RUNTIME.is_file():
            raise FileNotFoundError(RUNTIME)
        sys.path.insert(0, str(PROJECT))
        from bridge.runtime.policy_startup import active_policy
        policy = active_policy(settings["dsn"])
        if policy is None:
            raise ValueError("reviewed desktop MCP policy is unavailable")
        configure_agent_token(settings, policy, PROJECT)
    except (OSError, ValueError, TypeError) as exc:
        print(f"desktop MCP settings unavailable: {exc}", file=sys.stderr)
        return 2

    os.environ["BRIDGE_MONITOR_DSN"] = settings["dsn"]
    os.environ["BRIDGE_MONITOR_RUNTIME_CONFIG"] = str(RUNTIME)
    # This credential authenticates this process's internal recovery calls.
    # It does not need to persist or appear in the Codex registration.
    os.environ["BRIDGE_MONITOR_SYSTEM_TOKEN"] = secrets.token_urlsafe(48)
    from bridge.mcp_server import main as run_server

    return run_server(["--audience", "parent"])


if __name__ == "__main__":
    raise SystemExit(main())
