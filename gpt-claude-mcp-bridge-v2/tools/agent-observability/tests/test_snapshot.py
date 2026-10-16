"""Snapshot-level checks: the source may only be called complete when nothing required
was refused, unreadable, truncated or absent."""
import os
import subprocess
import unittest
from unittest import mock

import support
from support import ObsTestCase, three_turn_records
import dashboard
import obs_reader


def snapshot(ws, state_dir=None):
    return dashboard.build_snapshot(state_dir or ws.state, now=support.NOW, label="test")


def notice_codes(snap):
    return [n["code"] for n in snap["notices"]]


def notice_levels(snap, code):
    return [n["level"] for n in snap["notices"] if n["code"] == code]


class Completeness(ObsTestCase):
    def test_healthy_source_is_complete(self):
        self.ws.write_calls(three_turn_records())
        snap = snapshot(self.ws)
        self.assertTrue(snap["source"]["complete"])
        self.assertEqual(snap["source"]["incomplete_reasons"], [])

    def test_missing_state_dir_is_incomplete_with_error(self):
        snap = snapshot(self.ws, os.path.join(self.ws.root, "absent"))
        self.assertFalse(snap["source"]["complete"])
        self.assertIn("state_dir_missing", snap["source"]["incomplete_reasons"])
        self.assertEqual(notice_levels(snap, "state_dir_missing"), ["error"])

    def test_missing_telemetry_is_not_complete(self):
        snap = snapshot(self.ws)
        self.assertFalse(snap["source"]["complete"])
        self.assertIn("no_telemetry", snap["source"]["incomplete_reasons"])
        self.assertIn("no_telemetry", notice_codes(snap))

    def test_empty_telemetry_file_is_not_complete(self):
        self.ws.write_bytes("calls.jsonl", b"")
        snap = snapshot(self.ws)
        self.assertFalse(snap["source"]["complete"])
        self.assertIn("no_records", snap["source"]["incomplete_reasons"])
        self.assertIn("no_records", notice_codes(snap))

    def test_refused_telemetry_directory_is_incomplete_with_gap_and_error(self):
        self.ws.write_calls(three_turn_records())
        with support.simulated_reparse(os.path.join(self.ws.state, "telemetry")):
            snap = snapshot(self.ws)
        self.assertFalse(snap["source"]["complete"])
        self.assertGreaterEqual(snap["source"]["gap_total"], 1)  # previously reported 0
        self.assertIn("telemetry_file_refused_link", snap["source"]["incomplete_reasons"])
        self.assertIn("error", [n["level"] for n in snap["notices"]])
        self.assertEqual(snap["calls"], [])

    def test_native_junction_telemetry_directory_is_incomplete(self):
        if os.name != "nt":
            self.skipTest("junctions are Windows only")
        real = os.path.join(self.ws.root, "real-telemetry")
        os.mkdir(real)
        link = os.path.join(self.ws.state, "telemetry")
        proc = subprocess.run(["cmd", "/c", "mklink", "/J", link, real], capture_output=True)
        if proc.returncode != 0:
            self.skipTest("could not create a junction here")
        snap = snapshot(self.ws)
        self.assertFalse(snap["source"]["complete"])
        self.assertGreaterEqual(snap["source"]["gap_total"], 1)

    def test_refused_state_dir_ancestor_is_incomplete_with_error(self):
        self.ws.write_calls(three_turn_records())
        with support.simulated_reparse(self.ws.root):
            snap = snapshot(self.ws)
        self.assertFalse(snap["source"]["complete"])
        self.assertIn("state_dir_refused_link", snap["source"]["incomplete_reasons"])
        self.assertEqual(snap["calls"], [])

    def test_one_unreadable_telemetry_file_is_incomplete(self):
        self.ws.write_calls(three_turn_records())
        os.makedirs(os.path.join(self.ws.telemetry_dir(), "calls.1.jsonl"))  # not a regular file
        snap = snapshot(self.ws)
        self.assertFalse(snap["source"]["complete"])
        self.assertIn("telemetry_file_refused_not_regular", snap["source"]["incomplete_reasons"])
        self.assertIn("file_refused_not_regular", notice_codes(snap))

    def test_truncated_file_is_incomplete(self):
        self.ws.write_calls(three_turn_records())
        with mock.patch.object(obs_reader, "MAX_TELEMETRY_FILE_BYTES", 700):
            snap = snapshot(self.ws)
        self.assertFalse(snap["source"]["complete"])
        self.assertIn("telemetry_file_truncated", snap["source"]["incomplete_reasons"])

    def test_scan_budget_exhaustion_is_incomplete_with_notice(self):
        self.ws.write_calls(three_turn_records(), raw_lines=["x"] * 100)
        with mock.patch.object(obs_reader, "MAX_EVENTS", 10):
            snap = snapshot(self.ws)
        self.assertFalse(snap["source"]["complete"])
        self.assertIn("scan_budget_exceeded", snap["source"]["incomplete_reasons"])
        self.assertIn("scan_budget_exceeded", notice_codes(snap))


