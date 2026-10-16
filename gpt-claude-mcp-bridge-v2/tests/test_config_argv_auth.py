"""Routing/validation, read-only argv and authentication checks (no inference)."""

from __future__ import annotations

import json
import tempfile
import unittest

from tests.helpers import FIXTURES, WorldCase

from bridge import codexcli, config, errors


class RoutePrecedence(unittest.TestCase):
    def test_defaults(self):
        for tool in config.TOOL_NAMES:
            r = config.resolve_route(tool, {})
            self.assertEqual((r.model, r.effort), ("gpt-6.1-sol", "high"))
            self.assertEqual((r.model_source, r.effort_source), ("default", "default"))

    def test_v1_env_vars_still_apply_to_both_tools(self):
        env = {"GPT_ARCHITECT_MODEL": "gpt-6-astra", "GPT_ARCHITECT_EFFORT": "xhigh"}
        for tool in config.TOOL_NAMES:
            r = config.resolve_route(tool, env)
            self.assertEqual((r.model, r.effort), ("gpt-6-astra", "xhigh"))
            self.assertEqual(r.model_source, "GPT_ARCHITECT_MODEL")

    def test_per_tool_override_wins_and_is_independent(self):
        env = {
            "GPT_ARCHITECT_MODEL": "gpt-6.1-sol",
            "GPT_ARCHITECT_EFFORT": "high",
            "GPT_ARCHITECT_MODEL_REVIEW": "gpt-6-astra",
            "GPT_ARCHITECT_EFFORT_CONSULT": "low",
        }
        c = config.resolve_route(config.CONSULT, env)
        r = config.resolve_route(config.REVIEW, env)
        self.assertEqual((c.model, c.effort), ("gpt-6.1-sol", "low"))
        self.assertEqual((r.model, r.effort), ("gpt-6-astra", "high"))
        self.assertEqual(c.effort_source, "GPT_ARCHITECT_EFFORT_CONSULT")
        self.assertEqual(r.model_source, "GPT_ARCHITECT_MODEL_REVIEW")

    def test_empty_value_counts_as_unset(self):
        r = config.resolve_route(config.CONSULT, {"GPT_ARCHITECT_MODEL_CONSULT": "  ", "GPT_ARCHITECT_MODEL": "m1"})
        self.assertEqual(r.model, "m1")

    def test_rejects_unsafe_model_and_effort_strings(self):
        for bad in ("--dangerously-bypass-approvals-and-sandbox", "a b", "x;y", '"q"', "-m", "a" * 80):
            with self.assertRaises(errors.BridgeError) as cm:
                config.resolve_route(config.CONSULT, {"GPT_ARCHITECT_MODEL": bad})
            self.assertEqual(cm.exception.category, errors.INVALID_CONFIGURATION)
        with self.assertRaises(errors.BridgeError):
            config.resolve_route(config.REVIEW, {"GPT_ARCHITECT_EFFORT_REVIEW": "HIGH; rm"})

    def test_catalog_validation_rejects_unknown_model_and_unsupported_effort(self):
        catalog = {"gpt-6.1-sol": ["low", "high"], "gpt-5.5": ["low", "medium", "high", "xhigh"]}
        ok = config.Route(config.CONSULT, "gpt-6.1-sol", "high", "default", "default")
        config.validate_route_against_catalog(ok, catalog)
        with self.assertRaises(errors.BridgeError) as cm:
            config.validate_route_against_catalog(
                config.Route(config.CONSULT, "nope", "high", "GPT_ARCHITECT_MODEL", "default"), catalog)
        self.assertEqual(cm.exception.code, "unknown_model")
        with self.assertRaises(errors.BridgeError) as cm:
            config.validate_route_against_catalog(
                config.Route(config.REVIEW, "gpt-6.1-sol", "ultra", "default", "GPT_ARCHITECT_EFFORT_REVIEW"), catalog)
        self.assertEqual(cm.exception.code, "unsupported_effort")
        self.assertIn("never escalates", str(cm.exception))


class SettingsValidation(unittest.TestCase):
    def test_defaults_and_bounds(self):
        s = config.load_settings({"USERPROFILE": "C:\\h"})
        self.assertEqual(s.timeout_s, 900)
        self.assertEqual(s.queue_timeout_s, 960)
        self.assertEqual(s.session, "default")
        self.assertFalse(s.allow_untested_cli)
        for env in ({"GPT_ARCHITECT_TIMEOUT": "abc"}, {"GPT_ARCHITECT_TIMEOUT": "0"},
                    {"GPT_ARCHITECT_SESSION": "../x"}, {"GPT_ARCHITECT_MCP_ALLOW": "a b"},
                    {"GPT_ARCHITECT_ALLOW_UNTESTED_CLI": "yes"}):
            with self.assertRaises(errors.BridgeError):
                config.load_settings(env)


