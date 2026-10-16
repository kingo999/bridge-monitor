import json
import os
import subprocess
import sys
import unittest
from unittest import mock

import support
from support import ObsTestCase, account, by_id, success, three_turn_records
import obs_reader


def reasons(call):
    return [r["code"] for r in call["estimate"]["reasons"]]


class BoundedReads(ObsTestCase):
    def test_missing_state_dir_is_visible(self):
        state = obs_reader.read_state(os.path.join(self.ws.root, "does-not-exist"))
        self.assertEqual(state["state_dir_state"], "missing")
        self.assertEqual(state["telemetry"]["events"], [])

    def test_empty_state_dir_has_missing_files(self):
        state = obs_reader.read_state(self.ws.state)
        self.assertTrue(all(f["state"] == "missing" for f in state["telemetry"]["files"]))
        self.assertEqual(state["inflight"]["state"], "missing")

    def test_partial_trailing_line_is_a_visible_gap(self):
        recs = three_turn_records()
        self.ws.write_calls(recs[:2], raw_lines=[json.dumps(recs[2])[:80]], trailing_newline=False)
        result, state = account(self.ws)
        self.assertEqual(state["telemetry"]["gaps"][0]["code"], "partial_trailing_line")
        self.assertEqual(by_id(result, 2)["usage_delta"]["input_tokens"], 172553)
        self.assertEqual(len(result["calls"]), 2)

    def test_complete_unterminated_final_record_is_accepted_and_noted(self):
        recs = three_turn_records()
        self.ws.write_calls(recs, trailing_newline=False)
        _result, state = account(self.ws)
        self.assertEqual(len(_result["calls"]), 3)
        self.assertEqual(state["telemetry"]["unterminated_but_complete"], 1)

    def test_malformed_line_between_records_blocks_estimates_after_it(self):
        recs = three_turn_records()
        self.ws.write_bytes("calls.jsonl", (json.dumps(recs[0]) + "\n{not json\n" +
                                            json.dumps(recs[1]) + "\n" + json.dumps(recs[2]) + "\n").encode())
        result, state = account(self.ws)
        self.assertEqual(state["telemetry"]["gap_total"], 1)
        self.assertIsNone(by_id(result, 2)["usage_delta"])
        self.assertIn("after_malformed_line", reasons(by_id(result, 2)))
        self.assertEqual(by_id(result, 3)["usage_delta"]["input_tokens"], 118896)

    def test_oversized_line_is_skipped_and_flagged(self):
        recs = three_turn_records()
        huge = json.dumps({"call_id": support.call_id(77), "pad": "x" * (obs_reader.MAX_LINE_BYTES + 10)})
        self.ws.write_bytes("calls.jsonl", (json.dumps(recs[0]) + "\n" + huge + "\n" +
                                            json.dumps(recs[1]) + "\n").encode())
        result, state = account(self.ws)
        self.assertEqual(state["telemetry"]["gaps"][0]["code"], "oversized_line")
        self.assertIsNone(by_id(result, 2)["usage_delta"])

    def test_oversized_file_reads_bounded_tail_and_flags_head(self):
        recs = [success(i, 1000 * i, 500 * i, 100 * i, 10 * i, resumed=i > 1) for i in range(1, 40)]
        self.ws.write_calls(recs)
        with mock.patch.object(obs_reader, "MAX_TELEMETRY_FILE_BYTES", 6000):
            result, state = account(self.ws)
        f = next(x for x in state["telemetry"]["files"] if x["name"] == "calls.jsonl")
        self.assertEqual(f["state"], "truncated")
        self.assertEqual(state["telemetry"]["gaps"][0]["code"], "file_head_truncated")
        self.assertLess(len(result["calls"]), 39)
        first = result["calls"][0]
        self.assertIsNone(first["usage_delta"])  # no baseline before the truncation point
        self.assertTrue(all(c["call_id"] for c in result["calls"]))  # no half-parsed record

    def test_record_limit_keeps_newest_and_is_visible(self):
        recs = three_turn_records()
        self.ws.write_calls(recs)
        with mock.patch.object(obs_reader, "MAX_RECORDS", 2):
            result, state = account(self.ws)
        self.assertEqual(len(result["calls"]), 2)
        self.assertEqual([c["call_id"] for c in result["calls"]],
                         [support.call_id(2), support.call_id(3)])
        self.assertTrue(state["telemetry"]["scan_truncated"])
        self.assertIsNone(by_id(result, 2)["usage_delta"])
        self.assertEqual(by_id(result, 3)["usage_delta"]["input_tokens"], 118896)
        f = next(x for x in state["telemetry"]["files"] if x["name"] == "calls.jsonl")
        self.assertEqual(f["state"], "truncated")

    def test_event_budget_bounds_barriers_while_scanning(self):
        garbage = ["x"] * 2000
        self.ws.write_calls(three_turn_records()[:1], raw_lines=garbage)
        with mock.patch.object(obs_reader, "MAX_EVENTS", 25):
            result, state = account(self.ws)
        tel = state["telemetry"]
        self.assertTrue(tel["scan_truncated"])
        self.assertLessEqual(len(tel["events"]), 25 + 3)      # budget + head/boundary events
        self.assertLessEqual(tel["gap_total"], 25 + 3)
        self.assertEqual(result["calls"], [])                  # the older record was not reached

    def test_line_budget_bounds_blank_line_scanning(self):
        self.ws.write_bytes("calls.jsonl", json.dumps(three_turn_records()[0]).encode() + b"\n" + b"\n" * 5000)
        with mock.patch.object(obs_reader, "MAX_LINES_SCANNED", 100):
            result, state = account(self.ws)
        self.assertTrue(state["telemetry"]["scan_truncated"])
        self.assertEqual(result["calls"], [])

    def test_budget_leaves_older_rotated_files_unread_and_flagged(self):
        recs = three_turn_records()
        self.ws.write_calls(recs[:1], name="calls.1.jsonl")
        self.ws.write_calls(recs[1:], raw_lines=["x"] * 200)
        with mock.patch.object(obs_reader, "MAX_EVENTS", 10):
            result, state = account(self.ws)
        states = {f["name"]: f["state"] for f in state["telemetry"]["files"]}
        self.assertEqual(states["calls.jsonl"], "truncated")
        self.assertEqual(states["calls.1.jsonl"], "not_read")
        self.assertTrue(state["telemetry"]["scan_truncated"])

    def test_budget_does_not_fire_for_normal_volume(self):
        self.ws.write_calls([success(i, 1000 * i, 500 * i, 100 * i, 10 * i, resumed=i > 1)
                             for i in range(1, 200)])
        result, state = account(self.ws)
        self.assertFalse(state["telemetry"]["scan_truncated"])
        self.assertEqual(len(result["calls"]), 199)

    def test_forward_order_preserved_with_reverse_scan(self):
        self.ws.write_calls(three_turn_records())
        _result, state = account(self.ws)
        ids = [e["rec"]["call_id"] for e in state["telemetry"]["events"] if e["kind"] == "record"]
        self.assertEqual(ids, [support.call_id(1), support.call_id(2), support.call_id(3)])
        lines = [e["line"] for e in state["telemetry"]["events"] if e["kind"] == "record"]
        self.assertEqual(lines, [1, 2, 3])

    def test_non_object_nan_and_deep_nesting_do_not_crash(self):
        deep = "[" * 100000
        self.ws.write_bytes("calls.jsonl", b"[1,2]\n{\"a\": NaN}\n" + deep.encode() + b"\n\xff\xfe\n")
        result, state = account(self.ws)
        self.assertEqual(result["calls"], [])
        self.assertEqual(state["telemetry"]["gap_total"], 4)

    def test_hostile_text_is_bounded_and_neutralised(self):
        rec = success(1, 1000, 500, 100, 10, resumed=False)
        rec["outcome"] = "error"
        rec["error_category"] = "<script>alert(1)</script>\x00\x1b[31m" + "A" * 500
        rec["error_code"] = "‮evil"
        rec["warnings"] = ["<img src=x onerror=alert(1)>" + "B" * 1000] * 20
        rec["tool"] = "<b>tool</b>"
        rec["route"]["model"] = "model\nwith newline"
        rec["session"]["name"] = "<svg/onload=1>"
        rec["prompt"] = "SECRET PROMPT"
        rec["answer"] = "SECRET ANSWER"
        rec["unknown_field"] = {"nested": "SECRET"}
        self.ws.write_calls([rec])
        result, _ = account(self.ws)
        c = result["calls"][0]
        blob = json.dumps(c)
        self.assertNotIn("SECRET", blob)
        self.assertLessEqual(len(c["error_category"]), 65)
        self.assertNotIn("\x00", blob)
        self.assertNotIn("‮", c["error_code"])
        self.assertLessEqual(len(c["warnings"]), obs_reader.MAX_WARNINGS)
        self.assertTrue(all(len(w) <= 161 for w in c["warnings"]))
        self.assertEqual(c["warnings_total"], 20)
        self.assertIsNone(c["tool"])  # identifiers failing the pattern are dropped, not echoed
        self.assertIsNone(c["route"]["model"])
        self.assertIsNone(c["session"]["name"])


