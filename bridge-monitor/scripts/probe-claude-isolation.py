"""Bounded Sonnet call to inspect actual child tools with a harmless MCP server.

This probe does not activate a policy or contact Skynet. It writes its stream to
the project-local var/probes folder and never alters existing Claude settings.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge.adapters.claude_cli import LOCAL_TOOL, build_claude_command


PROJECT = Path(__file__).resolve().parents[1]


def main() -> int:
    python = (PROJECT / ".venv" / "Scripts" / "python.exe").resolve(strict=True)
    config = PROJECT / "var" / "mcp" / "isolation-probe.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"mcpServers": {"bridge_monitor": {
        "command": str(python), "args": [str(PROJECT / "tests" / "pilot" /
            "isolation_probe_mcp.py")]}}}, sort_keys=True), encoding="utf-8")
    command = build_claude_command("claude", "sonnet", config,
        system_prompt="Use only delegate_local_analysis with prompt 'probe'. "
                      "Then answer with the returned probe value.")
    command.extend(["--verbose", "--max-budget-usd", "0.25"])
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    env["MCP_TOOL_TIMEOUT"] = "30000"
    env["CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS"] = "0"
    result = subprocess.run(command, cwd=PROJECT, env=env, input="Run the isolation probe.",
                            text=True, capture_output=True, timeout=120, encoding="utf-8",
                            errors="replace")
    output = PROJECT / "var" / "probes" / "sonnet-isolation.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(result.stdout, encoding="utf-8")
    frames = []
    for line in result.stdout.splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                frames.append(value)
        except json.JSONDecodeError:
            pass
    init = next((frame for frame in frames if frame.get("type") == "system"
                 and frame.get("subtype") == "init"), None)
    tools = init.get("tools", []) if init else []
    tools = [tool for tool in tools if isinstance(tool, str)]
    used = [block.get("name") for frame in frames if frame.get("type") == "assistant"
            and isinstance(frame.get("message"), dict)
            for block in frame["message"].get("content", [])
            if isinstance(block, dict) and block.get("type") == "tool_use"]
    terminal = next((frame for frame in reversed(frames)
                     if frame.get("type") == "result"), None)
    reported = sorted({frame.get("model") for frame in frames if
                       frame.get("type") in ("system", "result") and
                       isinstance(frame.get("model"), str)})
    if not reported:
        reported = sorted({frame["message"]["model"] for frame in frames if
                           frame.get("type") == "assistant" and
                           isinstance(frame.get("message"), dict) and
                           isinstance(frame["message"].get("model"), str)})
    receipt = {"exit_code": result.returncode, "tools": tools,
               "tool_uses": used, "result_error": terminal.get("is_error") if terminal else None,
               "reported_models": reported,
               "mcp_servers": init.get("mcp_servers", []) if init else [],
               "slash_commands": init.get("slash_commands", []) if init else [],
               "skills": init.get("skills", []) if init else [],
               "plugins": [plugin.get("name") for plugin in init.get("plugins", [])
                           if isinstance(plugin, dict)] if init else [],
               "stream_file": str(output), "stderr_excerpt": result.stderr[-1000:]}
    print(json.dumps(receipt, sort_keys=True))
    return 0 if (result.returncode == 0 and terminal and
                 terminal.get("is_error") is False and tools == [LOCAL_TOOL]
                 and LOCAL_TOOL in used) else 1


if __name__ == "__main__":
    raise SystemExit(main())
