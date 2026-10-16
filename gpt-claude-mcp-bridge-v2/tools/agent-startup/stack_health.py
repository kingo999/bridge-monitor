#!/usr/bin/env python3
"""START-01 model-free startup health checks (Python 3.11+ standard library only).

    stack_health.py health           probe the registered MCP bridges with temporary stdio
                                     clients, check CLI sign-in and the dashboard, print one report
    stack_health.py dashboard-check  identify whatever answers on the dashboard port
    stack_health.py dashboard-start  start (detached, hidden) or reuse the OBS-01 dashboard

Nothing here runs a model, a consultation or a review. The probes speak the MCP stdio
wire protocol directly (initialize, tools/list and two read-only Claude tools). Every
temporary child process (probe or CLI status check) is owned: on Windows it is created
suspended, assigned to a Job Object with KILL_ON_JOB_CLOSE and only then resumed, so the
whole tree dies with the check, even if the root exits first or this helper is killed.
Only fixed codes and tool names reach the report: never configuration, environment values,
paths, tool or CLI output, or raw exceptions.

Exit codes: 0 all checks passed, 1 one or more checks failed, 2 prerequisites missing.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import http.client
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

SCHEMA = "start-01/1"
LOOPBACK = "127.0.0.1"
DEFAULT_PORT = 8089
DEFAULT_TIMEOUT = 45.0
DEFAULT_CLI_TIMEOUT = 20.0

MAX_LINE = 256 * 1024          # one protocol message from a probed server
MAX_TOTAL = 4 * 1024 * 1024    # everything one probed server may send
MAX_NOISE_LINES = 50
MAX_TOOL_PAGES = 5
MAX_CONFIG_BYTES = 1024 * 1024
MAX_CLI_OUTPUT = 64 * 1024     # CLI status output is classified, never kept or shown
GRACE_SECONDS = 5.0            # after closing stdin, before a probe tree is killed
KILL_WAIT_SECONDS = 5.0
WATCHDOG_SLACK_SECONDS = 1.0   # the watchdog fires after the probe deadline so normal timeouts win

MAX_HTML = 256 * 1024
MAX_ASSET = 2 * 1024 * 1024
MAX_SNAPSHOT = 8 * 1024 * 1024
HTTP_STEP_TIMEOUT = 3.0
DASHBOARD_BUDGET = 12.0
CONNECT_TIMEOUT = 5.0
DASHBOARD_START_WAIT = 20.0

GPT_TOOLS = frozenset({"consult_architect", "review_implementation"})
CLAUDE_TOOLS = frozenset({"implement_changes", "implementation_status", "capture_source_state",
                          "record_review", "list_receipts"})
REGISTRATIONS = {
    "gpt-architect": GPT_TOOLS,
    "gpt-architect-v1": GPT_TOOLS,
    "gpt-architect-v2": GPT_TOOLS,
    "claude-implementer": CLAUDE_TOOLS,
}
# The only tool calls a health check may ever make. Both only read local state.
READ_ONLY_CALLS = {
    "claude-implementer": (("capture_source_state", {}), ("list_receipts", {"limit": 1})),
}
SAFE_TOOL_CALLS = frozenset(name for calls in READ_ONLY_CALLS.values() for name, _ in calls)

# Model-free status commands (never log in, never modify auth).
CLI_AUTH_COMMANDS = {
    "claude": ("claude", ["auth", "status", "--json"]),
    "codex": ("codex", ["login", "status"]),
}

STAGE_CODES = {  # stage -> (timeout code, error-response code)
    "initialize": ("startup_timeout", "initialize_failed"),
    "list": ("list_tools_timeout", "list_tools_failed"),
    "call": ("tool_call_timeout", "tool_call_failed"),
}

APP_FAILURES = frozenset({"not_installed", "manifest_unrecognized", "override_missing",
                          "detection_failed", "launch_failed", "launch_unconfirmed"})
APP_STATUSES = ("not_checked", "running", "not_running", "unknown", "reused", "started") \
    + tuple(sorted(APP_FAILURES))
DASHBOARD_ACTIONS = ("not_checked", "reused", "started", "no_launch_requested",
                     "refused_port_occupied", "start_failed", "start_unhealthy", "script_missing")
DASHBOARD_ACTION_FAILURES = frozenset({"start_failed", "start_unhealthy", "script_missing"})

VERDICT_TEXT = {
    "pass_native_unverified": "PASS - direct MCP transport checks passed; the native Codex app "
                              "tool connection is NOT verified",
    "fail": "FAIL - one or more checks failed",
}
_EOF = object()
_OVERSIZE = object()


class ProbeError(Exception):
    """A probe failure carrying only a fixed code (never server text)."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class ConfigError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ owned process trees

CREATE_SUSPENDED = 0x00000004
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
PROCESS_SUSPEND_RESUME = 0x0800

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _BasicLimit(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _ExtendedLimit(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BasicLimit),
                    ("IoInfo", _IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class _Accounting(ctypes.Structure):
        _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                    ("ThisPeriodTotalUserTime", ctypes.c_int64),
                    ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                    ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                    ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]

    _K32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _NT = ctypes.WinDLL("ntdll")
    _K32.CreateJobObjectW.restype = wintypes.HANDLE
    _K32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _K32.SetInformationJobObject.restype = wintypes.BOOL
    _K32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _K32.QueryInformationJobObject.restype = wintypes.BOOL
    _K32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                               wintypes.DWORD, ctypes.c_void_p]
    _K32.AssignProcessToJobObject.restype = wintypes.BOOL
    _K32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _K32.TerminateJobObject.restype = wintypes.BOOL
    _K32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _K32.OpenProcess.restype = wintypes.HANDLE
    _K32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _K32.CloseHandle.restype = wintypes.BOOL
    _K32.CloseHandle.argtypes = [wintypes.HANDLE]
    _NT.NtResumeProcess.restype = ctypes.c_long
    _NT.NtResumeProcess.argtypes = [wintypes.HANDLE]


