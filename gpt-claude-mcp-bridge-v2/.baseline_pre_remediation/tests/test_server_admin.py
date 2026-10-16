"""MCP surface (names, schemas, real stdio round-trip, stdout hygiene) and admin CLI."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

from tests.helpers import FIXTURES, ROOT, WorldCase

from bridge import admin, config, errors


def _tool_dump(tool) -> dict:
    return tool.model_dump(mode="json", exclude_none=True)


class McpSurface(unittest.TestCase):
    def test_exactly_two_tools_with_v1_schemas(self):
        from bridge.server import mcp

        tools = asyncio.run(mcp.list_tools())
        got = sorted((_tool_dump(t) for t in tools), key=lambda d: d["name"])
        expected = json.loads((FIXTURES / "v1_tool_schemas.json").read_text(encoding="utf-8"))
        self.assertEqual([t["name"] for t in got], ["consult_architect", "review_implementation"])
        self.assertEqual(got, expected, "tool names, descriptions and schemas must be byte-identical to v1")

    def test_signatures_are_single_required_string(self):
        from bridge.server import mcp

        by_name = {t.name: t for t in asyncio.run(mcp.list_tools())}
        self.assertEqual(by_name["consult_architect"].inputSchema["required"], ["question"])
        self.assertEqual(by_name["review_implementation"].inputSchema["required"], ["focus"])
        for t in by_name.values():
            self.assertEqual(len(t.inputSchema["properties"]), 1)


class StdioRoundTrip(WorldCase):
    """Spawn the real server process and talk MCP to it over stdio."""

    def _server_env(self) -> dict[str, str]:
        env = self.w.env()
        env.setdefault("SYSTEMROOT", os.environ.get("SYSTEMROOT", ""))
        return env

    def test_client_session_end_to_end_with_one_argument_callers(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def scenario():
            params = StdioServerParameters(
                command=sys.executable, args=[str(ROOT / "architect_bridge.py")], env=self._server_env()
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    first = await session.call_tool("consult_architect", {"question": "How is X layered?"})
                    second = await session.call_tool("review_implementation", {"focus": "negative tests"})
                    bad = await session.call_tool("consult_architect", {"question": "   "})
                    return listed, first, second, bad

        listed, first, second, bad = asyncio.run(asyncio.wait_for(scenario(), 120))
        self.assertEqual(sorted(t.name for t in listed.tools), ["consult_architect", "review_implementation"])
        self.assertFalse(first.isError)
        self.assertEqual(first.content[0].text, "FAKE ANSWER")
        self.assertEqual(second.content[0].text, "FAKE ANSWER")
        self.assertEqual([r["kind"] for r in self.w.exec_entries()], ["exec_fresh", "exec_resume"])
        self.assertTrue(bad.isError)
        self.assertIn("bridge-error:invalid_input", bad.content[0].text)
        self.assertEqual(len(self.w.telemetry()), 3)

    def test_stdout_carries_only_json_rpc(self):
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "architect_bridge.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=self._server_env(), cwd=str(self.w.tmp),
        )
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "consult_architect", "arguments": {"question": "q"}}},
        ]
        assert proc.stdin is not None and proc.stdout is not None
        for m in messages:
            proc.stdin.write((json.dumps(m) + "\n").encode())
        proc.stdin.flush()
        lines: list[bytes] = []
        deadline = time.monotonic() + 60
        seen_ids: set[int] = set()
        while time.monotonic() < deadline and seen_ids != {1, 2, 3}:
            line = proc.stdout.readline()
            if not line:
                break
            lines.append(line)
            with contextlib.suppress(Exception):
                seen_ids.add(json.loads(line).get("id"))
        proc.stdin.close()
        proc.wait(timeout=20)
        stderr_text = proc.stderr.read()
        proc.stdout.close()
        proc.stderr.close()
        self.assertEqual(seen_ids, {1, 2, 3})
        for line in lines:
            obj = json.loads(line)  # every stdout line must be a JSON object ...
            self.assertEqual(obj.get("jsonrpc"), "2.0")  # ... that is a JSON-RPC message
        self.assertEqual(stderr_text.count(b"FAKE ANSWER"), 0)

    def test_mcp_cancellation_notification_kills_tree_and_writes_terminal_record(self):
        from tests.helpers import pid_alive, wait_for

        pidfile, started = self.w.tmp / "mcp_child.pid", self.w.tmp / "mcp_started"
        self.w.set(exec={"sleep": 120, "spawn_child": str(pidfile), "started_file": str(started)})
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "architect_bridge.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=self._server_env(), cwd=str(self.w.tmp),
        )
        assert proc.stdin is not None and proc.stdout is not None

        def send(obj):
            proc.stdin.write((json.dumps(obj) + "\n").encode())
            proc.stdin.flush()

        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}})
        proc.stdout.readline()
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
              "params": {"name": "consult_architect", "arguments": {"question": "long one"}}})
        try:
            wait_for(started, 30)
            child = int(wait_for(pidfile, 30))
            self.assertTrue(pid_alive(child))
            send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                  "params": {"requestId": 7, "reason": "user cancelled"}})
            deadline = time.monotonic() + 30
            while pid_alive(child) and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertFalse(pid_alive(child), "Codex process tree must die on MCP cancellation")
            deadline = time.monotonic() + 30
            while not self.w.telemetry() and time.monotonic() < deadline:
                time.sleep(0.1)
            rec = self.w.telemetry()[0]
            self.assertEqual((rec["outcome"], rec["error_category"]), ("cancelled", "cancelled"))
            self.assertEqual(len(self.w.exec_entries()), 1, "cancelled call is never replayed")
            # the lock is free again: a direct follow-up call is not queued
            self.w.set(exec={})
            self.assertEqual(self.service(GPT_ARCHITECT_QUEUE_TIMEOUT="1").execute(config.CONSULT, "x"),
                             "FAKE ANSWER")
        finally:
            proc.stdin.close()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
            proc.stdout.close()


class AdminCli(WorldCase):
    def run_admin(self, *argv, env=None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = admin.main(list(argv), env or self.w.env())
        return rc, out.getvalue(), err.getvalue()

    def test_status_reports_routes_auth_audit_and_registry_without_inference(self):
        self.w.set(mcp_servers=[{"name": "idea", "source": "config"}])
        self.service().execute(config.CONSULT, "q")
        before = len(self.w.exec_entries())
        rc, out, _ = self.run_admin("status", "--json")
        self.assertEqual(rc, 0, out)
        report = json.loads(out)
        self.assertEqual(report["codex_cli"]["auth"], "chatgpt")
        self.assertTrue(report["codex_cli"]["compatible"])
        self.assertEqual(report["routes"]["consult_architect"]["model"], "gpt-6.1-sol")
        self.assertEqual(report["capabilities"]["disabled_mcp_servers"], ["idea"])
        self.assertEqual(len(report["registry"]["entries"]), 1)
        self.assertEqual(len(self.w.exec_entries()), before, "status must never run inference")
        self.assertNotIn("auth.json", out)

    def test_status_flags_problems_with_nonzero_exit(self):
        self.w.set(auth="api_key")
        rc, out, _ = self.run_admin("status")
        self.assertEqual(rc, 1)
        self.assertIn("auth_api_key", out)

    def test_new_then_first_call_uses_named_session(self):
        rc, out, _ = self.run_admin("new", "--session", "arch2")
        self.assertEqual(rc, 0, out)
        reg = self.w.registry()["sessions"]
        self.assertEqual(len(reg), 1)
        self.assertIsNone(next(iter(reg.values()))["thread_id"])
        self.service(GPT_ARCHITECT_SESSION="arch2").execute(config.CONSULT, "q")
        self.assertEqual([r["kind"] for r in self.w.exec_entries()], ["exec_fresh"])
        self.assertIsNotNone(next(iter(self.w.registry()["sessions"].values()))["thread_id"])
        self.assertEqual(self.run_admin("new", "--session", "arch2")[1].count("already exists"), 1)

    def test_reset_requires_yes_retires_pointer_keeps_audit_and_next_call_is_fresh(self):
        svc = self.service()
        svc.execute(config.CONSULT, "q")
        tid = next(iter(self.w.registry()["sessions"].values()))["thread_id"]
        self.assertEqual(self.run_admin("reset")[0], 2)
        self.assertEqual(len(self.w.registry()["sessions"]), 1)
        rc, out, _ = self.run_admin("reset", "--yes")
        self.assertEqual(rc, 0, out)
        self.assertIn(tid, out)
        self.assertIn("not touched", out)
        reg = self.w.registry()
        self.assertEqual(reg["sessions"], {})
        self.assertEqual(reg["retired"][0]["thread_id"], tid)
        svc.execute(config.CONSULT, "again")
        self.assertEqual([r["kind"] for r in self.w.exec_entries()], ["exec_fresh", "exec_fresh"])

    def test_reset_refuses_while_a_call_is_running(self):
        from bridge import state

        root, repo_key = state.canonical_repo(str(self.w.repo))
        key = state.session_key(repo_key, "gpt-6.1-sol", "default")
        reg = state.Registry(self.w.state)
        reg.ensure_dirs()
        lock = reg.session_lock(key)
        lock.acquire(2)
        try:
            rc, out, _ = self.run_admin("reset", "--yes", "--model", "gpt-6.1-sol")
        finally:
            lock.release()
        self.assertEqual(rc, 1)
        self.assertIn("busy", out)

    def test_quarantine_recovers_from_corrupt_registry_explicitly(self):
        self.w.state.mkdir(parents=True)
        (self.w.state / "registry.json").write_bytes(b"][")
        rc, out, err = self.run_admin("status")
        self.assertEqual(rc, 1)
        self.assertIn("session_corrupt", out + err)
        self.assertEqual(self.run_admin("quarantine")[0], 2)
        rc, out, _ = self.run_admin("quarantine", "--yes")
        self.assertEqual(rc, 0)
        self.assertEqual(len(list(self.w.state.glob("registry.corrupt-*.json"))), 1)
        self.service().execute(config.CONSULT, "q")  # works again, explicitly


if __name__ == "__main__":
    unittest.main()