@unittest.skipUnless(os.name == "nt" or hasattr(os, "symlink"), "no link support")
class LinkRefusal(ObsTestCase):
    def _symlink(self, target, link, directory):
        try:
            os.symlink(target, link, target_is_directory=directory)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable in this environment")

    def _junction(self, target, link):
        if os.name != "nt":
            self.skipTest("junctions are Windows only")
        proc = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True)
        if proc.returncode != 0:
            self.skipTest("could not create a junction here")

    def test_symlinked_telemetry_file_is_refused(self):
        outside = os.path.join(self.ws.root, "outside.jsonl")
        with open(outside, "w") as fh:
            fh.write(json.dumps(success(1, 1, 0, 1, 0, resumed=False)) + "\n")
        self.ws.telemetry_dir()
        self._symlink(outside, os.path.join(self.ws.state, "telemetry", "calls.jsonl"), False)
        state = obs_reader.read_state(self.ws.state)
        f = next(x for x in state["telemetry"]["files"] if x["name"] == "calls.jsonl")
        self.assertEqual(f["state"], "refused_link")
        self.assertEqual(state["telemetry"]["events"][0]["kind"], "barrier")

    def test_junctioned_telemetry_directory_is_refused(self):
        real = os.path.join(self.ws.root, "real-telemetry")
        os.mkdir(real)
        with open(os.path.join(real, "calls.jsonl"), "w") as fh:
            fh.write(json.dumps(success(1, 1, 0, 1, 0, resumed=False)) + "\n")
        self._junction(real, os.path.join(self.ws.state, "telemetry"))
        state = obs_reader.read_state(self.ws.state)
        self.assertTrue(all(f["state"] == "refused_link" for f in state["telemetry"]["files"]))
        self.assertEqual([e for e in state["telemetry"]["events"] if e["kind"] == "record"], [])

    def test_junctioned_inflight_directory_is_refused(self):
        real = os.path.join(self.ws.root, "real-inflight")
        os.mkdir(real)
        with open(os.path.join(real, "a.json"), "w") as fh:
            json.dump({"state": "running"}, fh)
        self._junction(real, os.path.join(self.ws.state, "inflight"))
        state = obs_reader.read_state(self.ws.state)
        self.assertEqual(state["inflight"]["state"], "refused_link")
        self.assertEqual(state["inflight"]["markers"], [])

    def test_junctioned_state_dir_is_refused(self):
        link = os.path.join(self.ws.root, "linked-state")
        self._junction(self.ws.state, link)
        state = obs_reader.read_state(link)
        self.assertEqual(state["state_dir_state"], "refused_link")

    def test_directory_in_place_of_file_is_refused(self):
        os.makedirs(os.path.join(self.ws.telemetry_dir(), "calls.jsonl"))
        state = obs_reader.read_state(self.ws.state)
        f = next(x for x in state["telemetry"]["files"] if x["name"] == "calls.jsonl")
        self.assertEqual(f["state"], "refused_not_regular")

    def test_symlinked_marker_is_not_read(self):
        outside = os.path.join(self.ws.root, "outside.json")
        with open(outside, "w") as fh:
            json.dump({"state": "running", "call_id": support.call_id(1)}, fh)
        self.ws.inflight_dir()
        self._symlink(outside, os.path.join(self.ws.state, "inflight", "m.json"), False)
        state = obs_reader.read_state(self.ws.state)
        self.assertEqual(state["inflight"]["markers"], [])
        self.assertEqual(state["inflight"]["problems"], 1)