class InflightCompleteness(ObsTestCase):
    def setUp(self):
        super().setUp()
        self.ws.write_calls(three_turn_records())

    def test_refused_inflight_directory_is_incomplete_with_error(self):
        self.ws.write_marker("a.json", {"state": "running"})
        with support.simulated_reparse(os.path.join(self.ws.state, "inflight")):
            snap = snapshot(self.ws)
        self.assertFalse(snap["source"]["complete"])
        self.assertIn("inflight_refused_link", snap["source"]["incomplete_reasons"])
        self.assertEqual(notice_levels(snap, "inflight_refused_link"), ["error"])
        self.assertEqual(snap["inflight"], [])

    def test_unreadable_marker_is_incomplete(self):
        with open(os.path.join(self.ws.inflight_dir(), "bad.json"), "w") as fh:
            fh.write("{oops")
        snap = snapshot(self.ws)
        self.assertFalse(snap["source"]["complete"])
        self.assertIn("inflight_unreadable_markers", snap["source"]["incomplete_reasons"])
        self.assertIn("inflight_partial", notice_codes(snap))

    def test_truncated_marker_set_is_incomplete(self):
        for i in range(6):
            self.ws.write_marker("m%d.json" % i, {"state": "running"})
        with mock.patch.object(obs_reader, "MAX_MARKERS", 2):
            snap = snapshot(self.ws)
        self.assertFalse(snap["source"]["complete"])
        self.assertIn("inflight_truncated", snap["source"]["incomplete_reasons"])

    def test_absent_inflight_directory_alone_is_not_an_error(self):
        snap = snapshot(self.ws)
        self.assertTrue(snap["source"]["complete"])


class NoEstimatesNotice(ObsTestCase):
    def test_unsupported_cli_notice_uses_neutral_raw_counter_wording(self):
        self.ws.write_calls([support.success(1, 1000, 500, 100, 10, resumed=False, cli="9.9.9")])
        snap = snapshot(self.ws)
        notice = next(n for n in snap["notices"] if n["code"] == "no_estimates")
        self.assertIn("raw counters are shown as observed", notice["message"])
        self.assertNotIn("cumulative", notice["message"])
        # the per-row basis stays explicit
        self.assertEqual(snap["calls"][0]["usage_basis"], "unknown")
        self.assertEqual(snap["calls"][0]["usage_raw"]["input_tokens"], 1000)


class SnapshotBasis(ObsTestCase):
    def test_served_rows_carry_per_row_usage_basis(self):
        missing_cli = support.success(2, 1000, 500, 100, 10, resumed=False, thread="t-2", name="two")
        del missing_cli["cli_version"]
        self.ws.write_calls([support.success(1, 1000, 500, 100, 10, resumed=False), missing_cli])
        snap = snapshot(self.ws)
        basis = {c["call_id"]: c["usage_basis"] for c in snap["calls"]}
        self.assertEqual(basis[support.call_id(1)], "session_cumulative")
        self.assertEqual(basis[support.call_id(2)], "unknown")
        unknown = next(c for c in snap["calls"] if c["call_id"] == support.call_id(2))
        self.assertEqual(unknown["usage_raw"]["input_tokens"], 1000)  # still displayed
        self.assertIsNone(unknown["usage_delta"])


if __name__ == "__main__":
    unittest.main()
