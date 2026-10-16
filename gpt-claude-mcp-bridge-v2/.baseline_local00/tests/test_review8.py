"""Tests for the Desktop-coexistence diagnosis (the sandbox setup-refresh lock): the bridge must say what
the Codex sandbox log ACTUALLY shows, never infer a node_repl lock from process presence alone.

Mocked CLI and a disposable CODEX_HOME only: no model, no real sandbox.  Self-contained (it imports only the
harness) so `tools/prefix_check.py` can run it against the previous source.
"""

from __future__ import annotations

import asyncio
import json
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from bridge import codexcli, config, errors
from tests.helpers import FIXTURES, WorldCase

CONSULT, REVIEW = config.CONSULT, config.REVIEW
HELPER = "Failed to create unified exec process: helper_unknown_error: setup refresh had errors"
HOLDERS = [36004, 21896]


def stamp(delta_s: float = 0.0) -> str:
    """The sandbox log's UTC ISO timestamp form (nine fractional digits)."""
    when = datetime.now(timezone.utc) + timedelta(seconds=delta_s)
    return when.strftime("%Y-%m-%dT%H:%M:%S.%f") + "000+00:00"


def cmd_item(output, status="failed", exit_code=1, cwd=None):
    item = {"id": "i1", "type": "command_execution", "command": "x", "aggregated_output": output,
            "exit_code": exit_code, "status": status}
    if cwd:
        item["cwd"] = cwd
    return {"type": "item.completed", "item": item}


class Base(WorldCase):
    def log_dir(self) -> Path:
        d = self.w.codex_home / ".sandbox"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_log(self, *lines: str, name: str = "sandbox.2026-10-13.log") -> Path:
        path = self.log_dir() / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def refresh_failure(self, target: str, message: str, delta_s: float = 0.0) -> list[str]:
        ts = stamp(delta_s)
        reason = f"runtime read/execute validation failed: validate runtime read/execute access on {target}: {message}"
        return [
            f"[{ts}] {reason}",
            f'[{ts}] setup refresh: processed 0 write roots (read roots delegated); errors=["{reason}"]',
            f"[{ts}] setup error: setup refresh had errors",
        ]

    def lock_target(self) -> str:
        return str(self.w.home / "AppData" / "Local" / "OpenAI" / "Codex" / "runtimes" / "cua_node"
                   / "cfb32733c877621e" / "bin" / "node_repl.exe")

    def in_use(self) -> str:
        return ("open ACL target for root-only update: The process cannot access the file because it is "
                "being used by another process. (os error 32)")

    def expect(self, svc, tool=CONSULT, text="q") -> errors.BridgeError:
        with self.assertRaises(errors.BridgeError) as cm:
            svc.execute(tool, text)
        return cm.exception

    def holders(self, pids=HOLDERS):
        return mock.patch.object(codexcli, "running_lock_holders", lambda: list(pids))


class D1_LogConfirmedLock(Base):
    def test_the_named_file_and_reason_come_from_the_log_not_from_process_presence(self):
        self.w.set(sandbox="setup_failed")
        self.write_log(*self.refresh_failure(self.lock_target(), self.in_use()))
        with self.holders():
            exc = self.expect(self.service())
        text = str(exc)
        self.assertEqual((exc.category, exc.code), (errors.SANDBOX_UNAVAILABLE, "sandbox_setup_failed"))
        self.assertIn("node_repl.exe", text)
        self.assertIn("in use by another process", text)
        self.assertIn("sandbox log", text)
        self.assertNotIn("UNCONFIRMED", text)
        self.assertIn("No model call was made", text)
        self.assertIn(str(HOLDERS[0]), text)

    def test_diagnostics_are_sanitised(self):
        self.w.set(sandbox="setup_failed")
        self.write_log(*self.refresh_failure(self.lock_target(), self.in_use()))
        with self.holders():
            exc = self.expect(self.service())
        text = str(exc)
        self.assertNotIn(str(self.w.home), text, "the profile path must be shortened")
        self.assertNotIn("\x00", text)
        self.assertLess(len(text), 2500)


class D2_ProcessOnlyIsUnconfirmed(Base):
    def test_node_repl_without_log_evidence_is_reported_as_unconfirmed(self):
        self.w.set(sandbox="setup_failed")
        with self.holders():
            exc = self.expect(self.service())
        text = str(exc)
        self.assertIn("UNCONFIRMED", text)
        self.assertIn(str(HOLDERS[0]), text)
        self.assertNotIn("confirmed from the sandbox log", text)
        self.assertIn("Quit those apps", text, "the conditional operator recovery is kept")
        self.assertIn("No model call was made", text)

    def test_an_old_log_entry_is_not_attributed_to_this_call(self):
        self.w.set(sandbox="setup_failed")
        self.write_log(*self.refresh_failure(self.lock_target(), self.in_use(), delta_s=-3600))
        with self.holders():
            exc = self.expect(self.service())
        text = str(exc)
        self.assertIn("UNCONFIRMED", text)
        self.assertNotIn("in use by another process", text)