class _WindowsTree:
    """A Job Object that kills every member when terminated or when its handle closes."""

    def __init__(self):
        job = _K32.CreateJobObjectW(None, None)
        if not job:
            raise ProbeError("job_failed")
        info = _ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _K32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            _K32.CloseHandle(job)
            raise ProbeError("job_failed")
        self._job = job

    def adopt(self, pid):
        """Assign a suspended process to the job, then let it run (fail closed)."""
        process = _K32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_SUSPEND_RESUME,
                                   False, pid)
        if not process:
            raise ProbeError("job_failed")
        try:
            if not _K32.AssignProcessToJobObject(self._job, process):
                raise ProbeError("job_failed")
            if _NT.NtResumeProcess(process) != 0:
                raise ProbeError("job_failed")
        finally:
            _K32.CloseHandle(process)

    def members(self):
        """Live processes in the job (None when it cannot be determined)."""
        job = self._job
        if not job:
            return 0
        info = _Accounting()
        if not _K32.QueryInformationJobObject(job, 1, ctypes.byref(info), ctypes.sizeof(info), None):
            return None
        return int(info.ActiveProcesses)

    def kill(self):
        job = self._job
        if job:
            _K32.TerminateJobObject(job, 1)

    def release(self):
        job, self._job = self._job, None
        if job:
            _K32.CloseHandle(job)  # KILL_ON_JOB_CLOSE also reaps anything that slipped through


class _PosixTree:
    """The probe's own process group (it leads a new session), tracked past root exit."""

    def __init__(self):
        self._pgid = None

    def adopt(self, pid):
        self._pgid = pid

    def members(self):
        if self._pgid is None:
            return 0
        try:
            os.killpg(self._pgid, 0)
        except ProcessLookupError:
            return 0
        except OSError:
            return 1
        return 1

    def kill(self):
        if self._pgid is not None:
            import signal
            try:
                os.killpg(self._pgid, signal.SIGKILL)
            except OSError:
                pass

    def release(self):
        pass


