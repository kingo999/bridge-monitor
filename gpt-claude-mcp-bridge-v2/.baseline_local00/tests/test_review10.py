"""Tests for the state-directory settings file that carries the Windows sandbox backend override for clients
that cannot add an environment variable to the server (the ChatGPT/Codex desktop app).

Self-contained (harness only) so `tools/prefix_check.py` can run it against the previous source.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from bridge import config, errors
from tests.helpers import WorldCase

CONSULT = config.CONSULT


class S1_SettingsFileSupplyingTheBackend(WorldCase):
    def write(self, text: str | bytes) -> Path:
        self.w.state.mkdir(parents=True, exist_ok=True)
        path = self.w.state / "settings.json"
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
        return path

    def load(self, **extra):
        return config.load_settings(self.w.env(**extra))

    def test_the_file_supplies_the_backend_when_the_environment_does_not(self):
        self.write('{"windows_sandbox": "unelevated"}')
        s = self.load()
        self.assertEqual(s.windows_sandbox, "unelevated")
        self.assertEqual(s.windows_sandbox_source, "settings_file")

    def test_the_environment_variable_wins_over_the_file(self):
        self.write('{"windows_sandbox": "elevated"}')
        s = self.load(GPT_ARCHITECT_WINDOWS_SANDBOX="unelevated")
        self.assertEqual((s.windows_sandbox, s.windows_sandbox_source), ("unelevated", "environment"))

    def test_no_file_and_no_variable_leaves_the_users_codex_config_in_charge(self):
        s = self.load()
        self.assertIsNone(s.windows_sandbox)

    def test_an_invalid_file_refuses_instead_of_being_ignored(self):
        bad = ['{"windows_sandbox": "root"}', '{"windows_sandbox": "unelevated", "extra": 1}',
               '["unelevated"]', 'not json', '{"windows_sandbox": 1}', b'\xff\xfe\x00',
               '{"windows_sandbox": "unelevated"}' + " " * 5000]
        for text in bad:
            self.write(text)
            with self.assertRaises(errors.BridgeError, msg=repr(text)[:60]) as cm:
                self.load()
            self.assertEqual(cm.exception.code, "settings_file_invalid", repr(text)[:60])
            self.assertIn("No model call was made", str(cm.exception))

    def test_a_directory_in_its_place_is_refused(self):
        self.w.state.mkdir(parents=True, exist_ok=True)
        (self.w.state / "settings.json").mkdir()
        with self.assertRaises(errors.BridgeError) as cm:
            self.load()
        self.assertEqual(cm.exception.code, "settings_file_invalid")


class S2_ServicePathUsesIt(WorldCase):
    def test_a_call_runs_the_preflight_under_the_file_backend_and_says_where_it_came_from(self):
        self.w.state.mkdir(parents=True, exist_ok=True)
        (self.w.state / "settings.json").write_text('{"windows_sandbox": "unelevated"}', encoding="utf-8")
        answer = self.service().execute(CONSULT, "q")
        first = self.w.log_entries("sandbox")[0]
        self.assertIn('windows.sandbox="unelevated"', " ".join(first["argv"]))
        self.assertIn("windows_sandbox_override:unelevated", self.w.telemetry()[0]["warnings"])
        self.assertIn("settings.json in the v2 state directory", answer)
        self.assertNotIn("(GPT_ARCHITECT_WINDOWS_SANDBOX)", answer)

    def test_an_invalid_file_stops_the_call_before_any_process_runs(self):
        self.w.state.mkdir(parents=True, exist_ok=True)
        (self.w.state / "settings.json").write_text("{broken", encoding="utf-8")
        with self.assertRaises(errors.BridgeError) as cm:
            self.service().execute(CONSULT, "q")
        self.assertEqual(cm.exception.code, "settings_file_invalid")
        self.assertFalse(cm.exception.inference_may_have_started)
        self.assertEqual(self.w.log_entries(), [], "nothing was started")

    def test_the_environment_override_banner_is_unchanged(self):
        self.w.extra_env["GPT_ARCHITECT_WINDOWS_SANDBOX"] = "unelevated"
        answer = self.service().execute(CONSULT, "q")
        self.assertIn("(GPT_ARCHITECT_WINDOWS_SANDBOX). That configuration has no apply_patch/shell", answer)


if __name__ == "__main__":
    unittest.main()
