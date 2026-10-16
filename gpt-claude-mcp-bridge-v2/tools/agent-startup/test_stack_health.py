"""Behaviour tests for the START-01 startup wrapper (fixtures only).

No model call, no real bridge, no real CLI, no Codex app: registrations point at a
throwaway stdio MCP fixture server written into a temp directory, CLI sign-in commands are
fake scripts, and dashboards are in-process or on a loopback port nobody uses. Run:

    python -B -I C:/AI/gpt-claude-mcp-bridge-v2/tools/agent-startup/test_stack_health.py
"""
from __future__ import annotations

import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
HELPER = os.path.join(HERE, "stack_health.py")
SCRIPT = os.path.join(HERE, "start-agent-stack.ps1")
OBS_DIR = os.path.abspath(os.path.join(HERE, "..", "agent-observability"))
DASHBOARD_PY = os.path.join(OBS_DIR, "dashboard.py")
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)  # works under python -I
sys.path.insert(0, OBS_DIR)

import dashboard  # noqa: E402  (the real OBS-01 server, served from fixtures)
import stack_health  # noqa: E402

# The interpreter that actually runs: under a venv the venv "python.exe" is a launcher stub
# that spawns this one, which would blur which process a test is killing.
PY = getattr(sys, "_base_executable", None) or sys.executable
JSON_MARKER = "--- JSON summary ---"
SECRET = "s3cret-value-XYZ-7781"

FAKE_SERVER = r'''
import json, os, subprocess, sys, time

MODE = sys.argv[1]
LOG = os.environ.get("FIXTURE_LOG")
SECRET = os.environ.get("FIXTURE_SECRET")
GPT = ["consult_architect", "review_implementation"]
CLAUDE = ["implement_changes", "implementation_status", "capture_source_state",
          "record_review", "list_receipts"]


def log(text):
    if LOG:
        with open(LOG, "a") as fh:
            fh.write(text + "\n")


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def spawn_child():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    log("child %d" % child.pid)


log("launch %s %d" % (MODE, os.getpid()))
if MODE == "exit_early":
    sys.exit(3)
if MODE == "orphan":          # parent exits at once, descendant stays alive
    spawn_child()
    sys.exit(0)
if MODE in ("hang", "hang_child"):
    if MODE == "hang_child":
        spawn_child()
    time.sleep(600)
if MODE == "flood":
    sys.stdout.write("x" * (1024 * 1024))
    sys.stdout.flush()
    time.sleep(600)
if MODE == "stall_stdin":     # a valid request with a huge id, then never read stdin again
    spawn_child()
    sys.stdin.readline()
    send({"jsonrpc": "2.0", "id": "x" * 200000, "method": "ping"})
    time.sleep(600)
if SECRET:
    sys.stderr.write(SECRET + "\n")
    sys.stderr.flush()
tools = CLAUDE if MODE.startswith("claude") else GPT
if MODE == "gpt_missing_tool":
    tools = GPT[:1]
for raw in sys.stdin:
    try:
        msg = json.loads(raw)
    except ValueError:
        continue
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        if MODE == "bad_init":
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": "x"}})
        else:
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                "serverInfo": {"name": "fixture", "version": "0"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
            {"name": n, "description": "d", "inputSchema": {"type": "object"}} for n in tools]}})
    elif method == "tools/call":
        name = msg["params"]["name"]
        log("call " + name)
        bad = MODE == "claude_call_error"
        text = ("failure " + (SECRET or "")) if bad else "{}"
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": text}], "isError": bad}})
    elif mid is not None:
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "no"}})
if MODE.endswith("orphan_on_eof"):   # clean protocol, then a descendant is left behind, exit 0
    spawn_child()
    sys.exit(0)
if MODE.endswith("exit_nonzero_on_eof"):
    sys.exit(5)
'''

FAKE_CLI = r'''
import os, subprocess, sys, time

MODE = sys.argv[1]
SECRET = os.environ.get("FIXTURE_SECRET", "")
LOG = os.environ.get("FIXTURE_LOG")
if MODE == "claude_in":
    print('{"loggedIn": true, "authMethod": "token", "email": "%s"}' % SECRET)
elif MODE == "claude_in_exit1":   # claims signed in but the command itself failed
    print('{"loggedIn": true}')
    sys.exit(1)
elif MODE == "claude_out":
    print('{"loggedIn": false}')
    sys.exit(1)
elif MODE == "codex_in":
    sys.stderr.write("Logged in using ChatGPT %s\n" % SECRET)
elif MODE == "codex_out":
    sys.stderr.write("Not logged in\n")
    sys.exit(1)
elif MODE == "garbage":
    print("zzz " + SECRET)
elif MODE == "crash":
    sys.exit(3)
elif MODE == "flood":
    sys.stdout.write("x" * 300000)
    sys.stdout.flush()
    time.sleep(600)
elif MODE == "hang_child":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    if LOG:
        with open(LOG, "a") as fh:
            fh.write("child %d\n" % child.pid)
    time.sleep(600)
'''


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def pid_alive(pid):
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    import ctypes
    kernel = ctypes.windll.kernel32
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel.CloseHandle(handle)