class OwnedProcess:
    """One temporary child plus every descendant it creates; always reaped by shutdown()."""

    def __init__(self, argv, env=None, cwd=None, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                 stderr=subprocess.DEVNULL):
        self.argv, self.env, self.cwd = list(argv), env, cwd
        self.stdin, self.stdout, self.stderr = stdin, stdout, stderr
        self.proc = None
        self._tree = None
        self._lock = threading.Lock()
        self._released = False

    def kill_tree(self):
        """Kill the whole owned tree now. Safe from any thread (a watchdog) and idempotent;
        it never closes a pipe, so a writer blocked on the child is released by the child's
        death rather than by a close that could itself block."""
        with self._lock:
            if self._tree is not None and not self._released:
                self._tree.kill()

    def start(self):
        tree = _WindowsTree() if os.name == "nt" else _PosixTree()
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                                       | CREATE_SUSPENDED)
        else:
            kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(self.argv, stdin=self.stdin, stdout=self.stdout, stderr=self.stderr,
                                    cwd=self.cwd, env=self.env, close_fds=True, **kwargs)
        except (OSError, ValueError):
            tree.release()
            raise ProbeError("launch_failed") from None
        try:
            tree.adopt(proc.pid)
        except ProbeError:
            try:
                proc.kill()
                proc.wait(timeout=KILL_WAIT_SECONDS)
            except (OSError, subprocess.SubprocessError):
                pass
            for stream in (proc.stdin, proc.stdout):
                try:
                    if stream is not None:
                        stream.close()
                except (OSError, ValueError):
                    pass
            tree.release()
            raise
        self.proc, self._tree = proc, tree

    def _wait_empty(self):
        try:
            self.proc.wait(timeout=KILL_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            return False
        end = time.monotonic() + KILL_WAIT_SECONDS
        while True:
            if self._tree.members() == 0:
                return True
            if time.monotonic() >= end:
                return False
            time.sleep(0.05)

    def shutdown(self, grace):
        """Give the root `grace` seconds to exit, then reap the whole tree.

        Returns (outcome, exit_code): outcome is exited | descendants_reaped | killed |
        unreaped | not_started; exit_code is the root's own exit status, or None when the
        root had to be killed.
        """
        proc, tree = self.proc, self._tree
        if proc is None:
            return "not_started", None
        root_exited = False
        try:
            proc.wait(timeout=max(0.0, grace))
            root_exited = True
        except subprocess.TimeoutExpired:
            pass
        exit_code = proc.returncode if root_exited else None
        members = tree.members()
        survivors = members is None or members > (0 if root_exited else 1)
        if root_exited and not survivors:
            outcome = "exited"
        else:
            tree.kill()
            if self._wait_empty():
                outcome = "descendants_reaped" if root_exited else "killed"
            else:
                outcome = "unreaped"
        with self._lock:
            self._released = True
            tree.release()
        return outcome, exit_code


# ------------------------------------------------------------------ MCP stdio probe

class StdioProbe:
    """One temporary MCP stdio client that owns its server process tree."""

    def __init__(self, command, args, env, cwd):
        self.command, self.args, self.env, self.cwd = command, list(args), env, cwd
        self.owned = None
        self.proc = None
        self._lines = queue.Queue()
        self._reader = None
        self._next_id = 0
        self._noise = 0
        self._watchdog = None
        self._expired = False
        self._send_failed = False

    def start(self, deadline=None):
        """Start the server. With `deadline` (monotonic), a watchdog kills the owned tree shortly
        after it, so even a write blocked on a peer that stopped reading cannot outlast it."""
        self.owned = OwnedProcess([self.command, *self.args], env=self.env, cwd=self.cwd)
        self.owned.start()
        self.proc = self.owned.proc
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        if deadline is not None:
            delay = max(0.0, deadline - time.monotonic()) + WATCHDOG_SLACK_SECONDS
            self._watchdog = threading.Timer(delay, self._expire)
            self._watchdog.daemon = True
            self._watchdog.start()

    def _expire(self):
        self._expired = True
        self.owned.kill_tree()

    def _pump(self):
        total = 0
        try:
            while True:
                line = self.proc.stdout.readline(MAX_LINE + 1)
                if not line:
                    break
                total += len(line)
                if len(line) > MAX_LINE or total > MAX_TOTAL:
                    self._lines.put(_OVERSIZE)
                    return
                self._lines.put(line)
        except (OSError, ValueError):
            pass
        self._lines.put(_EOF)

    def _send(self, message):
        data = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()
        except (OSError, ValueError):
            self._send_failed = True
            raise ProbeError("write_timeout" if self._expired else "exited_early") from None

    def _receive(self, deadline, timeout_code):
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProbeError(timeout_code)
            try:
                item = self._lines.get(timeout=min(0.1, remaining))
            except queue.Empty:
                if self.proc.poll() is None:
                    continue
                # The root is gone but a descendant may still hold the pipe, so stdout never
                # reaches EOF. Whatever it wrote is queued by now; no response means it died.
                try:
                    item = self._lines.get(timeout=0.5)
                except queue.Empty:
                    raise ProbeError(timeout_code if self._expired else "exited_early") from None
            if item is _EOF:
                raise ProbeError(timeout_code if self._expired else "exited_early")
            if item is _OVERSIZE:
                raise ProbeError("oversized_output")
            try:
                message = json.loads(item)
            except ValueError:
                message = None
            if isinstance(message, dict):
                return message
            self._noise += 1
            if self._noise > MAX_NOISE_LINES:
                raise ProbeError("protocol_error")

    def request(self, method, params, deadline, stage):
        timeout_code, error_code = STAGE_CODES[stage]
        self._next_id += 1
        request_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        while True:
            message = self._receive(deadline, timeout_code)
            if "method" in message:
                if "id" in message:  # a server-to-client request: decline it
                    self._send({"jsonrpc": "2.0", "id": message["id"],
                                "error": {"code": -32601, "message": "unsupported"}})
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message or not isinstance(message.get("result"), dict):
                raise ProbeError(error_code)
            return message["result"]

    def initialize(self, deadline):
        result = self.request("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "start-01-health", "version": "1"}}, deadline, "initialize")
        if not isinstance(result.get("protocolVersion"), str):
            raise ProbeError("protocol_error")
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def list_tools(self, deadline):
        names, cursor = set(), None
        for _ in range(MAX_TOOL_PAGES):
            result = self.request("tools/list", {"cursor": cursor} if cursor else {}, deadline, "list")
            tools = result.get("tools")
            if not isinstance(tools, list):
                raise ProbeError("protocol_error")
            names.update(t["name"] for t in tools if isinstance(t, dict) and isinstance(t.get("name"), str))
            cursor = result.get("nextCursor")
            if not cursor or not isinstance(cursor, str):
                return names
        raise ProbeError("protocol_error")

    def call_tool(self, name, arguments, deadline):
        if name not in SAFE_TOOL_CALLS:
            raise ProbeError("call_not_allowed")
        result = self.request("tools/call", {"name": name, "arguments": arguments}, deadline, "call")
        if result.get("isError"):
            raise ProbeError("tool_call_failed")

    def close(self):
        """Close stdin and reap the whole tree. Returns (cleanup outcome, root exit code)."""
        if self.owned is None or self.proc is None:
            return "not_started", None
        if self._watchdog is not None:
            self._watchdog.cancel()
        if self._send_failed or self._expired:
            # Closing stdin flushes buffered bytes and could block on a peer that stopped
            # reading: kill the owned tree first so the flush fails fast instead.
            self.owned.kill_tree()
        try:
            self.proc.stdin.close()
        except (OSError, ValueError):
            pass
        outcome, exit_code = self.owned.shutdown(GRACE_SECONDS)
        if self._reader is not None:
            self._reader.join(timeout=2)
            if not self._reader.is_alive():
                try:
                    self.proc.stdout.close()
                except (OSError, ValueError):
                    pass
        return outcome, exit_code


def _result(status, code, transport="not_run", tools=None, checks=None, elapsed=0.0, cleanup="not_started"):
    return {"status": status, "transport": transport, "code": code, "tools": tools,
            "read_only_checks": checks or {}, "elapsed_ms": int(elapsed * 1000), "cleanup": cleanup}


def probe_launch(launch, expected, calls, root, timeout):
    """Probe one launch; never raises and never returns server-supplied text."""
    started = time.monotonic()
    deadline = started + timeout
    env = dict(os.environ)
    env.update(launch["env"])
    probe = StdioProbe(launch["command"], launch["args"], env, root)
    transport, code, tools, checks = "fail", "probe_error", None, {}
    cleanup, exit_code = "not_started", None
    try:
        probe.start(deadline)
        probe.initialize(deadline)
        names = probe.list_tools(deadline)
        missing = sorted(expected - names)
        tools = {"expected": sorted(expected), "missing": missing, "discovered": len(names)}
        if missing:
            raise ProbeError("tools_missing")
        transport, code = "pass", "ok"
        for name, arguments in calls:
            try:
                probe.call_tool(name, arguments, deadline)
                checks[name] = "ok"
            except ProbeError as exc:
                checks[name] = exc.code
                if code == "ok":
                    code = exc.code
    except ProbeError as exc:
        code = exc.code
    except Exception:  # noqa: BLE001 - nothing raw may leave the probe
        code = "probe_error"
    finally:
        try:
            cleanup, exit_code = probe.close()
        except Exception:  # noqa: BLE001
            cleanup, exit_code = "unreaped", None
    if cleanup == "unreaped" and code == "ok":
        code = "cleanup_failed"          # fail closed: a tree we could not reap is not healthy
    elif code == "ok" and exit_code not in (None, 0):
        code = "nonzero_exit"            # the server exited by itself with an error status
    status = "pass" if transport == "pass" and code == "ok" else "fail"
    return _result(status, code, transport, tools, checks, time.monotonic() - started, cleanup)


# ------------------------------------------------------------------ CLI sign-in status

def _first_json_object(text):
    start = text.find("{")
    if start < 0:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(text[start:])
    except ValueError:
        return None
    return value


def classify_auth(name, text, exit_code):
    """-> (status, code) from CLI status output; the text itself is never kept."""
    claimed = None  # what the output says; only a successful exit may turn True into signed_in
    if name == "claude":
        value = _first_json_object(text)
        if isinstance(value, dict):
            flag = value.get("loggedIn", value.get("logged_in"))
            if isinstance(flag, bool):
                claimed = flag
    else:
        lowered = text.lower()
        if "not logged in" in lowered or "not signed in" in lowered:
            claimed = False
        elif "logged in" in lowered:
            claimed = True
    if claimed is False:
        return "not_signed_in", "ok"
    if exit_code is None:
        return "error", "did_not_exit"
    if exit_code != 0:
        return "error", "nonzero_exit"
    if claimed is True:
        return "signed_in", "ok"
    return "unknown", "unparsed_output"


def _auth_result(status, code, cleanup="not_started"):
    return {"status": status, "code": code, "cleanup": cleanup}


def check_cli_auth(name, argv_prefix, timeout):
    """Run the model-free sign-in status command with bounded time and output. Never raises."""
    try:
        return _check_cli_auth(name, argv_prefix, timeout)
    except Exception:  # noqa: BLE001
        return _auth_result("error", "probe_error")


def _check_cli_auth(name, argv_prefix, timeout):
    executable, tail = CLI_AUTH_COMMANDS[name]
    if argv_prefix:
        prefix = list(argv_prefix)
    else:
        found = shutil.which(executable)
        prefix = [found] if found else []
    if not prefix or not (os.path.isfile(prefix[0]) or shutil.which(prefix[0])):
        return _auth_result("unknown", "executable_missing")
    owned = OwnedProcess(prefix + tail, env=dict(os.environ), cwd=None,
                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        owned.start()
    except ProbeError:
        return _auth_result("error", "launch_failed")
    box = {"data": b""}

    def pump():
        try:
            box["data"] = owned.proc.stdout.read(MAX_CLI_OUTPUT + 1)
        except (OSError, ValueError):
            pass

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    end = time.monotonic() + timeout
    while reader.is_alive() and time.monotonic() < end:
        reader.join(0.1)
        if owned.proc.poll() is not None:  # the command is done; a descendant may hold the pipe
            reader.join(1.0)
            break
    timed_out = reader.is_alive() and owned.proc.poll() is None
    overflow = (not reader.is_alive()) and len(box["data"]) > MAX_CLI_OUTPUT
    outcome, exit_code = owned.shutdown(0.0 if (timed_out or overflow) else min(2.0, timeout))
    reader.join(timeout=2)
    if not reader.is_alive():
        try:
            owned.proc.stdout.close()
        except (OSError, ValueError):
            pass
    if outcome == "unreaped":
        return _auth_result("error", "cleanup_failed", outcome)
    if timed_out:
        return _auth_result("error", "timeout", outcome)
    if overflow or len(box["data"]) > MAX_CLI_OUTPUT:
        return _auth_result("error", "output_too_large", outcome)
    status, code = classify_auth(name, box["data"].decode("utf-8", "replace"), exit_code)
    return _auth_result(status, code, outcome)


# ------------------------------------------------------------------ configuration

def load_servers(path):
    if tomllib is None:
        raise ConfigError("python_too_old")
    try:
        if os.path.getsize(path) > MAX_CONFIG_BYTES:
            raise ConfigError("config_invalid")
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        raise ConfigError("config_missing") from None
    except ConfigError:
        raise
    except OSError:
        raise ConfigError("config_unreadable") from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, ValueError):
        raise ConfigError("config_invalid") from None
    servers = data.get("mcp_servers")
    if not isinstance(servers, dict):
        raise ConfigError("config_invalid")
    return servers


def resolve_launch(entry):
    """-> (launch, None) or (None, code). Verifies the command exists; never runs it."""
    if not isinstance(entry, dict):
        return None, "config_invalid"
    command, args, env = entry.get("command"), entry.get("args", []), entry.get("env", {})
    if not (isinstance(command, str) and command and isinstance(args, list)
            and all(isinstance(a, str) for a in args) and isinstance(env, dict)
            and all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())):
        return None, "config_invalid"
    if os.path.isabs(command):
        resolved = command if os.path.isfile(command) else None
    else:
        resolved = shutil.which(command)
    if not resolved:
        return None, "command_missing"
    return {"command": resolved, "args": list(args), "env": dict(env)}, None


def probe_registrations(root, servers, timeout):
    """Probe every named registration; equivalent launches run once and report every alias."""
    results, groups = {}, {}
    for name in REGISTRATIONS:
        if name not in servers:
            results[name] = _result("fail", "registration_missing")
            continue
        launch, code = resolve_launch(servers[name])
        if launch is None:
            results[name] = _result("fail", code)
            continue
        key = (os.path.normcase(os.path.abspath(launch["command"])), tuple(launch["args"]),
               tuple(sorted(launch["env"].items())))
        groups.setdefault(key, {"launch": launch, "names": []})["names"].append(name)

    def run(group):
        expected = frozenset().union(*(REGISTRATIONS[n] for n in group["names"]))
        calls = []
        for n in group["names"]:
            calls.extend(c for c in READ_ONLY_CALLS.get(n, ()) if c not in calls)
        return probe_launch(group["launch"], expected, calls, root, timeout)

    if groups:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(groups))) as pool:
            futures = {pool.submit(run, g): g for g in groups.values()}
            for future in concurrent.futures.as_completed(futures):
                group = futures[future]
                try:
                    outcome = future.result()
                except Exception:  # noqa: BLE001
                    outcome = _result("fail", "probe_error")
                first = group["names"][0]
                for name in group["names"]:
                    entry = dict(outcome)
                    entry["same_launch_as"] = None if name == first else first
                    results[name] = entry
    for entry in results.values():
        entry.setdefault("same_launch_as", None)
    return {name: results[name] for name in REGISTRATIONS}, len(groups)