class D3_NothingFound(Base):
    def test_no_process_and_no_log_makes_no_lock_claim(self):
        self.w.set(sandbox="setup_failed")
        with self.holders([]):
            exc = self.expect(self.service())
        text = str(exc)
        self.assertNotIn("node_repl.exe is running", text)
        self.assertNotIn("holds its runtime file", text)
        self.assertIn("could not be identified", text)
        self.assertIn("No model call was made", text)

    def test_a_different_logged_failure_is_reported_as_itself(self):
        self.w.set(sandbox="setup_failed")
        other = str(self.w.home / "AppData" / "Local" / "OpenAI" / "Codex" / "bin" / "other.dll")
        self.write_log(*self.refresh_failure(other, "open ACL target for root-only update: Access is denied. "
                                                    "(os error 5)"))
        with self.holders():
            exc = self.expect(self.service())
        text = str(exc)
        self.assertIn("other.dll", text)
        self.assertIn("access was denied", text)
        self.assertIn("nothing ties", text)
        self.assertNotIn("in use by another process", text)


class D4_OtherStagesAndTurnFailures(Base):
    def test_a_repository_read_failure_never_claims_a_lock(self):
        self.w.set(sandbox="repo_unreadable")
        with self.holders():
            exc = self.expect(self.service())
        self.assertEqual(exc.code, "repo_unreadable")
        self.assertNotIn("node_repl", str(exc))

    def test_an_actual_turn_failure_after_a_clean_preflight_names_no_lock(self):
        self.w.set(exec={"events": [{"type": "thread.started", "thread_id": "{thread}"},
                                    cmd_item(HELPER, cwd=Path(self.w.repo).as_uri()),
                                    {"type": "turn.completed"}]})
        with self.holders():
            exc = self.expect(self.service())
        self.assertEqual(exc.category, errors.SANDBOX_UNAVAILABLE)
        self.assertEqual(exc.code, "turn_sandbox_setup_failed")
        self.assertNotIn("node_repl", str(exc))


class D5_NoInferenceNoSessionAdvance(Base):
    def test_a_failed_fresh_call_runs_nothing_and_creates_no_session(self):
        self.w.set(sandbox="setup_failed")
        self.write_log(*self.refresh_failure(self.lock_target(), self.in_use()))
        with self.holders():
            exc = self.expect(self.service())
        self.assertFalse(exc.inference_may_have_started)
        self.assertEqual(self.w.exec_entries(), [], "no inference, no retry")
        self.assertEqual(len(self.w.log_entries("sandbox")), 1, "stopped at the first stage")
        self.assertFalse((self.w.state / "registry.json").exists() and self.w.registry()["sessions"])
        records = self.w.telemetry()
        self.assertEqual(len(records), 1)
        r = records[0]
        self.assertEqual((r["outcome"], r["error_category"], r["error_code"]),
                         ("error", "sandbox_unavailable", "sandbox_setup_failed"))
        self.assertFalse(r["inference_may_have_started"])
        self.assertEqual((r["history"], r["pointer_state"], r["session_state"]),
                         ("no_turn_started", "unchanged", "ready"))

    def test_a_failed_resumed_call_leaves_the_bound_session_alone(self):
        svc = self.service()
        svc.execute(CONSULT, "seed")
        before = self.w.registry()["sessions"]
        execs_before = len(self.w.exec_entries())
        self.w.set(sandbox="setup_failed")
        self.write_log(*self.refresh_failure(self.lock_target(), self.in_use()))
        with self.holders():
            exc = self.expect(self.service(), tool=REVIEW)
        self.assertEqual(exc.code, "sandbox_setup_failed")
        self.assertEqual(len(self.w.exec_entries()), execs_before, "nothing was resumed or retried")
        self.assertEqual(self.w.registry()["sessions"], before)
        rec = self.w.telemetry()[-1]
        self.assertEqual((rec["tool"], rec["error_code"], rec["inference_may_have_started"]),
                         (REVIEW, "sandbox_setup_failed", False))


class D6_McpSchemasUnchanged(unittest.TestCase):
    def test_tool_names_and_single_string_inputs_are_unchanged(self):
        from bridge.server import mcp

        tools = asyncio.run(mcp.list_tools())
        expected = json.loads((FIXTURES / "v1_tool_schemas.json").read_text(encoding="utf-8"))
        got = sorted((t.model_dump(mode="json", exclude_none=True) for t in tools), key=lambda d: d["name"])
        self.assertEqual(got, expected, "names, descriptions and schemas are byte-identical to v1's")
        by_name = {t.name: t for t in tools}
        self.assertEqual(by_name["consult_architect"].inputSchema["required"], ["question"])
        self.assertEqual(by_name["review_implementation"].inputSchema["required"], ["focus"])
        for t in tools:
            self.assertEqual(len(t.inputSchema["properties"]), 1)


if __name__ == "__main__":
    unittest.main()