class AncestorRefusal(ObsTestCase):
    """A link anywhere above the state dir must be refused, not followed."""

    def _fixture(self):
        self.ws.write_calls(three_turn_records())

    def test_simulated_reparse_ancestor_is_refused_unconditionally(self):
        self._fixture()
        with support.simulated_reparse(self.ws.root):  # parent of the state dir
            state = obs_reader.read_state(self.ws.state)
        self.assertEqual(state["state_dir_state"], "refused_link")
        self.assertEqual(state["telemetry"]["events"], [])
        self.assertEqual(state["inflight"]["markers"], [])

    def test_simulated_reparse_grandparent_is_refused(self):
        self._fixture()
        with support.simulated_reparse(os.path.dirname(self.ws.root)):
            state = obs_reader.read_state(self.ws.state)
        self.assertEqual(state["state_dir_state"], "refused_link")

    def test_normal_path_is_still_read(self):
        self._fixture()
        state = obs_reader.read_state(self.ws.state)
        self.assertEqual(state["state_dir_state"], "ok")
        self.assertEqual(len([e for e in state["telemetry"]["events"] if e["kind"] == "record"]), 3)

    def test_relative_state_dir_is_checked_through_its_absolute_ancestors(self):
        self._fixture()
        try:
            rel = os.path.relpath(self.ws.state)
        except ValueError:  # different drive from the working directory
            self.skipTest("no relative path to the fixture from this working directory")
        with support.simulated_reparse(self.ws.root):
            state = obs_reader.read_state(rel)
        self.assertEqual(state["state_dir_state"], "refused_link")

    def test_native_junction_ancestor_is_refused(self):
        if os.name != "nt":
            self.skipTest("junctions are Windows only")
        real_parent = os.path.join(self.ws.root, "real-parent")
        os.mkdir(real_parent)
        real_state = os.path.join(real_parent, "state")
        os.mkdir(real_state)
        os.mkdir(os.path.join(real_state, "telemetry"))
        with open(os.path.join(real_state, "telemetry", "calls.jsonl"), "w") as fh:
            fh.write(json.dumps(success(1, 1, 0, 1, 0, resumed=False)) + "\n")
        junction = os.path.join(self.ws.root, "junction-parent")
        proc = subprocess.run(["cmd", "/c", "mklink", "/J", junction, real_parent], capture_output=True)
        if proc.returncode != 0:
            self.skipTest("could not create a junction here")
        state = obs_reader.read_state(os.path.join(junction, "state"))
        self.assertEqual(state["state_dir_state"], "refused_link")
        self.assertEqual([e for e in state["telemetry"]["events"] if e["kind"] == "record"], [])

    def test_native_symlink_ancestor_is_refused(self):
        real_parent = os.path.join(self.ws.root, "real-parent")
        os.mkdir(real_parent)
        os.mkdir(os.path.join(real_parent, "state"))
        link = os.path.join(self.ws.root, "link-parent")
        try:
            os.symlink(real_parent, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable in this environment")
        state = obs_reader.read_state(os.path.join(link, "state"))
        self.assertEqual(state["state_dir_state"], "refused_link")

    def test_simulated_reparse_telemetry_dir_and_inflight_dir_leaves(self):
        self._fixture()
        self.ws.write_marker("a.json", {"state": "running"})
        with support.simulated_reparse(os.path.join(self.ws.state, "telemetry"),
                                       os.path.join(self.ws.state, "inflight")):
            state = obs_reader.read_state(self.ws.state)
        self.assertTrue(all(f["state"] == "refused_link" for f in state["telemetry"]["files"]))
        self.assertGreaterEqual(state["telemetry"]["gap_total"], 1)
        self.assertEqual(state["inflight"]["state"], "refused_link")


class MarkerBounds(ObsTestCase):
    def test_oversized_and_malformed_markers_are_counted_not_read(self):
        self.ws.write_marker("ok.json", {"state": "running", "call_id": support.call_id(1)})
        with open(os.path.join(self.ws.inflight_dir(), "big.json"), "w") as fh:
            fh.write('{"state": "running", "pad": "' + "x" * (obs_reader.MAX_MARKER_BYTES + 5) + '"}')
        with open(os.path.join(self.ws.inflight_dir(), "bad.json"), "w") as fh:
            fh.write("{oops")
        with open(os.path.join(self.ws.inflight_dir(), "note.txt"), "w") as fh:
            fh.write("ignored")
        state = obs_reader.read_state(self.ws.state)
        self.assertEqual(len(state["inflight"]["markers"]), 1)
        self.assertEqual(state["inflight"]["problems"], 2)
        self.assertEqual(state["inflight"]["skipped"], 1)

    def test_marker_count_is_bounded(self):
        for i in range(8):
            self.ws.write_marker("m%d.json" % i, {"state": "running"})
        with mock.patch.object(obs_reader, "MAX_MARKERS", 3):
            state = obs_reader.read_state(self.ws.state)
        self.assertEqual(len(state["inflight"]["markers"]), 3)
        self.assertTrue(state["inflight"]["truncated"])


class ReadOnly(ObsTestCase):
    def test_reading_does_not_modify_the_state_dir(self):
        self.ws.write_calls(three_turn_records())
        self.ws.write_marker("a.json", {"state": "running"})
        before = support.tree_digest(self.ws.state)
        account(self.ws)
        self.assertEqual(support.tree_digest(self.ws.state), before)


if __name__ == "__main__":
    unittest.main()