# ------------------------------------------------------------------ dashboard identification

class _DashboardError(Exception):
    pass


def _http_get(port, path, limit, deadline):
    """One GET with a hard wall-clock deadline: a timer closes our own socket at the deadline,
    so dripped headers or body can never outlast it (per-recv timeouts alone would)."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _DashboardError("deadline")
    sock = socket.create_connection((LOOPBACK, port), timeout=max(0.05, min(HTTP_STEP_TIMEOUT, remaining)))
    fired = threading.Event()

    def expire():
        fired.set()
        for action in (lambda: sock.shutdown(socket.SHUT_RDWR), sock.close):
            try:
                action()
            except OSError:
                pass

    timer = threading.Timer(max(0.0, deadline - time.monotonic()), expire)
    timer.daemon = True
    timer.start()
    conn = http.client.HTTPConnection(LOOPBACK, port)
    conn.sock = sock
    try:
        sock.settimeout(HTTP_STEP_TIMEOUT)
        conn.putrequest("GET", path, skip_host=True, skip_accept_encoding=True)
        conn.putheader("Host", "%s:%d" % (LOOPBACK, port))
        conn.endheaders()
        response = conn.getresponse()
        chunks, total = [], 0
        while True:
            chunk = response.read(16384)
            if not chunk:
                break
            total += len(chunk)
            if total > limit or time.monotonic() > deadline:
                raise _DashboardError("too_large_or_slow")
            chunks.append(chunk)
        if fired.is_set():
            raise _DashboardError("deadline")
        return response.status, {k.lower(): v for k, v in response.getheaders()}, b"".join(chunks)
    except (OSError, http.client.HTTPException):
        if fired.is_set():
            raise _DashboardError("deadline") from None
        raise
    finally:
        timer.cancel()
        try:
            conn.close()
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass


def _dashboard_result(port, state, code, telemetry=None):
    return {"port": port, "url": "http://%s:%d/" % (LOOPBACK, port), "state": state,
            "code": code, "telemetry": telemetry}


def check_dashboard(port, budget=DASHBOARD_BUDGET):
    """Identify the actual OBS-01 dashboard with bounded GETs: root, assets, snapshot shape.

    State: absent (nothing listening), healthy (identified OBS-01) or occupied_unhealthy
    (something answers or holds the port but is not a healthy OBS-01). Never kills anything.
    The whole check, including every wait, ends within `budget` seconds.
    """
    deadline = time.monotonic() + budget
    try:
        sock = socket.create_connection((LOOPBACK, port),
                                        timeout=max(0.05, min(CONNECT_TIMEOUT, budget)))
        sock.close()
    except ConnectionRefusedError:
        return _dashboard_result(port, "absent", "no_listener")
    except OSError:
        return _dashboard_result(port, "occupied_unhealthy", "connect_failed")

    def get(path, limit):
        return _http_get(port, path, limit, deadline)

    unhealthy = "occupied_unhealthy"
    try:
        status, headers, body = get("/", MAX_HTML)
        if (status != 200 or b"Agent observability (OBS-01)" not in body
                or b"/static/app.js" not in body):
            return _dashboard_result(port, unhealthy, "not_obs01")
        for path, kind in (("/static/app.js", "text/javascript"), ("/static/style.css", "text/css")):
            status, asset_headers, body = get(path, MAX_ASSET)
            if status != 200 or not body or not asset_headers.get("content-type", "").startswith(kind):
                return _dashboard_result(port, unhealthy, "assets_invalid")
        status, snap_headers, body = get("/api/snapshot", MAX_SNAPSHOT)
        if (status != 200 or snap_headers.get("x-content-type-options") != "nosniff"
                or "default-src 'none'" not in snap_headers.get("content-security-policy", "")
                or not snap_headers.get("content-type", "").startswith("application/json")):
            return _dashboard_result(port, unhealthy, "snapshot_invalid")
        snapshot = json.loads(body.decode("utf-8"))
    except _DashboardError:
        return _dashboard_result(port, unhealthy, "timeout")
    except (OSError, http.client.HTTPException, ValueError):
        return _dashboard_result(port, unhealthy, "not_http")
    source = snapshot.get("source") if isinstance(snapshot, dict) else None
    if not (isinstance(snapshot, dict) and snapshot.get("schema") == "obs-01/1"
            and isinstance(source, dict) and isinstance(source.get("complete"), bool)
            and isinstance(snapshot.get("summary"), dict) and isinstance(snapshot.get("notices"), list)
            and isinstance(snapshot.get("calls"), list)):
        return _dashboard_result(port, unhealthy, "snapshot_invalid")
    reasons = source.get("incomplete_reasons")
    reasons = [r for r in reasons if isinstance(r, str) and re.fullmatch(r"[a-z0-9_]{1,64}", r)][:20] \
        if isinstance(reasons, list) else []
    return _dashboard_result(port, "healthy", "ok",
                             {"complete": source["complete"], "reasons": reasons})


def wait_for_dashboard(port, seconds, abort=None):
    """Poll until healthy or `seconds` pass (hard wall-clock bound). `abort()` may end it early."""
    end = time.monotonic() + seconds
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return check_dashboard(port, 0.5)
        result = check_dashboard(port, min(DASHBOARD_BUDGET, remaining))
        if result["state"] == "healthy" or time.monotonic() >= end or (abort and abort()):
            return result
        time.sleep(min(0.3, max(0.0, end - time.monotonic())))


def _terminate_own_tree(proc):
    """Stop a process this helper itself spawned (never anything pre-existing)."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                           timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
        proc.wait(timeout=KILL_WAIT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        pass


def start_dashboard(port, script, root, runtime_dir, state_dir, wait):
    """Reuse the identified OBS-01 or start it detached; refuse any other occupant.

    The dashboard gets stdin/stdout from DEVNULL, a plain log file for stderr, no inherited
    handles (close_fds) and no console window, so it can outlive this helper and whatever
    captured the caller's output pipes. Returns {"action": ..., "pid": int|None}.
    """
    first = check_dashboard(port)
    if first["state"] == "healthy":
        return {"action": "reused", "pid": None}
    if first["state"] != "absent":
        return {"action": "refused_port_occupied", "pid": None}
    if not os.path.isfile(script):
        return {"action": "script_missing", "pid": None}
    argv = [sys.executable, "-B", "-I", script, "--port", str(port)]
    if state_dir:
        argv += ["--state-dir", state_dir]
    log = None
    try:
        os.makedirs(runtime_dir, exist_ok=True)
        log = open(os.path.join(runtime_dir, "dashboard.err.log"), "wb")  # a file, never a pipe
    except OSError:
        log = None
    proc = None
    try:
        flags_options = [0]
        if os.name == "nt":
            base = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            # Leave any caller job if allowed; fall back when the job forbids breakaway.
            flags_options = [base | 0x01000000, base]
        for flags in flags_options:
            kwargs = {"creationflags": flags} if os.name == "nt" else {"start_new_session": True}
            try:
                proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=log if log is not None else subprocess.DEVNULL,
                                        close_fds=True, cwd=root, **kwargs)
                break
            except (OSError, ValueError):
                continue
    finally:
        if log is not None:
            log.close()
    if proc is None:
        return {"action": "start_failed", "pid": None}

    after = wait_for_dashboard(port, wait, abort=lambda: proc.poll() is not None)
    exited = proc.poll() is not None
    if after["state"] == "healthy":
        if exited:
            return {"action": "reused", "pid": None}  # another run won the port
        try:
            record = {"pid": proc.pid, "port": port, "started_at": _utc_now()}
            temp = os.path.join(runtime_dir, "dashboard.json.tmp")
            with open(temp, "w", encoding="ascii") as fh:
                json.dump(record, fh)
            os.replace(temp, os.path.join(runtime_dir, "dashboard.json"))
        except OSError:
            pass
        return {"action": "started", "pid": proc.pid}
    if exited:
        return {"action": "start_failed", "pid": None}
    _terminate_own_tree(proc)
    return {"action": "start_unhealthy", "pid": None}