class ReadOnlyArgv(unittest.TestCase):
    cli = codexcli.Cli(("node", "codex.js"), "node-script", "node codex.js")
    audit = codexcli.Audit(
        disable_features=["hooks", "plugins"],
        server_overrides=["-c", "mcp_servers.idea.enabled=false"],
    )

    def build(self, resume=None, model="gpt-6.1-sol", effort="high"):
        return codexcli.build_argv(self.cli, model=model, effort=effort, output_file="C:\\t\\a.txt",
                                   audit=self.audit, resume_thread=resume)

    def test_fresh_argv_exact(self):
        self.assertEqual(self.build(), [
            "node", "codex.js", "exec", "--json", "--sandbox", "read-only", "--model", "gpt-6.1-sol",
            "-c", 'sandbox_mode="read-only"', "-c", 'approval_policy="never"',
            "-c", 'model_reasoning_effort="high"',
            "--disable", "hooks", "--disable", "plugins", "-c", "mcp_servers.idea.enabled=false",
            "--output-last-message", "C:\\t\\a.txt", "-",
        ])

    def test_resume_argv_pins_read_only_model_and_effort(self):
        argv = self.build(resume="11111111-2222-3333-4444-555555555555", model="gpt-6-astra", effort="xhigh")
        self.assertEqual(argv[2:5], ["exec", "resume", "11111111-2222-3333-4444-555555555555"])
        self.assertIn("--model", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "gpt-6-astra")
        self.assertIn('sandbox_mode="read-only"', argv)
        self.assertIn('approval_policy="never"', argv)
        self.assertIn('model_reasoning_effort="xhigh"', argv)
        self.assertNotIn("--sandbox", argv)  # resume has no such flag; enforced through -c

    def test_windows_sandbox_override_is_opt_in_and_validated(self):
        self.assertNotIn("windows.sandbox", " ".join(self.build()))
        argv = codexcli.build_argv(self.cli, model="m", effort="high", output_file="f", audit=self.audit,
                                   resume_thread=None, windows_sandbox="unelevated")
        self.assertIn('windows.sandbox="unelevated"', argv)
        codexcli.assert_read_only_argv(argv, skip=2)
        self.assertEqual(config.load_settings({"GPT_ARCHITECT_WINDOWS_SANDBOX": "unelevated"}).windows_sandbox,
                         "unelevated")
        with tempfile.TemporaryDirectory() as isolated_settings:
            self.assertIsNone(config.load_settings({"GPT_ARCHITECT_V2_STATE_DIR": isolated_settings}).windows_sandbox)
        with self.assertRaises(errors.BridgeError):
            config.load_settings({"GPT_ARCHITECT_WINDOWS_SANDBOX": "off"})

    def test_never_contains_bypass_flags(self):
        for resume in (None, "11111111-2222-3333-4444-555555555555"):
            joined = " ".join(self.build(resume=resume))
            for token in ("dangerously", "danger-full-access", "workspace-write", "--full-auto", "--yolo"):
                self.assertNotIn(token, joined)

    def test_guard_rejects_bypass_and_missing_policy(self):
        argv = self.build()
        with self.assertRaises(errors.BridgeError):
            codexcli.assert_read_only_argv([*argv, "--dangerously-bypass-approvals-and-sandbox"], skip=2)
        with self.assertRaises(errors.BridgeError):
            codexcli.assert_read_only_argv([a for a in argv if "approval_policy" not in a], skip=2)

    def test_v1_fresh_flags_are_preserved_in_order(self):
        v1 = json.loads((FIXTURES / "v1_subprocess_behavior.json").read_text(encoding="utf-8"))
        v1_fresh = v1["scenarios"][0]["calls"][0]["argv"][1:-3]  # drop launcher, -o FILE, '-'
        v2 = self.build()
        position = -1
        for token in v1_fresh:
            self.assertIn(token, v2[position + 1:], f"v1 token {token!r} lost or reordered")
            position = v2.index(token, position + 1)


class Authentication(WorldCase):
    def test_classification(self):
        c = codexcli.classify_auth
        self.assertEqual(c(0, "Logged in using ChatGPT"), "chatgpt")
        self.assertEqual(c(1, "Not logged in"), "missing")
        self.assertEqual(c(0, "Logged in using an API key - sk-xxxx"), "api_key")
        self.assertEqual(c(0, "something else"), "unsupported")
        self.assertEqual(c(2, "boom"), "unsupported")

    def _run(self, auth):
        self.w.set(auth=auth)
        svc = self.service()
        with self.assertRaises(errors.BridgeError) as cm:
            svc.execute(config.CONSULT, "q")
        return cm.exception

    def test_missing_api_key_and_unsupported_are_reported_without_inference(self):
        for auth, code in (("none", "auth_missing"), ("api_key", "auth_api_key"), ("weird", "auth_unsupported")):
            self.setUp()
            exc = self._run(auth)
            self.assertEqual((exc.category, exc.code), (errors.AUTHENTICATION, code))
            self.assertFalse(exc.inference_may_have_started)
            self.assertEqual(self.w.exec_entries(), [], "no inference may be attempted")
            self.assertNotIn("sk-fake", str(exc), "raw status output must never be echoed")
            if code != "auth_unsupported":
                self.assertIn("never", str(exc).lower())  # states the no-login/no-fallback contract

    def test_bridge_never_logs_in_or_out(self):
        self.service().execute(config.CONSULT, "q")
        for entry in self.w.log_entries():
            argv = entry["argv"]
            self.assertNotIn("logout", argv)
            if "login" in argv:
                self.assertEqual(argv[argv.index("login") + 1], "status")

    def test_api_key_env_is_scrubbed_from_every_child(self):
        svc = self.service(CODEX_API_KEY="sk-user-key-1", OPENAI_API_KEY="sk-user-key-2")
        svc.execute(config.CONSULT, "q")
        self.assertTrue(self.w.log_entries())
        self.assertFalse(any(e["env_api_key_present"] for e in self.w.log_entries()))

    def test_codex_home_is_preserved(self):
        self.service().execute(config.CONSULT, "q")
        # fake_codex logs cwd/env flags only; CODEX_HOME passthrough is asserted via child_env
        env = codexcli.child_env({"CODEX_HOME": "X", "CODEX_API_KEY": "k", "PATH": "p"})
        self.assertEqual(env, {"CODEX_HOME": "X", "PATH": "p"})


if __name__ == "__main__":
    unittest.main()