def wait_dead(pid, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not pid_alive(pid):
            return True
        time.sleep(0.1)
    return not pid_alive(pid)


def powershell():
    return shutil.which("powershell") or shutil.which("pwsh")


def command_line(pid):
    exe = powershell()
    if not exe:
        return ""
    proc = subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-Command",
         "(Get-CimInstance Win32_Process -Filter 'ProcessId=%d').CommandLine" % pid],
        capture_output=True, text=True, timeout=60)
    return proc.stdout


def stop_dashboard(pid, port):
    """Stop only a test-created dashboard, identified by its command line."""
    text = command_line(pid)
    if "dashboard.py" in text and "--port" in text and str(port) in text:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=30)


class Workspace:
    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="start01-")
        self.server = os.path.join(self.dir, "fake_mcp_server.py")
        with open(self.server, "w") as fh:
            fh.write(FAKE_SERVER)
        self.cli = os.path.join(self.dir, "fake_cli.py")
        with open(self.cli, "w") as fh:
            fh.write(FAKE_CLI)
        self.log = os.path.join(self.dir, "launches.log")
        self.state = os.path.join(self.dir, "state")
        os.mkdir(self.state)
        self.config = os.path.join(self.dir, "config.toml")

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write_config(self, regs, env_extra=None):
        """regs: name -> {"mode": ..., "tag": ..., "command": ...} (None leaves it out)."""
        lines = []
        for name, spec in regs.items():
            if spec is None:
                continue
            command = spec.get("command", PY)
            args = [self.server, spec["mode"]] + ([spec["tag"]] if spec.get("tag") else [])
            env = {"FIXTURE_LOG": self.log}
            env.update(env_extra or {})
            lines.append("[mcp_servers.%s]" % name)
            lines.append("command = %s" % json.dumps(command))
            lines.append("args = %s" % json.dumps(args))
            lines.append("[mcp_servers.%s.env]" % name)
            lines.extend("%s = %s" % (k, json.dumps(v)) for k, v in env.items())
        with open(self.config, "w") as fh:
            fh.write("\n".join(lines) + "\n")

    def records(self):
        """Every fixture log record: ['launch', mode, pid], ['child', pid], ['call', tool]."""
        if not os.path.exists(self.log):
            return []
        with open(self.log) as fh:
            return [line.split() for line in fh.read().splitlines() if line]

    def launches(self):
        return [r for r in self.records() if r[0] == "launch"]

    def spawned_pids(self):
        """Server and descendant pids the fixtures logged."""
        return [int(r[2]) if r[0] == "launch" else int(r[1])
                for r in self.records() if r[0] in ("launch", "child")]

    def cli_argv(self, mode):
        return json.dumps([PY, self.cli, mode])


GOOD = {
    "gpt-architect": {"mode": "gpt"},
    "gpt-architect-v1": {"mode": "gpt", "tag": "v1"},
    "gpt-architect-v2": {"mode": "gpt"},
    "claude-implementer": {"mode": "claude"},
}


class DashboardFixture:
    """The real OBS-01 DashboardServer over an empty fixture state directory."""

    def __init__(self, state):
        self.server = dashboard.DashboardServer(0, state, "fixture")
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class FakeHttp:
    """A loopback HTTP server with a scripted handler (foreign service / look-alike)."""

    def __init__(self, routes, default=(200, "text/plain", b"hello")):
        outer = routes

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                status, ctype, body = outer.get(self.path, default)
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class DripServer:
    """Answers one byte at a time so no single recv ever times out (slow-loris style)."""

    def __init__(self, mode):
        self.mode = mode  # "headers": drip the header block; "body": drip the body
        self.stop = threading.Event()
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(8)
        self.listener.settimeout(0.2)
        self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while not self.stop.is_set():
            try:
                conn, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        try:
            conn.settimeout(2)
            try:
                conn.recv(4096)
            except OSError:
                pass
            if self.mode == "headers":
                drip = b"HTTP/1.1 200 OK\r\nX-Pad: " + b"a" * 5000
            else:
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                             b"Content-Length: 1000000\r\n\r\n")
                drip = b"<" * 5000
            for byte in drip:
                if self.stop.is_set():
                    return
                conn.sendall(bytes([byte]))
                time.sleep(0.2)
        except OSError:
            pass
        finally:
            conn.close()

    def close(self):
        self.stop.set()
        self.listener.close()
        self.thread.join(timeout=3)


def run_helper(*args, timeout=180, env=None):
    return subprocess.run([PY, "-B", "-I", HELPER, *args], capture_output=True, text=True,
                          timeout=timeout, env=env)


def health(ws, port, *extra, timeout="8"):
    """Transport/dashboard health with CLI sign-in skipped (these tests use fakes only)."""
    proc = run_helper("health", "--root", ROOT, "--config", ws.config, "--timeout", timeout,
                      "--dashboard-port", str(port), "--format", "json", "--cli-auth", "skip", *extra)
    report = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else None
    return proc, report


class TransportProbeTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)

    def regs(self, report):
        return report["mcp_transport"]["registrations"]

    def assert_all_dead(self, pids):
        self.assertTrue(pids, "the fixtures logged no process to check")
        for pid in pids:
            self.assertTrue(wait_dead(pid), "process %d was left running" % pid)

    def test_all_registrations_pass_and_equivalent_aliases_share_one_launch(self):
        self.ws.write_config(GOOD)
        dash = DashboardFixture(self.ws.state)
        self.addCleanup(dash.close)
        proc, report = health(self.ws, dash.port)
        self.assertEqual(proc.returncode, 0, proc.stdout[-500:])
        regs = self.regs(report)
        self.assertEqual(set(regs), set(GOOD))
        for name, entry in regs.items():
            self.assertEqual(entry["status"], "pass", name)
            # A venv launcher may leave its real interpreter to be reaped; both are full reaps.
            self.assertIn(entry["cleanup"], ("exited", "descendants_reaped"), name)
        self.assert_all_dead(self.ws.spawned_pids())
        # gpt-architect and gpt-architect-v2 are the same launch: three launches, four aliases.
        self.assertEqual(len(self.ws.launches()), 3)
        self.assertEqual(regs["gpt-architect-v2"]["same_launch_as"], "gpt-architect")
        self.assertIsNone(regs["gpt-architect"]["same_launch_as"])
        self.assertEqual(report["mcp_transport"]["launches"], 3)

    def test_pass_never_claims_native_app_connection(self):
        self.ws.write_config(GOOD)
        dash = DashboardFixture(self.ws.state)
        self.addCleanup(dash.close)
        proc, report = health(self.ws, dash.port)
        self.assertEqual(report["verdict"], "pass_native_unverified")
        self.assertEqual(report["app"]["native_tool_connection"], "unverified")
        self.assertIn("native_app_tool_connection", report["not_verified"])

    def test_health_calls_only_read_only_tools_never_model_work(self):
        self.ws.write_config(GOOD)
        dash = DashboardFixture(self.ws.state)
        self.addCleanup(dash.close)
        health(self.ws, dash.port)
        calls = {line[1] for line in self.ws.records() if line[0] == "call"}
        self.assertEqual(calls, {"capture_source_state", "list_receipts"})

    def test_probe_refuses_unlisted_tool_calls_without_starting_anything(self):
        probe = stack_health.StdioProbe(PY, [], {}, ROOT)
        for name in ("implement_changes", "consult_architect", "review_implementation", "record_review"):
            with self.assertRaises(stack_health.ProbeError) as ctx:
                probe.call_tool(name, {}, time.monotonic() + 5)
            self.assertEqual(ctx.exception.code, "call_not_allowed")
        self.assertIsNone(probe.proc)

    def test_missing_expected_tool_fails_with_the_missing_name(self):
        regs = dict(GOOD, **{"gpt-architect-v1": {"mode": "gpt_missing_tool", "tag": "v1"}})
        self.ws.write_config(regs)
        proc, report = health(self.ws, free_port())
        entry = self.regs(report)["gpt-architect-v1"]
        self.assertEqual(entry["status"], "fail")
        self.assertEqual(entry["code"], "tools_missing")
        self.assertEqual(entry["tools"]["missing"], ["review_implementation"])
        self.assertEqual(self.regs(report)["gpt-architect"]["status"], "pass")
        self.assertEqual(proc.returncode, 1)

    def test_startup_timeout_reaps_the_probe_and_its_children(self):
        self.ws.write_config({"claude-implementer": {"mode": "hang_child"}})
        started = time.monotonic()
        proc, report = health(self.ws, free_port(), timeout="3")
        self.assertLess(time.monotonic() - started, 60)
        entry = self.regs(report)["claude-implementer"]
        self.assertEqual(entry["code"], "startup_timeout")
        self.assertEqual(entry["status"], "fail")
        self.assertEqual(entry["cleanup"], "killed")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(len(self.ws.spawned_pids()), 2, "expected the server and its child")
        self.assert_all_dead(self.ws.spawned_pids())

    def test_parent_exiting_early_with_a_live_descendant_is_still_reaped(self):
        self.ws.write_config({"claude-implementer": {"mode": "orphan"}})
        _, report = health(self.ws, free_port(), timeout="8")
        entry = self.regs(report)["claude-implementer"]
        self.assertEqual(entry["status"], "fail")
        self.assertEqual(entry["code"], "exited_early")
        self.assertEqual(entry["cleanup"], "descendants_reaped")
        self.assertEqual(len(self.ws.spawned_pids()), 2)
        self.assert_all_dead(self.ws.spawned_pids())

    def test_descendant_left_behind_after_a_clean_protocol_is_reaped_and_reported(self):
        self.ws.write_config({"claude-implementer": {"mode": "claude_orphan_on_eof"}})
        _, report = health(self.ws, free_port(), timeout="8")
        entry = self.regs(report)["claude-implementer"]
        self.assertEqual(entry["cleanup"], "descendants_reaped")
        self.assert_all_dead(self.ws.spawned_pids())

    def test_nonzero_exit_after_a_clean_protocol_is_a_failure(self):
        self.ws.write_config({"gpt-architect": {"mode": "gpt_exit_nonzero_on_eof"}})
        _, report = health(self.ws, free_port(), timeout="8")
        entry = self.regs(report)["gpt-architect"]
        self.assertEqual(entry["transport"], "pass")
        self.assertEqual(entry["status"], "fail")
        self.assertEqual(entry["code"], "nonzero_exit")

    def test_failed_cleanup_fails_closed(self):
        self.ws.write_config({})
        original = stack_health.StdioProbe.close

        def close_then_report_unreaped(probe):
            original(probe)  # really reap, then claim the tree could not be verified gone
            return "unreaped", None

        launch = {"command": PY, "args": [self.ws.server, "gpt"], "env": {"FIXTURE_LOG": self.ws.log}}
        with mock.patch.object(stack_health.StdioProbe, "close", close_then_report_unreaped):
            result = stack_health.probe_launch(launch, stack_health.GPT_TOOLS, [], ROOT, 15)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["code"], "cleanup_failed")
        self.assertEqual(result["cleanup"], "unreaped")

    def test_stalled_stdin_writer_is_bounded_by_the_probe_deadline_and_reaped(self):
        self.ws.write_config({"gpt-architect": {"mode": "stall_stdin"}})
        started = time.monotonic()
        # Direct helper invocation: no outer PowerShell timeout is involved.
        proc, report = health(self.ws, free_port(), timeout="3")
        self.assertLess(time.monotonic() - started, 40)
        entry = self.regs(report)["gpt-architect"]
        self.assertEqual(entry["status"], "fail")
        self.assertEqual(entry["code"], "write_timeout")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(len(self.ws.spawned_pids()), 2, "expected the server and its child")
        self.assert_all_dead(self.ws.spawned_pids())

    @unittest.skipUnless(os.name == "nt", "Job Objects are Windows-only")
    def test_forced_death_of_the_helper_closes_its_job_and_reaps_probe_trees(self):
        self.ws.write_config({"gpt-architect": {"mode": "hang_child"}})
        helper = subprocess.Popen(
            [PY, "-B", "-I", HELPER, "health", "--root", ROOT, "--config", self.ws.config,
             "--timeout", "120", "--dashboard-port", str(free_port()), "--format", "json",
             "--cli-auth", "skip"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(helper.kill)
        end = time.monotonic() + 30
        while time.monotonic() < end and len(self.ws.spawned_pids()) < 2:
            time.sleep(0.2)
        pids = self.ws.spawned_pids()
        self.assertEqual(len(pids), 2, "the probe tree never came up")
        self.assertTrue(all(pid_alive(p) for p in pids))
        helper.kill()  # TerminateProcess: no atexit, no finally, no tree walk
        helper.wait(timeout=15)
        self.assert_all_dead(pids)

    def test_early_exit_and_initialize_error_are_failures_by_code(self):
        self.ws.write_config({"gpt-architect": {"mode": "exit_early"},
                              "gpt-architect-v1": {"mode": "bad_init", "tag": "v1"}})
        _, report = health(self.ws, free_port())
        regs = self.regs(report)
        self.assertEqual(regs["gpt-architect"]["code"], "exited_early")
        self.assertEqual(regs["gpt-architect-v1"]["code"], "initialize_failed")
        self.assertEqual(regs["gpt-architect-v2"]["code"], "registration_missing")

    def test_oversized_output_is_bounded_and_reaped(self):
        self.ws.write_config({"claude-implementer": {"mode": "flood"}})
        _, report = health(self.ws, free_port(), timeout="8")
        self.assertEqual(self.regs(report)["claude-implementer"]["code"], "oversized_output")
        self.assert_all_dead(self.ws.spawned_pids())

    def test_tool_error_is_a_failure_not_readiness(self):
        self.ws.write_config({"claude-implementer": {"mode": "claude_call_error"}},
                             env_extra={"FIXTURE_SECRET": SECRET})
        proc, report = health(self.ws, free_port())
        entry = self.regs(report)["claude-implementer"]
        self.assertEqual(entry["status"], "fail")
        self.assertEqual(entry["code"], "tool_call_failed")
        self.assertEqual(proc.returncode, 1)

    def test_missing_command_is_reported_without_launching(self):
        regs = dict(GOOD, **{"claude-implementer": {
            "mode": "claude", "command": os.path.join(self.ws.dir, "no-such-python.exe")}})
        self.ws.write_config(regs)
        _, report = health(self.ws, free_port())
        self.assertEqual(self.regs(report)["claude-implementer"]["code"], "command_missing")
        self.assertFalse([line for line in self.ws.launches() if line[1] == "claude"])

    def test_missing_config_is_a_distinct_exit_without_traceback(self):
        proc = run_helper("health", "--root", ROOT, "--config", os.path.join(self.ws.dir, "absent.toml"),
                          "--dashboard-port", str(free_port()), "--format", "json", "--cli-auth", "skip")
        self.assertEqual(proc.returncode, 2)
        report = json.loads(proc.stdout)
        self.assertEqual(report["mcp_transport"]["status"], "not_run")
        self.assertIn("config:config_missing", report["failures"])
        self.assertNotIn("Traceback", proc.stdout + proc.stderr)

    def test_report_never_echoes_secrets_paths_or_server_errors(self):
        self.ws.write_config({"gpt-architect": {"mode": "gpt"},
                              "claude-implementer": {"mode": "claude_call_error"}},
                             env_extra={"FIXTURE_SECRET": SECRET})
        report_file = os.path.join(self.ws.dir, "report.json")
        proc = run_helper("health", "--root", ROOT, "--config", self.ws.config, "--timeout", "8",
                          "--dashboard-port", str(free_port()), "--format", "both",
                          "--report-file", report_file, "--cli-auth", "skip")
        with open(report_file) as fh:
            persisted = fh.read()
        everything = proc.stdout + proc.stderr + persisted
        self.assertNotIn(SECRET, everything)
        self.assertNotIn(self.ws.server, everything)
        self.assertNotIn(PY, everything)
        self.assertNotIn("Traceback", everything)
        self.assertIn(JSON_MARKER, proc.stdout)


class CliAuthTests(unittest.TestCase):
    """Sign-in status checks against fake CLIs only (never the real claude or codex)."""

    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)
        self.ws.write_config(GOOD)
        self.dash = DashboardFixture(self.ws.state)
        self.addCleanup(self.dash.close)

    def run_auth(self, claude, codex, cli_timeout="5", secret=None, extra=()):
        env = dict(os.environ, FIXTURE_LOG=self.ws.log)
        if secret:
            env["FIXTURE_SECRET"] = secret
        self.report_file = os.path.join(self.ws.dir, "auth-report.json")
        started = time.monotonic()
        proc = run_helper("health", "--root", ROOT, "--config", self.ws.config, "--timeout", "10",
                          "--dashboard-port", str(self.dash.port), "--format", "both",
                          "--report-file", self.report_file, "--cli-timeout", cli_timeout,
                          "--claude-argv", claude, "--codex-argv", codex, *extra, env=env)
        self.elapsed = time.monotonic() - started
        text = proc.stdout.split(JSON_MARKER, 1)[1]
        return proc, json.loads(text)

    def test_signed_in_is_reported_and_passes(self):
        proc, report = self.run_auth(self.ws.cli_argv("claude_in"), self.ws.cli_argv("codex_in"))
        auth = report["cli"]["auth"]
        self.assertEqual(auth["claude"]["status"], "signed_in")
        self.assertEqual(auth["codex"]["status"], "signed_in")
        self.assertEqual(report["failures"], [])
        self.assertNotIn("cli_sign_in", report["not_verified"])
        self.assertEqual(proc.returncode, 0)

    def test_not_signed_in_is_a_failure(self):
        proc, report = self.run_auth(self.ws.cli_argv("claude_out"), self.ws.cli_argv("codex_out"))
        auth = report["cli"]["auth"]
        self.assertEqual(auth["claude"]["status"], "not_signed_in")
        self.assertEqual(auth["codex"]["status"], "not_signed_in")
        self.assertIn("cli_auth:claude:not_signed_in", report["failures"])
        self.assertIn("cli_auth:codex:not_signed_in", report["failures"])
        self.assertEqual(proc.returncode, 1)

    def test_unparseable_output_is_unknown_and_fails_normal_health(self):
        proc, report = self.run_auth(self.ws.cli_argv("garbage"), self.ws.cli_argv("garbage"))
        self.assertEqual(report["cli"]["auth"]["claude"]["status"], "unknown")
        self.assertEqual(report["cli"]["auth"]["claude"]["code"], "unparsed_output")
        self.assertIn("cli_sign_in", report["not_verified"])
        self.assertIn("cli_auth:claude:unparsed_output", report["failures"])
        self.assertIn("cli_auth:codex:unparsed_output", report["failures"])
        self.assertEqual(report["verdict"], "fail")
        self.assertEqual(proc.returncode, 1)

    def test_crash_is_error_and_fails_normal_health(self):
        proc, report = self.run_auth(self.ws.cli_argv("crash"), self.ws.cli_argv("claude_in"))
        self.assertEqual(report["cli"]["auth"]["claude"]["status"], "error")
        self.assertEqual(report["cli"]["auth"]["claude"]["code"], "nonzero_exit")
        self.assertIn("cli_auth:claude:nonzero_exit", report["failures"])
        self.assertEqual(proc.returncode, 1)

    def test_nonzero_exit_is_never_signed_in_even_with_a_signed_in_payload(self):
        proc, report = self.run_auth(self.ws.cli_argv("claude_in_exit1"), self.ws.cli_argv("codex_in"))
        self.assertEqual(report["cli"]["auth"]["claude"]["status"], "error")
        self.assertIn("cli_auth:claude:nonzero_exit", report["failures"])
        self.assertEqual(proc.returncode, 1)

    def test_classification_never_reports_signed_in_for_an_unsuccessful_exit(self):
        classify = stack_health.classify_auth
        self.assertEqual(classify("claude", '{"loggedIn": true}', 0), ("signed_in", "ok"))
        self.assertEqual(classify("claude", '{"loggedIn": true}', 1), ("error", "nonzero_exit"))
        self.assertEqual(classify("claude", '{"loggedIn": true}', None), ("error", "did_not_exit"))
        self.assertEqual(classify("codex", "Logged in using ChatGPT", 0), ("signed_in", "ok"))
        self.assertEqual(classify("codex", "Logged in using ChatGPT", 1), ("error", "nonzero_exit"))
        self.assertEqual(classify("claude", '{"loggedIn": false}', 1), ("not_signed_in", "ok"))
        self.assertEqual(classify("codex", "Not logged in", 1), ("not_signed_in", "ok"))
        self.assertEqual(classify("codex", "???", 0), ("unknown", "unparsed_output"))

    def test_missing_executable_is_unknown_and_fails_normal_health(self):
        missing = json.dumps([os.path.join(self.ws.dir, "no-such-cli.exe")])
        proc, report = self.run_auth(missing, self.ws.cli_argv("claude_in"))
        self.assertEqual(report["cli"]["auth"]["claude"]["status"], "unknown")
        self.assertEqual(report["cli"]["auth"]["claude"]["code"], "executable_missing")
        self.assertIn("cli_auth:claude:executable_missing", report["failures"])
        self.assertEqual(proc.returncode, 1)

    def test_timeout_is_error_and_the_cli_tree_is_reaped(self):
        proc, report = self.run_auth(self.ws.cli_argv("hang_child"), self.ws.cli_argv("claude_in"),
                                     cli_timeout="2")
        self.assertEqual(report["cli"]["auth"]["claude"]["status"], "error")
        self.assertEqual(report["cli"]["auth"]["claude"]["code"], "timeout")
        self.assertIn("cli_auth:claude:timeout", report["failures"])
        self.assertEqual(proc.returncode, 1)
        self.assertLess(self.elapsed, 60)
        pids = [int(r[1]) for r in self.ws.records() if r[0] == "child"]
        self.assertEqual(len(pids), 1)
        for pid in pids:
            self.assertTrue(wait_dead(pid), "CLI descendant %d was left running" % pid)

    def test_output_is_bounded(self):
        proc, report = self.run_auth(self.ws.cli_argv("flood"), self.ws.cli_argv("claude_in"),
                                     cli_timeout="10")
        self.assertEqual(report["cli"]["auth"]["claude"]["code"], "output_too_large")
        self.assertIn("cli_auth:claude:output_too_large", report["failures"])
        self.assertEqual(proc.returncode, 1)
        self.assertLess(self.elapsed, 60)

    def test_cli_output_and_secrets_never_reach_the_report(self):
        proc, report = self.run_auth(self.ws.cli_argv("claude_in"), self.ws.cli_argv("codex_in"),
                                     secret=SECRET)
        with open(self.report_file) as fh:
            persisted = fh.read()
        everything = proc.stdout + proc.stderr + persisted
        self.assertNotIn(SECRET, everything)
        self.assertNotIn("ChatGPT", everything)
        self.assertNotIn("authMethod", everything)
        self.assertEqual(report["cli"]["auth"]["claude"]["status"], "signed_in")

    def test_skip_option_is_explicit_and_leaves_sign_in_unverified(self):
        proc, report = self.run_auth(self.ws.cli_argv("claude_out"), self.ws.cli_argv("codex_out"),
                                     extra=("--cli-auth", "skip"))
        self.assertEqual(report["cli"]["auth"]["claude"]["status"], "skipped")
        self.assertIn("cli_sign_in", report["not_verified"])
        self.assertIn("cli_auth:claude:skipped_by_option", report["warnings"])
        self.assertEqual(report["failures"], [])
        self.assertEqual(proc.returncode, 0)

    def test_sign_in_is_checked_by_default(self):
        _, report = self.run_auth(self.ws.cli_argv("claude_in"), self.ws.cli_argv("codex_in"))
        self.assertNotEqual(report["cli"]["auth"]["claude"]["status"], "skipped")


class DashboardIdentificationTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)

    def check(self, port):
        proc = run_helper("dashboard-check", "--port", str(port))
        self.assertEqual(proc.returncode, 0, proc.stderr[-300:])
        return json.loads(proc.stdout)

    def test_free_port_is_absent(self):
        self.assertEqual(self.check(free_port())["state"], "absent")

    def test_real_obs01_is_healthy_and_incomplete_telemetry_is_not_unhealthy(self):
        dash = DashboardFixture(self.ws.state)  # empty state dir: no telemetry yet
        self.addCleanup(dash.close)
        result = self.check(dash.port)
        self.assertEqual(result["state"], "healthy")
        self.assertFalse(result["telemetry"]["complete"])
        self.assertTrue(result["telemetry"]["reasons"])

    def test_foreign_http_service_is_refused_not_reused(self):
        foreign = FakeHttp({})
        self.addCleanup(foreign.close)
        result = self.check(foreign.port)
        self.assertEqual(result["state"], "occupied_unhealthy")
        self.assertEqual(result["code"], "not_obs01")

    def test_lookalike_with_broken_snapshot_is_refused(self):
        page = (b"<html><title>Agent observability (OBS-01)</title>"
                b"<script src=\"/static/app.js\"></script></html>")
        look = FakeHttp({"/": (200, "text/html; charset=utf-8", page),
                         "/static/app.js": (200, "text/javascript", b"//"),
                         "/static/style.css": (200, "text/css", b"/**/"),
                         "/api/snapshot": (500, "text/plain", b"boom")})
        self.addCleanup(look.close)
        self.assertEqual(self.check(look.port)["state"], "occupied_unhealthy")

    def test_non_http_listener_is_refused(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(5)
        self.addCleanup(listener.close)
        stop = threading.Event()

        def accept_and_drop():
            listener.settimeout(0.2)
            while not stop.is_set():
                try:
                    conn, _ = listener.accept()
                    conn.close()
                except OSError:
                    pass

        thread = threading.Thread(target=accept_and_drop, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(stop.set)
        self.assertEqual(self.check(listener.getsockname()[1])["state"], "occupied_unhealthy")

    def test_dripped_headers_end_at_the_wall_clock_deadline(self):
        drip = DripServer("headers")
        self.addCleanup(drip.close)
        started = time.monotonic()
        result = stack_health.check_dashboard(drip.port, budget=2.0)
        self.assertLess(time.monotonic() - started, 8.0)
        self.assertEqual(result["state"], "occupied_unhealthy")
        self.assertEqual(result["code"], "timeout")

    def test_dripped_body_ends_at_the_wall_clock_deadline(self):
        drip = DripServer("body")
        self.addCleanup(drip.close)
        started = time.monotonic()
        result = stack_health.check_dashboard(drip.port, budget=2.0)
        self.assertLess(time.monotonic() - started, 8.0)
        self.assertEqual(result["state"], "occupied_unhealthy")
        self.assertEqual(result["code"], "timeout")

    def test_waiting_for_a_dripping_dashboard_is_bounded_end_to_end(self):
        drip = DripServer("headers")
        self.addCleanup(drip.close)
        started = time.monotonic()
        proc = run_helper("dashboard-check", "--port", str(drip.port), "--wait", "3", timeout=60)
        self.assertLess(time.monotonic() - started, 30.0)
        self.assertEqual(json.loads(proc.stdout)["state"], "occupied_unhealthy")

    def test_health_report_separates_liveness_from_incomplete_telemetry(self):
        self.ws.write_config(GOOD)
        dash = DashboardFixture(self.ws.state)
        self.addCleanup(dash.close)
        proc, report = health(self.ws, dash.port, "--dashboard-action", "reused")
        self.assertEqual(report["dashboard"]["state"], "healthy")
        self.assertFalse(report["dashboard"]["telemetry"]["complete"])
        self.assertIn("dashboard_telemetry_incomplete", report["warnings"])
        self.assertEqual(report["failures"], [])
        self.assertEqual(proc.returncode, 0)

    def test_occupied_port_fails_health_and_leaves_the_occupant_running(self):
        self.ws.write_config(GOOD)
        foreign = FakeHttp({})
        self.addCleanup(foreign.close)
        proc, report = health(self.ws, foreign.port, "--dashboard-action", "refused_port_occupied")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(report["dashboard"]["state"], "occupied_unhealthy")
        self.assertIn("dashboard:not_obs01", report["failures"])
        self.assertEqual(self.check(foreign.port)["code"], "not_obs01")  # still answering

    def test_stopped_dashboard_is_a_failed_check_not_a_silent_pass(self):
        self.ws.write_config(GOOD)
        proc, report = health(self.ws, free_port(), "--dashboard-action", "no_launch_requested")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("dashboard:not_running", report["failures"])


@unittest.skipUnless(os.name == "nt", "dashboard detachment is exercised on Windows")
class DashboardStartTests(unittest.TestCase):
    """dashboard-start run from a caller that captures stdout and stderr through pipes: the
    exact shape that previously hung until the detached dashboard was stopped."""

    def setUp(self):
        if not powershell():
            self.skipTest("PowerShell is needed to verify a process by command line")
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)
        self.port = free_port()
        self.runtime = os.path.join(self.ws.dir, "runtime")
        self.pids = []
        self.addCleanup(self.stop_started)

    def stop_started(self):
        for pid in self.pids:
            stop_dashboard(pid, self.port)

    def start(self):
        started = time.monotonic()
        proc = subprocess.run(
            [PY, "-B", "-I", HELPER, "dashboard-start", "--port", str(self.port),
             "--script", DASHBOARD_PY, "--root", ROOT, "--runtime-dir", self.runtime,
             "--state-dir", self.ws.state, "--wait", "20"],
            capture_output=True, text=True, timeout=90)  # TimeoutExpired = pinned pipes
        self.assertLess(time.monotonic() - started, 60)
        self.assertEqual(proc.returncode, 0, proc.stderr[-300:])
        result = json.loads(proc.stdout)
        if result.get("pid"):
            self.pids.append(result["pid"])
        return result

    def listening(self):
        return json.loads(run_helper("dashboard-check", "--port", str(self.port)).stdout)

    def test_start_returns_with_captured_pipes_and_the_dashboard_survives(self):
        first = self.start()
        self.assertEqual(first["action"], "started")
        self.assertTrue(first["pid"])
        self.assertEqual(self.listening()["state"], "healthy")
        self.assertTrue(pid_alive(first["pid"]))
        with open(os.path.join(self.runtime, "dashboard.json")) as fh:
            self.assertEqual(json.load(fh)["pid"], first["pid"])

    def test_repeat_start_reuses_and_does_not_start_another(self):
        first = self.start()
        second = self.start()
        self.assertEqual(second["action"], "reused")
        self.assertIsNone(second["pid"])
        self.assertTrue(pid_alive(first["pid"]))

    def test_occupied_port_is_refused(self):
        foreign = FakeHttp({})
        self.addCleanup(foreign.close)
        self.port = foreign.port
        self.assertEqual(self.start()["action"], "refused_port_occupied")
        self.assertEqual(self.listening()["code"], "not_obs01")


@unittest.skipUnless(os.name == "nt", "PowerShell entry point is Windows-only")
class EntryPointTests(unittest.TestCase):
    """Runs start-agent-stack.ps1 against fixtures: -SkipCliAuth, a free loopback port, a
    fixture config and a nonexistent app override. The only process it may start is the
    repository's own dashboard.py, which the tests stop by verified command line. Output is
    captured through pipes, exactly as a calling process (or the default console) would."""

    def setUp(self):
        self.exe = powershell()
        if not self.exe:
            self.skipTest("no PowerShell on PATH")
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)
        self.ws.write_config(GOOD)
        self.port = free_port()
        self.started_pids = []
        self.addCleanup(self.stop_started)

    def args(self, *extra, timeout="20"):
        return ["-ConfigPath", self.ws.config, "-PythonPath", PY, "-SkipCliAuth",
                "-AppExecutable", os.path.join(self.ws.dir, "no-app.exe"),
                "-StateDir", self.ws.state, "-DashboardPort", str(self.port),
                "-TimeoutSeconds", timeout, *extra]

    def invoke(self, *extra, timeout="20", limit=240):
        started = time.monotonic()
        proc = subprocess.run([self.exe, "-NoProfile", "-NonInteractive", "-File", SCRIPT,
                               *self.args(*extra, timeout=timeout)],
                              capture_output=True, text=True, timeout=limit, cwd=self.ws.dir)
        text = (proc.stdout + proc.stderr).lower()
        if "running scripts is disabled" in text or "cannot be loaded" in text:
            self.skipTest("execution policy blocks local unsigned scripts")
        return proc, time.monotonic() - started

    def run_script(self, *extra):
        proc, elapsed = self.invoke(*extra)
        self.assertLess(elapsed, 200)
        self.assertIn(JSON_MARKER, proc.stdout, proc.stdout[-800:] + proc.stderr[-300:])
        report = json.loads(proc.stdout.split(JSON_MARKER, 1)[1])
        pid = report["dashboard"].get("started_pid")
        if pid:
            self.started_pids.append(pid)
        return proc, report

    def stop_started(self):
        for pid in self.started_pids:
            stop_dashboard(pid, self.port)

    def listening(self):
        return run_helper("dashboard-check", "--port", str(self.port))

    def test_health_only_launches_nothing(self):
        proc, report = self.run_script("-HealthOnly")
        self.assertEqual(report["mode"], "health_only")
        self.assertEqual(report["dashboard"]["action"], "no_launch_requested")
        self.assertIsNone(report["dashboard"].get("started_pid"))
        self.assertEqual(json.loads(self.listening().stdout)["state"], "absent")
        self.assertEqual(proc.returncode, 1)  # the dashboard is down: an honest failure

    def test_cold_start_then_repeat_reuses_the_same_dashboard(self):
        first_proc, first = self.run_script("-SkipAppLaunch")
        self.assertEqual(first["dashboard"]["action"], "started", first_proc.stdout[-800:])
        self.assertTrue(first["dashboard"]["started_pid"])
        self.assertEqual(first["dashboard"]["state"], "healthy")
        self.assertEqual(first["app"]["native_tool_connection"], "unverified")
        self.assertEqual(first_proc.returncode, 0, first_proc.stdout[-800:])
        # The script has exited and its captured pipes reached EOF; the dashboard still serves.
        self.assertEqual(json.loads(self.listening().stdout)["state"], "healthy")
        pid = first["dashboard"]["started_pid"]
        self.assertTrue(pid_alive(pid))

        second_proc, second = self.run_script("-SkipAppLaunch")
        self.assertEqual(second["dashboard"]["action"], "reused")
        self.assertIsNone(second["dashboard"].get("started_pid"))
        self.assertTrue(pid_alive(pid), "repeat run must not restart the dashboard")
        self.assertEqual(second_proc.returncode, 0)

    def test_occupied_port_is_refused_and_occupant_untouched(self):
        foreign = FakeHttp({})
        self.addCleanup(foreign.close)
        self.port = foreign.port
        proc, report = self.run_script("-SkipAppLaunch")
        self.assertEqual(report["dashboard"]["action"], "refused_port_occupied")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(self.listening().stdout)["code"], "not_obs01")

    def test_wrapper_timeout_stops_the_helper_and_reaps_probe_trees(self):
        self.ws.write_config({"claude-implementer": {"mode": "hang_child"}})
        proc, elapsed = self.invoke("-HealthOnly", "-HelperWaitSeconds", "8", timeout="120", limit=120)
        self.assertLess(elapsed, 100)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("did not complete", proc.stdout)
        pids = self.ws.spawned_pids()
        self.assertEqual(len(pids), 2, "the probe tree never came up before the wrapper deadline")
        for pid in pids:
            self.assertTrue(wait_dead(pid), "probe process %d survived the wrapper timeout" % pid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