# ------------------------------------------------------------------ report

def cli_executables():
    return {name: ("found" if shutil.which(exe) else "missing")
            for name, (exe, _) in CLI_AUTH_COMMANDS.items()}


def collect_checks(args, servers):
    """Run MCP probes and CLI sign-in checks concurrently; return (registrations, launches, auth)."""
    prefixes = {"claude": args.claude_argv, "codex": args.codex_argv}
    auth, pending = {}, {}
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=len(CLI_AUTH_COMMANDS))
    try:
        for name in CLI_AUTH_COMMANDS:
            if args.cli_auth == "skip":
                auth[name] = _auth_result("skipped", "skipped_by_option")
            else:
                pending[name] = pool.submit(check_cli_auth, name, prefixes[name], args.cli_timeout)
        registrations, launches = {}, 0
        if servers is not None:
            registrations, launches = probe_registrations(args.root, servers, args.timeout)
        for name, future in pending.items():
            auth[name] = future.result()
    finally:
        pool.shutdown(wait=True)
    return registrations, launches, auth


def build_report(args, servers, config_code):
    registrations, launches, auth = collect_checks(args, servers)
    transport_status = "not_run"
    if servers is not None:
        transport_status = "pass" if all(r["status"] == "pass" for r in registrations.values()) else "fail"
    dash = check_dashboard(args.dashboard_port)
    dash["action"] = args.dashboard_action
    dash["started_pid"] = args.dashboard_pid or None
    executables = cli_executables()

    failures, warnings = [], []
    if config_code:
        failures.append("config:" + config_code)
    for name, entry in registrations.items():
        if entry["status"] != "pass":
            failures.append("mcp:%s:%s" % (name, entry["code"]))
        if entry["cleanup"] == "unreaped" and entry["code"] != "cleanup_failed":
            failures.append("mcp:%s:cleanup_unreaped" % name)
    if dash["state"] == "absent":
        failures.append("dashboard:not_running")
    elif dash["state"] != "healthy":
        failures.append("dashboard:" + dash["code"])
    if args.dashboard_action in DASHBOARD_ACTION_FAILURES:
        failures.append("dashboard:" + args.dashboard_action)
    if args.app_status in APP_FAILURES:
        failures.append("app:" + args.app_status)
    if dash["telemetry"] and not dash["telemetry"]["complete"]:
        warnings.append("dashboard_telemetry_incomplete")
    sign_in_unknown = False
    for name, result in auth.items():
        if result["status"] == "signed_in":
            continue
        if result["status"] == "skipped":  # explicit fixture-only option: unverified, not failed
            sign_in_unknown = True
            warnings.append("cli_auth:%s:%s" % (name, result["code"]))
            continue
        if result["status"] != "not_signed_in":
            sign_in_unknown = True  # an attempted check that did not establish sign-in
        failures.append("cli_auth:%s:%s" % (name, "not_signed_in" if result["status"] == "not_signed_in"
                                            else result["code"]))
    checkout = args.app_checkout
    if checkout == "manual_action_required":
        warnings.append("open_checkout_in_codex_manually")

    not_verified = ["native_app_tool_connection", "cold_reboot_persistence"]
    if sign_in_unknown:
        not_verified.insert(1, "cli_sign_in")
    verdict = "fail" if failures else "pass_native_unverified"
    exit_code = 2 if config_code else (1 if failures else 0)
    return {
        "schema": SCHEMA,
        "generated_at": _utc_now(),
        "mode": args.mode,
        "verdict": verdict,
        "exit_code": exit_code,
        "failures": failures,
        "warnings": warnings,
        "app": {"status": args.app_status, "checkout": checkout,
                "native_tool_connection": "unverified"},
        "dashboard": dash,
        "mcp_transport": {
            "status": transport_status,
            "scope": "direct temporary stdio clients; initialize, tools/list and read-only checks; "
                     "no model calls",
            "launches": launches,
            "registrations": registrations,
        },
        "cli": {"executables": executables, "auth": auth},
        "not_verified": not_verified,
    }


