"""Run one isolated Codex → Sonnet → Qwen pilot with project-local MCP settings.

Requires an active reviewed pilot policy, BRIDGE_MONITOR_DSN, and
BRIDGE_MONITOR_RUNTIME_CONFIG. Never edits the user's Codex configuration.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge.runtime.policy_startup import active_policy


PROJECT = Path(__file__).resolve().parents[1]
PYTHON = PROJECT / ".venv" / "Scripts" / "python.exe"
OUT = PROJECT / "var" / "probes"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("normal", "timeout"), default="normal")
    parser.add_argument("--output-stem", default=None,
                        help="project-local receipt name; omit to retain the original pilot names")
    args = parser.parse_args()
    if not os.environ.get("BRIDGE_MONITOR_DSN"):
        raise SystemExit("BRIDGE_MONITOR_DSN is required")
    if not os.environ.get("BRIDGE_MONITOR_SYSTEM_TOKEN"):
        raise SystemExit("BRIDGE_MONITOR_SYSTEM_TOKEN is required")
    policy = active_policy(os.environ["BRIDGE_MONITOR_DSN"])
    if not policy:
        raise SystemExit("reviewed pilot policy is not active")
    client_timeout_seconds = policy.deployment.client_timeout_seconds
    runtime = Path(os.environ.get("BRIDGE_MONITOR_RUNTIME_CONFIG", "")).resolve()
    if not runtime.is_file() or not runtime.is_relative_to(PROJECT):
        raise SystemExit("project-local BRIDGE_MONITOR_RUNTIME_CONFIG is required")
    task = {"task_id": str(uuid.uuid4()), "work_item_id": str(uuid.uuid4()),
            "category": "arithmetic", "scope": ["analysis"],
            "token_budget": 10000, "call_budget": 2}
    if args.case == "normal":
        worker_prompt = ("Delegate exactly one bounded arithmetic check to Qwen: calculate 7 * 8. "
                         "Use its answer if correct. Return the answer and an accurate local-result "
                         "disposition in the required completion JSON.")
    else:
        worker_prompt = ("Delegate exactly one arithmetic check to Qwen: calculate 7 * 8 "
                         "and produce at least 100 numbered verification lines. If local work "
                         "times out or requires paid continuation, calculate 7 * 8 yourself "
                         "and answer 56. Report the fallback_child_call_id and no invented "
                         "local result disposition in the required completion JSON.")
    prompt = ("Call bridge_monitor.delegate_claude exactly once with these arguments: "
              + json.dumps({"task": task, "prompt": worker_prompt,
                            "requested_model": "sonnet", "reserve_tokens": 5000})
              + ". Do not edit files or call any other tool. Return the tool result and "
                "state whether you accept the worker's answer.")
    env_vars = ["BRIDGE_MONITOR_DSN", "BRIDGE_MONITOR_RUNTIME_CONFIG",
                "BRIDGE_MONITOR_SYSTEM_TOKEN"]
    if policy.deployment.qwen_agent_endpoint:
        agent_token_name = policy.deployment.qwen_agent_token_ref.removeprefix("env:")
        if not os.environ.get(agent_token_name):
            raise SystemExit(f"{agent_token_name} is required for the agent route")
        env_vars.append(agent_token_name)
    config = {
        "mcp_servers.bridge_monitor.command": str(PYTHON.resolve(strict=True)),
        "mcp_servers.bridge_monitor.args": ["-m", "bridge.mcp_server", "--audience", "parent"],
        "mcp_servers.bridge_monitor.env_vars": env_vars,
        "mcp_servers.bridge_monitor.tool_timeout_sec": client_timeout_seconds,
        "mcp_servers.bridge_monitor.tools.delegate_claude.approval_mode": "approve",
    }
    command = ["codex", "exec", "--ignore-user-config", "--strict-config",
               "--ephemeral", "-C", str(PROJECT), "-s", "workspace-write",
               "-m", "gpt-6.1-sol"]
    for key, value in config.items():
        command.extend(["-c", f"{key}={json.dumps(value)}"])
    command.extend(["--json", prompt])
    OUT.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    completed = subprocess.run(command, cwd=PROJECT, env=os.environ.copy(),
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=client_timeout_seconds + 60)
    stem = args.output_stem or f"codex-{args.case}-flow"
    if not stem or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in stem):
        raise SystemExit("output stem must be a simple filename")
    stream = OUT / f"{stem}.jsonl"
    stream.write_text(completed.stdout, encoding="utf-8")
    (OUT / f"{stem}.stderr.txt").write_text(
        completed.stderr, encoding="utf-8")
    frames = []
    for line in completed.stdout.splitlines():
        try:
            frames.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    calls = [frame["item"] for frame in frames if frame.get("type") == "item.completed"
             and isinstance(frame.get("item"), dict)
             and frame["item"].get("type") == "mcp_tool_call"]
    bridge_result = None
    if len(calls) == 1 and isinstance(calls[0].get("result"), dict):
        content = calls[0]["result"].get("content", [])
        if content and isinstance(content[0], dict):
            try:
                bridge_result = json.loads(content[0].get("text", ""))
            except (ValueError, TypeError):
                pass
    with psycopg.connect(os.environ["BRIDGE_MONITOR_DSN"]) as conn:
        routes = conn.execute("SELECT route FROM delegation_decisions "
            "WHERE work_item_id=%s ORDER BY decided_at", (task["work_item_id"],)).fetchall()
        fallback = conn.execute("SELECT f.state,f.timeout_stage,j.slot_held "
            "FROM runtime_fallback_transitions f JOIN runtime_jobs j "
            "ON j.call_id=f.child_call_id WHERE f.work_item_id=%s",
            (task["work_item_id"],)).fetchone()
        resolution = conn.execute("SELECT outcome FROM work_resolutions "
            "WHERE work_item_id=%s ORDER BY recorded_at DESC LIMIT 1",
            (task["work_item_id"],)).fetchone()
    route_names = [row[0] for row in routes]
    effective_routes = [route for route in route_names if route in ("local", "paid")]
    receipt = {"case": args.case, "task": task, "worker_prompt": worker_prompt,
               "client_tool_timeout_seconds": client_timeout_seconds,
               "elapsed_seconds": round(time.monotonic() - started, 3),
               "exit_code": completed.returncode,
               "bridge_result": bridge_result,
               "routes": route_names, "effective_routes": effective_routes,
               "fallback": fallback,
               "resolution": resolution[0] if resolution else None,
               "calls": [{"server": item.get("server"), "tool": item.get("tool"),
                          "status": item.get("status"), "error": item.get("error"),
                          "result": item.get("result")} for item in calls],
               "stream_file": str(stream)}
    (OUT / f"{stem}.receipt.json").write_text(
        json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, sort_keys=True))
    expected_accounting = (effective_routes == ["local"] and fallback is None and
                           receipt["resolution"] == "local_retained"
                           if args.case == "normal" else
                           effective_routes == ["local", "paid"] and
                           fallback == ("completed", "execution", True) and
                           receipt["resolution"] == "paid_replaced")
    return 0 if (completed.returncode == 0 and len(calls) == 1 and
                 calls[0].get("server") == "bridge_monitor" and
                 calls[0].get("tool") == "delegate_claude" and
                 calls[0].get("status") == "completed" and
                 isinstance(bridge_result, dict) and
                 bridge_result.get("state") == "completed" and
                 bridge_result.get("accounting_complete") is True and
                 expected_accounting) else 1


if __name__ == "__main__":
    raise SystemExit(main())