def render_human(report):
    out = []
    add = out.append
    add("START-01 agent stack report (%s, %s)" % (report["schema"], report["generated_at"]))
    add("Mode:       %s" % report["mode"])
    add("Result:     %s" % VERDICT_TEXT[report["verdict"]])
    app = report["app"]
    add("Codex app:  %s; native tool connection: UNVERIFIED (not observed by this check)"
        % app["status"])
    if app["checkout"] == "manual_action_required":
        add("            Open the trusted checkout in Codex yourself if it is not already open.")
    dash = report["dashboard"]
    add("Dashboard:  %s (%s); action: %s" % (dash["state"], dash["code"], dash["action"]))
    add("            %s" % dash["url"])
    tele = dash["telemetry"]
    if tele is not None:
        if tele["complete"]:
            add("            telemetry: complete")
        else:
            add("            telemetry: INCOMPLETE (%s) - the server is live; the data is not complete"
                % (", ".join(tele["reasons"]) or "no reason given"))
    transport = report["mcp_transport"]
    add("MCP transport (direct probes, no model calls): %s [%d distinct launch(es)]"
        % (transport["status"], transport["launches"]))
    for name, entry in transport["registrations"].items():
        extra = []
        if entry["same_launch_as"]:
            extra.append("same launch as %s" % entry["same_launch_as"])
        if entry["tools"] and entry["tools"]["missing"]:
            extra.append("missing tools: %s" % ", ".join(entry["tools"]["missing"]))
        for check, outcome in sorted(entry["read_only_checks"].items()):
            extra.append("%s=%s" % (check, outcome))
        if entry["cleanup"] in ("killed", "descendants_reaped", "unreaped"):
            extra.append("probe cleanup: %s" % entry["cleanup"])
        add("  %-20s %-4s %-22s %s" % (name, entry["status"].upper(), entry["code"], "; ".join(extra)))
    cli = report["cli"]
    add("CLI sign-in (status commands, no login attempted):")
    for name in sorted(cli["auth"]):
        result = cli["auth"][name]
        add("  %-20s %-14s %s [executable %s]" % (name, result["status"], result["code"],
                                                 cli["executables"].get(name, "unknown")))
    if report["warnings"]:
        add("Warnings:   " + ", ".join(report["warnings"]))
    if report["failures"]:
        add("Failures:   " + ", ".join(report["failures"]))
    add("Not verified: " + ", ".join(report["not_verified"]))
    return "\n".join(out)


def write_report_file(path, report):
    try:
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        temp = path + ".tmp"
        with open(temp, "w", encoding="ascii") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=True)
        os.replace(temp, path)
        return True
    except OSError:
        return False


# ------------------------------------------------------------------ commands

def cmd_health(args):
    servers, config_code = None, None
    if not os.path.isdir(args.root):
        config_code = "root_missing"
    else:
        try:
            servers = load_servers(args.config)
        except ConfigError as exc:
            config_code = exc.code
    report = build_report(args, servers, config_code)
    if args.report_file and not write_report_file(args.report_file, report):
        report["warnings"].append("report_file_unwritable")
    if args.format in ("human", "both"):
        print(render_human(report))
    if args.format == "both":
        print("--- JSON summary ---")
    if args.format in ("json", "both"):
        print(json.dumps(report, indent=2, ensure_ascii=True))
    return report["exit_code"]


def cmd_dashboard_check(args):
    result = wait_for_dashboard(args.port, args.wait) if args.wait > 0 else check_dashboard(args.port)
    print(json.dumps(result, ensure_ascii=True))
    return 0


def cmd_dashboard_start(args):
    runtime = args.runtime_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".runtime")
    result = start_dashboard(args.port, args.script, args.root, runtime, args.state_dir, args.wait)
    print(json.dumps(result, ensure_ascii=True))
    return 0


def _argv_prefix(text):
    try:
        value = json.loads(text)
    except ValueError:
        raise argparse.ArgumentTypeError("expected a JSON list of strings") from None
    if not (isinstance(value, list) and value and all(isinstance(v, str) for v in value)):
        raise argparse.ArgumentTypeError("expected a JSON list of strings")
    return value


def build_parser():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.getcwd()
    parser = argparse.ArgumentParser(description="START-01 model-free startup health checks.")
    sub = parser.add_subparsers(dest="command", required=True)

    health = sub.add_parser("health", help="probe MCP registrations, CLI sign-in and the dashboard")
    health.add_argument("--root", default=root, help="trusted checkout (probe working directory)")
    health.add_argument("--config", default=None, help="Codex config (default <root>/.codex/config.toml)")
    health.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help="seconds allowed for each distinct launch (default %d)" % DEFAULT_TIMEOUT)
    health.add_argument("--dashboard-port", type=int, default=DEFAULT_PORT)
    health.add_argument("--mode", choices=("startup", "health_only", "skip_app_launch"), default="startup")
    health.add_argument("--app-status", choices=APP_STATUSES, default="not_checked")
    health.add_argument("--app-checkout", choices=("manual_action_required", "not_applicable"),
                        default="not_applicable")
    health.add_argument("--dashboard-action", choices=DASHBOARD_ACTIONS, default="not_checked")
    health.add_argument("--dashboard-pid", type=int, default=0)
    health.add_argument("--cli-auth", choices=("check", "skip"), default="check",
                        help="run 'claude auth status --json' and 'codex login status' (default) "
                             "or skip them (fixtures)")
    health.add_argument("--cli-timeout", type=float, default=DEFAULT_CLI_TIMEOUT)
    health.add_argument("--claude-argv", type=_argv_prefix, default=None,
                        help="fixtures only: JSON argv prefix replacing the claude executable")
    health.add_argument("--codex-argv", type=_argv_prefix, default=None,
                        help="fixtures only: JSON argv prefix replacing the codex executable")
    health.add_argument("--format", choices=("human", "json", "both"), default="both")
    health.add_argument("--report-file", default=None)
    health.set_defaults(func=cmd_health)

    check = sub.add_parser("dashboard-check", help="identify what answers on the dashboard port")
    check.add_argument("--port", type=int, default=DEFAULT_PORT)
    check.add_argument("--wait", type=float, default=0.0, help="poll up to this many seconds")
    check.set_defaults(func=cmd_dashboard_check)

    start = sub.add_parser("dashboard-start", help="start (detached, hidden) or reuse the dashboard")
    start.add_argument("--port", type=int, default=DEFAULT_PORT)
    start.add_argument("--script", default=os.path.abspath(os.path.join(here, "..", "agent-observability", "dashboard.py")))
    start.add_argument("--root", default=root)
    start.add_argument("--runtime-dir", default=None)
    start.add_argument("--state-dir", default=None)
    start.add_argument("--wait", type=float, default=DASHBOARD_START_WAIT)
    start.set_defaults(func=cmd_dashboard_start)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "health":
        args.root = os.path.abspath(args.root)
        args.config = args.config or os.path.join(args.root, ".codex", "config.toml")
        args.timeout = min(max(args.timeout, 1.0), 300.0)
        args.cli_timeout = min(max(args.cli_timeout, 1.0), 120.0)
    port = args.dashboard_port if args.command == "health" else args.port
    if not 1 <= port <= 65535:
        parser.error("port must be 1-65535")
    try:
        return args.func(args)
    except Exception:  # noqa: BLE001 - never print a traceback or raw error text
        sys.stdout.write("internal_error\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
