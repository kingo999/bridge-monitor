import unittest

import support
from support import (ObsTestCase, account, by_id, failure, success, three_turn_records, THREAD)


def status(call):
    return call["estimate"]["status"]


def reason_codes(call):
    return [r["code"] for r in call["estimate"]["reasons"]]


class ThreeTurnFixture(ObsTestCase):
    def test_reproduces_observed_deltas(self):
        self.ws.write_calls(three_turn_records())
        result, _ = account(self.ws)
        calls = [by_id(result, n) for n in (1, 2, 3)]
        self.assertEqual([c["usage_delta"]["input_tokens"] for c in calls], [181960, 172553, 118896])
        self.assertEqual([c["usage_delta"]["cached_input_tokens"] for c in calls], [136448, 167424, 117504])
        self.assertEqual([c["usage_delta"]["output_tokens"] for c in calls], [3652, 1335, 139])
        self.assertEqual([c["durations"]["run_ms"] for c in calls], [141652, 53113, 14297])
        self.assertEqual([status(c) for c in calls],
                         ["fresh_baseline_estimate", "eligible_estimate", "eligible_estimate"])

    def test_raw_cumulative_values_preserved_and_labelled(self):
        self.ws.write_calls(three_turn_records())
        result, _ = account(self.ws)
        raw = [by_id(result, n)["usage_raw"]["input_tokens"] for n in (1, 2, 3)]
        self.assertEqual(raw, [181960, 354513, 473409])
        for n in (1, 2, 3):
            self.assertEqual(by_id(result, n)["usage_basis"], "session_cumulative")
            self.assertIsNone(by_id(result, n)["usage_raw"]["total_tokens"])
            self.assertIsNone(by_id(result, n)["usage_delta"]["total_tokens"])

    def test_reasoning_is_a_subset_and_never_added(self):
        self.ws.write_calls(three_turn_records())
        result, _ = account(self.ws)
        c = by_id(result, 2)
        self.assertEqual(c["usage_delta"]["reasoning_output_tokens"], 1888 - 1484)
        self.assertEqual(c["usage_delta"]["output_tokens"], 1335)  # unchanged by reasoning
        self.assertAlmostEqual(c["ratios"]["reasoning_share_raw"], 1888 / 4987, places=4)

    def test_cache_ratios_need_positive_known_denominator(self):
        self.ws.write_calls([success(1, 0, 0, 10, 0, resumed=False)])
        result, _ = account(self.ws)
        c = by_id(result, 1)
        self.assertIsNone(c["ratios"]["cache_raw"])
        self.assertIsNone(c["ratios"]["cache_delta"])


class Baselines(ObsTestCase):
    def test_unknown_first_resumed_call_has_no_baseline_but_seeds_the_next(self):
        self.ws.write_calls([success(1, 1000, 500, 100, 10, resumed=True),
                             success(2, 1600, 900, 160, 20, resumed=True)])
        result, _ = account(self.ws)
        self.assertEqual(status(by_id(result, 1)), "not_estimated")
        self.assertIsNone(by_id(result, 1)["usage_delta"])
        self.assertIn("no_baseline_first_resumed", reason_codes(by_id(result, 1)))
        self.assertEqual(by_id(result, 2)["usage_delta"]["input_tokens"], 600)

    def test_fresh_requires_explicit_false(self):
        self.ws.write_calls([success(1, 1000, 500, 100, 10, resumed=None)])
        result, _ = account(self.ws)
        self.assertEqual(status(by_id(result, 1)), "not_estimated")
        self.assertIsNone(by_id(result, 1)["usage_delta"])

    def test_fresh_flag_on_an_already_observed_thread_conflicts(self):
        self.ws.write_calls([success(1, 1000, 500, 100, 10, resumed=False),
                             success(2, 1500, 700, 140, 10, resumed=False)])
        result, _ = account(self.ws)
        self.assertEqual(status(by_id(result, 2)), "not_estimated")
        self.assertIn("fresh_flag_conflict", reason_codes(by_id(result, 2)))

    def test_rotation_missing_baseline_in_remaining_file(self):
        # Only calls.jsonl survives; its first call is a resumed turn.
        self.ws.write_calls(three_turn_records()[1:])
        result, _ = account(self.ws)
        self.assertIsNone(by_id(result, 2)["usage_delta"])
        self.assertEqual(by_id(result, 3)["usage_delta"]["input_tokens"], 118896)

    def test_rotated_files_are_read_oldest_first(self):
        recs = three_turn_records()
        self.ws.write_calls(recs[:1], name="calls.2.jsonl")
        self.ws.write_calls(recs[1:2], name="calls.1.jsonl")
        self.ws.write_calls(recs[2:], name="calls.jsonl")
        result, state = account(self.ws)
        self.assertEqual(by_id(result, 3)["usage_delta"]["input_tokens"], 118896)
        self.assertEqual(state["telemetry"]["gap_total"], 0)

    def test_rotation_gap_invalidates_baseline(self):
        recs = three_turn_records()
        self.ws.write_calls(recs[:1], name="calls.3.jsonl")
        # calls.2 and calls.1 missing: call 2 may have been rotated away.
        self.ws.write_calls(recs[2:], name="calls.jsonl")
        result, state = account(self.ws)
        self.assertIsNone(by_id(result, 3)["usage_delta"])
        self.assertIn("after_rotation_gap", reason_codes(by_id(result, 3)))
        self.assertGreaterEqual(state["telemetry"]["gap_total"], 1)

    def test_two_disjoint_rotation_gaps_each_suppress_the_next_estimate(self):
        recs = three_turn_records()
        self.ws.write_calls(recs[:1], name="calls.5.jsonl")
        self.ws.write_calls(recs[1:2], name="calls.3.jsonl")   # calls.4 missing before it
        self.ws.write_calls(recs[2:], name="calls.1.jsonl")    # calls.2 missing before it
        result, state = account(self.ws)
        self.assertEqual([status(by_id(result, n)) for n in (1, 2, 3)],
                         ["fresh_baseline_estimate", "not_estimated", "not_estimated"])
        self.assertIsNone(by_id(result, 2)["usage_delta"])
        self.assertIsNone(by_id(result, 3)["usage_delta"])
        self.assertIn("after_rotation_gap", reason_codes(by_id(result, 2)))
        self.assertIn("after_rotation_gap", reason_codes(by_id(result, 3)))
        # raw cumulative counters of the later calls are still shown as logged
        self.assertEqual(by_id(result, 3)["usage_raw"]["input_tokens"], 473409)
        tel = state["telemetry"]
        self.assertEqual(tel["gap_total"], 2)
        self.assertEqual([(g["code"], g["file"]) for g in tel["gaps"]],
                         [("rotation_gap", "calls.4.jsonl"), ("rotation_gap", "calls.2.jsonl")])
        # each barrier sits between the two records it separates
        shape = [e["kind"] if e["kind"] != "record" else e["rec"]["call_id"][-1]
                 for e in tel["events"]]
        self.assertEqual(shape, ["1", "barrier", "2", "barrier", "3"])

    def test_intervening_success_seeds_intervals_until_the_next_gap(self):
        self.ws.write_calls([success(1, 1000, 500, 100, 10, resumed=False),
                             success(2, 1600, 800, 160, 20, resumed=True)], name="calls.5.jsonl")
        self.ws.write_calls([success(3, 2000, 900, 300, 30, resumed=True),
                             success(4, 2600, 1000, 360, 40, resumed=True)], name="calls.3.jsonl")
        self.ws.write_calls([success(5, 3000, 1100, 400, 50, resumed=True)], name="calls.1.jsonl")
        result, state = account(self.ws)
        self.assertEqual(by_id(result, 2)["usage_delta"]["input_tokens"], 600)   # inside calls.5
        self.assertIsNone(by_id(result, 3)["usage_delta"])                       # across missing .4
        self.assertEqual(by_id(result, 4)["usage_delta"]["input_tokens"], 600)   # reseeded by 3
        self.assertIsNone(by_id(result, 5)["usage_delta"])                       # across missing .2
        self.assertEqual(state["telemetry"]["gap_total"], 2)

    def test_consecutive_missing_files_are_one_gap(self):
        recs = three_turn_records()
        self.ws.write_calls(recs[:1], name="calls.5.jsonl")
        self.ws.write_calls(recs[1:])                            # calls.4 .. calls.1 all missing
        result, state = account(self.ws)
        self.assertEqual(state["telemetry"]["gap_total"], 1)
        self.assertEqual([g["code"] for g in state["telemetry"]["gaps"]], ["rotation_gap"])
        self.assertIsNone(by_id(result, 2)["usage_delta"])
        self.assertEqual(by_id(result, 3)["usage_delta"]["input_tokens"], 118896)  # reseeded by 2

    def test_gap_group_and_single_gap_are_counted_separately(self):
        recs = three_turn_records()
        self.ws.write_calls(recs[:1], name="calls.5.jsonl")
        self.ws.write_calls(recs[1:2], name="calls.2.jsonl")     # calls.4, calls.3 missing (one group)
        self.ws.write_calls(recs[2:])                            # calls.1 missing (second gap)
        result, state = account(self.ws)
        self.assertEqual(state["telemetry"]["gap_total"], 2)
        self.assertIsNone(by_id(result, 2)["usage_delta"])
        self.assertIsNone(by_id(result, 3)["usage_delta"])

    def test_missing_current_file_alone_is_not_a_gap(self):
        self.ws.write_calls(three_turn_records(), name="calls.1.jsonl")
        _result, state = account(self.ws)
        self.assertEqual(state["telemetry"]["gap_total"], 0)

    def test_counter_reset_has_no_delta_then_recovers(self):
        self.ws.write_calls([success(1, 5000, 100, 500, 50, resumed=False),
                             success(2, 800, 10, 60, 5, resumed=True),
                             success(3, 1000, 30, 90, 8, resumed=True)])
        result, _ = account(self.ws)
        self.assertIsNone(by_id(result, 2)["usage_delta"])
        self.assertIn("counter_reset", reason_codes(by_id(result, 2)))
        self.assertEqual(by_id(result, 3)["usage_delta"]["input_tokens"], 200)


class Failures(ObsTestCase):
    def test_inference_started_failure_with_null_thread_invalidates_by_repo_session(self):
        self.ws.write_calls([success(1, 1000, 500, 100, 10, resumed=False),
                             failure(2, outcome="timeout", inference=True, thread=None),
                             success(3, 2000, 900, 300, 20, resumed=True),
                             success(4, 2500, 1000, 360, 25, resumed=True)])
        result, _ = account(self.ws)
        self.assertIsNone(by_id(result, 3)["usage_delta"])
        self.assertIn("after_inference_started_unmeasured", reason_codes(by_id(result, 3)))
        self.assertEqual(by_id(result, 4)["usage_delta"]["input_tokens"], 500)
        self.assertEqual(status(by_id(result, 2)), "not_estimated")

    def test_failure_before_inference_does_not_break_continuity(self):
        self.ws.write_calls([success(1, 1000, 500, 100, 10, resumed=False),
                             failure(2, inference=False, thread=None),
                             success(3, 2000, 900, 300, 20, resumed=True)])
        result, _ = account(self.ws)
        self.assertEqual(status(by_id(result, 2)), "not_applicable")
        self.assertEqual(by_id(result, 3)["usage_delta"]["input_tokens"], 1000)

    def test_unclassifiable_failure_invalidates(self):
        bad = failure(2, thread=None)
        bad["inference_may_have_started"] = None
        bad["history"] = None
        self.ws.write_calls([success(1, 1000, 500, 100, 10, resumed=False), bad,
                             success(3, 2000, 900, 300, 20, resumed=True)])
        result, _ = account(self.ws)
        self.assertIsNone(by_id(result, 3)["usage_delta"])

    def test_failure_in_another_repo_session_does_not_invalidate(self):
        other = failure(2, thread=None, name="other", repo_key="f" * 24)
        self.ws.write_calls([success(1, 1000, 500, 100, 10, resumed=False), other,
                             success(3, 2000, 900, 300, 20, resumed=True)])
        result, _ = account(self.ws)
        self.assertEqual(by_id(result, 3)["usage_delta"]["input_tokens"], 1000)

    def test_unknown_repo_failure_invalidates_everything(self):
        anon = failure(2, thread=None)
        anon["session"] = {"name": None, "repo_key": None, "thread_id": None, "resumed": None}
        self.ws.write_calls([success(1, 1000, 500, 100, 10, resumed=False), anon,
                             success(3, 2000, 900, 300, 20, resumed=True)])
        result, _ = account(self.ws)
        self.assertIsNone(by_id(result, 3)["usage_delta"])

    def test_partial_persistence_and_in_doubt_are_uncertain(self):
        for outcome in ("success_partial_persistence", "success_session_in_doubt"):
            with self.subTest(outcome=outcome):
                ws = support.Workspace()
                self.addCleanup(ws.cleanup)
                partial = success(2, 2000, 900, 300, 20, resumed=True, outcome=outcome,
                                  session_state="in_doubt")
                ws.write_calls([success(1, 1000, 500, 100, 10, resumed=False), partial,
                                success(3, 2600, 1000, 380, 30, resumed=True)])
                result, _ = account(ws)
                self.assertEqual(status(by_id(result, 2)), "not_estimated")
                self.assertIn("recovery_uncertain", reason_codes(by_id(result, 2)))
                self.assertIsNone(by_id(result, 3)["usage_delta"])

    def test_success_without_usage_stays_unknown_and_breaks_continuity(self):
        no_usage = success(2, None, None, None, None, resumed=True)
        self.ws.write_calls([success(1, 1000, 500, 100, 10, resumed=False), no_usage,
                             success(3, 2000, 900, 300, 20, resumed=True)])
        result, _ = account(self.ws)
        c = by_id(result, 2)
        self.assertIsNone(c["usage_raw"]["input_tokens"])  # null, never 0
        self.assertIsNone(c["usage_delta"])
        self.assertIsNone(by_id(result, 3)["usage_delta"])

    def test_missing_usage_object_is_unknown(self):
        rec = success(1, 1, 0, 1, 0, resumed=False)
        del rec["usage"]
        self.ws.write_calls([rec])
        result, _ = account(self.ws)
        self.assertIsNone(by_id(result, 1)["usage_raw"]["input_tokens"])
        self.assertFalse(by_id(result, 1)["usage_present"])
        self.assertIsNone(by_id(result, 1)["usage_delta"])

    def test_inconsistent_or_invalid_usage_gives_no_estimate(self):
        cases = {
            "cached_gt_input": success(1, 100, 200, 10, 1, resumed=False),
            "reasoning_gt_output": success(1, 100, 50, 10, 11, resumed=False),
            "negative": success(1, -5, 0, 10, 1, resumed=False),
            "string": success(1, "100", 0, 10, 1, resumed=False),
            "float": success(1, 100.5, 0, 10, 1, resumed=False),
            "bool": success(1, True, 0, 10, 1, resumed=False),
        }
        for label, rec in cases.items():
            with self.subTest(label):
                ws = support.Workspace()
                self.addCleanup(ws.cleanup)
                ws.write_calls([rec])
                result, _ = account(ws)
                self.assertIsNone(by_id(result, 1)["usage_delta"])
                self.assertEqual(status(by_id(result, 1)), "not_estimated")

    def test_success_contradicting_its_flags_is_ambiguous(self):
        cases = [dict(inference_may_have_started=False), dict(exit_code=2),
                 dict(session_state="pending_reconcile"), dict(pointer_state="bind_failed")]
        for over in cases:
            with self.subTest(over):
                ws = support.Workspace()
                self.addCleanup(ws.cleanup)
                ws.write_calls([success(1, 1000, 500, 100, 10, resumed=False, **over)])
                result, _ = account(ws)
                self.assertIsNone(by_id(result, 1)["usage_delta"])


class IdentityIsolation(ObsTestCase):
    def test_other_thread_session_repo_do_not_mix(self):
        t2 = "99999999-0000-7000-8000-000000000001"
        self.ws.write_calls([
            success(1, 1000, 500, 100, 10, resumed=False),
            success(2, 9000, 100, 900, 90, resumed=False, thread=t2, name="side", repo_key="e" * 24),
            success(3, 1400, 700, 160, 12, resumed=True),
            success(4, 9500, 150, 950, 95, resumed=True, thread=t2, name="side", repo_key="e" * 24),
        ])
        result, _ = account(self.ws)
        self.assertEqual(by_id(result, 3)["usage_delta"]["input_tokens"], 400)
        self.assertEqual(by_id(result, 4)["usage_delta"]["input_tokens"], 500)

    def test_model_change_breaks_the_interval(self):
        self.ws.write_calls([
            success(1, 1000, 500, 100, 10, resumed=False),
            success(2, 1500, 700, 150, 15, resumed=True, model="gpt-other"),
            success(3, 2100, 900, 210, 20, resumed=True, model="gpt-other"),
        ])
        result, _ = account(self.ws)
        self.assertIsNone(by_id(result, 2)["usage_delta"])
        self.assertIn("model_changed", reason_codes(by_id(result, 2)))
        self.assertEqual(by_id(result, 3)["usage_delta"]["input_tokens"], 600)

    def test_same_thread_under_different_repo_is_a_conflict(self):
        self.ws.write_calls([
            success(1, 1000, 500, 100, 10, resumed=False),
            success(2, 1500, 700, 150, 15, resumed=True, repo_key="d" * 24),
        ])
        result, _ = account(self.ws)
        self.assertIsNone(by_id(result, 2)["usage_delta"])
        self.assertIn("identity_mismatch", reason_codes(by_id(result, 2)))


class SchemaAndCli(ObsTestCase):
    def test_unsupported_cli_has_unknown_basis_and_no_estimate(self):
        self.ws.write_calls([success(1, 1000, 500, 100, 10, resumed=False, cli="9.9.9"),
                             success(2, 1500, 700, 150, 15, resumed=True, cli="9.9.9")])
        result, _ = account(self.ws)
        for n in (1, 2):
            c = by_id(result, n)
            self.assertEqual(c["usage_basis"], "unknown")
            self.assertIsNone(c["usage_delta"])
            self.assertIn("unsupported_cli", reason_codes(c))
            self.assertIsNone(c["ratios"]["cache_raw"])

    def test_missing_cli_version_is_unsupported(self):
        rec = success(1, 1000, 500, 100, 10, resumed=False)
        del rec["cli_version"]
        self.ws.write_calls([rec])
        result, _ = account(self.ws)
        self.assertIsNone(by_id(result, 1)["usage_delta"])

    def test_unknown_schema_is_unknown_and_invalidates(self):
        future = {"schema_version": 2, "call_id": support.call_id(2), "usage": {"input_tokens": 5},
                  "started_at": "2026-10-14T08:00:00Z"}
        self.ws.write_calls([success(1, 1000, 500, 100, 10, resumed=False), future,
                             success(3, 2000, 900, 300, 20, resumed=True)])
        result, _ = account(self.ws)
        c = by_id(result, 2)
        self.assertEqual(c["outcome_class"], "unknown")
        self.assertIsNone(c["usage_raw"]["input_tokens"])
        self.assertIn("unsupported_schema", reason_codes(c))
        self.assertIsNone(by_id(result, 3)["usage_delta"])

    def test_unrecognised_outcome_is_ambiguous(self):
        self.ws.write_calls([success(1, 1000, 500, 100, 10, resumed=False),
                             success(2, 1500, 700, 150, 15, resumed=True, outcome="mystery"),
                             success(3, 2000, 900, 300, 20, resumed=True)])
        result, _ = account(self.ws)
        self.assertIsNone(by_id(result, 3)["usage_delta"])


class DuplicatesAndOrdering(ObsTestCase):
    def test_identical_duplicates_count_once(self):
        recs = three_turn_records()
        self.ws.write_calls([recs[0], recs[1], recs[1], recs[2]])
        result, state = account(self.ws)
        self.assertEqual(len(result["calls"]), 3)
        self.assertEqual(state["telemetry"]["duplicates_identical"], 1)
        self.assertEqual(by_id(result, 3)["usage_delta"]["input_tokens"], 118896)

    def test_duplicates_across_rotated_files_count_once(self):
        recs = three_turn_records()
        self.ws.write_calls(recs[:2], name="calls.1.jsonl")
        self.ws.write_calls(recs[1:], name="calls.jsonl")
        result, state = account(self.ws)
        self.assertEqual(len(result["calls"]), 3)
        self.assertEqual(by_id(result, 3)["usage_delta"]["input_tokens"], 118896)

    def test_conflicting_duplicate_invalidates_accounting(self):
        recs = three_turn_records()
        altered = dict(recs[1])
        altered["usage"] = dict(altered["usage"], input_tokens=999999)
        self.ws.write_calls([recs[0], recs[1], altered, recs[2]])
        result, state = account(self.ws)
        self.assertEqual(state["telemetry"]["conflict_total"], 1)
        self.assertEqual(by_id(result, 2)["outcome_class"], "conflict")
        self.assertIsNone(by_id(result, 2)["usage_raw"]["input_tokens"])
        self.assertIsNone(by_id(result, 3)["usage_delta"])

    def test_missing_call_id_is_a_gap(self):
        recs = three_turn_records()
        nameless = dict(recs[1])
        del nameless["call_id"]
        self.ws.write_calls([recs[0], nameless, recs[2]])
        result, state = account(self.ws)
        self.assertIsNone(by_id(result, 3)["usage_delta"])
        self.assertEqual(state["telemetry"]["gap_total"], 1)

    def test_timestamp_order_contradiction_is_ambiguous(self):
        a = success(1, 1000, 500, 100, 10, resumed=False, minutes=30)
        b = success(2, 1600, 800, 160, 20, resumed=True, minutes=5)  # earlier than a, appended after
        self.ws.write_calls([a, b])
        result, _ = account(self.ws)
        self.assertIsNone(by_id(result, 2)["usage_delta"])
        self.assertIn("order_conflict", reason_codes(by_id(result, 2)))

    def test_invalid_timestamps_are_unknown_not_zero(self):
        rec = success(1, 1000, 500, 100, 10, resumed=False, started_at="yesterday", ended_at="2026-13-45T00:00:00Z")
        self.ws.write_calls([rec])
        result, _ = account(self.ws)
        c = by_id(result, 1)
        self.assertIsNone(c["started_at"])
        self.assertIsNone(c["durations"]["end_to_end_ms"])
        self.assertEqual(c["durations"]["run_ms"], 1000)
        self.assertIsNone(c["usage_delta"])  # ordering cannot be verified

    def test_end_to_end_distinct_from_execution_and_queue(self):
        rec = success(1, 1000, 500, 100, 10, resumed=False, run_ms=60000, queue_ms=2500)
        self.ws.write_calls([rec])
        result, _ = account(self.ws)
        d = by_id(result, 1)["durations"]
        self.assertEqual(d["run_ms"], 60000)
        self.assertEqual(d["queue_ms"], 2500)
        self.assertEqual(d["end_to_end_ms"], 64000)

    def test_negative_or_bad_durations_are_unknown(self):
        rec = success(1, 1000, 500, 100, 10, resumed=False, run_ms=-1, queue_ms="x")
        self.ws.write_calls([rec])
        result, _ = account(self.ws)
        d = by_id(result, 1)["durations"]
        self.assertIsNone(d["run_ms"])
        self.assertIsNone(d["queue_ms"])


class UnknownIdentities(ObsTestCase):
    """Unknown identities must never match each other (None == None is not a match)."""

    def _pair(self, **identity):
        self.ws.write_calls([
            success(1, 1000, 500, 100, 10, resumed=False, **identity),
            success(2, 1600, 800, 160, 20, resumed=True, **identity),
        ])
        result, _ = account(self.ws)
        return result

    def test_each_missing_identity_field_blocks_estimates_separately(self):
        for field, value in (("repo_key", None), ("name", None), ("model", None), ("thread", None)):
            with self.subTest(missing=field):
                ws = support.Workspace()
                self.addCleanup(ws.cleanup)
                ws.write_calls([
                    success(1, 1000, 500, 100, 10, resumed=False, **{field: value}),
                    success(2, 1600, 800, 160, 20, resumed=True, **{field: value}),
                ])
                result, _ = account(ws)
                for n in (1, 2):
                    self.assertIsNone(by_id(result, n)["usage_delta"], (field, n))
                    self.assertEqual(status(by_id(result, n)), "not_estimated", (field, n))
                self.assertTrue({"identity_unknown", "missing_thread_identity"}
                                & set(reason_codes(by_id(result, 2))))

    def test_all_identities_null_on_both_successes_gives_no_estimate(self):
        result = self._pair(repo_key=None, name=None, model=None, thread=None)
        for n in (1, 2):
            self.assertIsNone(by_id(result, n)["usage_delta"])
            self.assertEqual(status(by_id(result, n)), "not_estimated")
            self.assertIn("identity_unknown", reason_codes(by_id(result, n)))
        self.assertEqual(by_id(result, 2)["usage_raw"]["input_tokens"], 1600)  # raw still shown

    def test_known_thread_with_unknown_session_name_does_not_match_itself(self):
        result = self._pair(name=None)
        self.assertIsNone(by_id(result, 2)["usage_delta"])

    def test_intervening_missing_identity_row_invalidates_the_chain(self):
        cases = {
            "name": dict(name=None),
            "repo_key": dict(repo_key=None),
            "model": dict(model=None),
            "all": dict(name=None, repo_key=None, model=None, thread=None),
        }
        for label, missing in cases.items():
            with self.subTest(missing=label):
                ws = support.Workspace()
                self.addCleanup(ws.cleanup)
                ws.write_calls([
                    success(1, 1000, 500, 100, 10, resumed=False),
                    success(2, 1400, 700, 140, 14, resumed=True, **missing),
                    success(3, 2000, 900, 300, 20, resumed=True),
                    success(4, 2600, 1000, 360, 25, resumed=True),
                ])
                result, _ = account(ws)
                self.assertIsNone(by_id(result, 2)["usage_delta"])
                self.assertIsNone(by_id(result, 3)["usage_delta"])      # chain invalidated
                self.assertEqual(by_id(result, 4)["usage_delta"]["input_tokens"], 600)  # reseeded

    def test_two_unknown_rows_do_not_share_an_ordering_scope(self):
        a = success(1, 1000, 500, 100, 10, resumed=False, name=None, minutes=30)
        b = success(2, 1000, 500, 100, 10, resumed=False, name=None, minutes=5)
        self.ws.write_calls([a, b])
        result, _ = account(self.ws)
        self.assertNotIn("order_conflict", reason_codes(by_id(result, 2)))


class DeltaCoherence(ObsTestCase):
    """Valid cumulative snapshots can still difference into an impossible interval."""

    def _run(self, first, second, third=None):
        recs = [success(1, *first, resumed=False), success(2, *second, resumed=True)]
        if third:
            recs.append(success(3, *third, resumed=True))
        self.ws.write_calls(recs)
        result, _ = account(self.ws)
        return result

    def assertSuppressed(self, call):
        self.assertIsNone(call["usage_delta"])
        self.assertEqual(status(call), "not_estimated")
        self.assertIn("delta_inconsistent", reason_codes(call))
        self.assertIsNone(call["ratios"]["cache_delta"])
        self.assertIsNone(call["ratios"]["reasoning_share_delta"])

    def test_cached_delta_exceeding_input_delta_is_suppressed(self):
        # input/cached 100/20 -> 150/90 is two valid snapshots but delta cached 70 > input 50
        result = self._run((100, 20, 50, 10), (150, 90, 70, 15))
        self.assertSuppressed(by_id(result, 2))
        raw = by_id(result, 2)["usage_raw"]
        self.assertEqual((raw["input_tokens"], raw["cached_input_tokens"]), (150, 90))
        self.assertAlmostEqual(by_id(result, 2)["ratios"]["cache_raw"], 0.6)

    def test_reasoning_delta_exceeding_output_delta_is_suppressed(self):
        # out/reasoning 50/10 -> 70/60 is valid per snapshot but delta reasoning 50 > output 20
        result = self._run((100, 20, 50, 10), (200, 30, 70, 60))
        self.assertSuppressed(by_id(result, 2))

    def test_the_reported_combined_case(self):
        result = self._run((100, 20, 50, 10), (150, 90, 70, 60))
        self.assertSuppressed(by_id(result, 2))

    def test_reseeds_continuity_from_the_later_snapshot_only(self):
        result = self._run((100, 20, 50, 10), (150, 90, 70, 60), (210, 100, 100, 70))
        self.assertSuppressed(by_id(result, 2))
        third = by_id(result, 3)
        self.assertEqual(status(third), "eligible_estimate")
        self.assertEqual(third["usage_delta"]["input_tokens"], 60)       # from 150, not from 100
        self.assertEqual(third["usage_delta"]["cached_input_tokens"], 10)
        self.assertEqual(third["usage_delta"]["output_tokens"], 30)
        self.assertEqual(third["usage_delta"]["reasoning_output_tokens"], 10)

    def test_no_row_ever_reports_an_impossible_ratio(self):
        result = self._run((100, 20, 50, 10), (150, 90, 70, 60), (210, 100, 100, 70))
        for call in result["calls"]:
            for key, value in call["ratios"].items():
                self.assertTrue(value is None or 0 <= value <= 1, (key, value))
            d = call["usage_delta"]
            if d:
                self.assertLessEqual(d["cached_input_tokens"], d["input_tokens"])
                self.assertLessEqual(d["reasoning_output_tokens"], d["output_tokens"])

    def test_coherent_equal_boundary_is_still_an_estimate(self):
        result = self._run((100, 20, 50, 10), (150, 70, 70, 30))  # cached delta 50 == input delta 50
        self.assertEqual(status(by_id(result, 2)), "eligible_estimate")
        self.assertEqual(by_id(result, 2)["ratios"]["cache_delta"], 1.0)


class RawRatioValidity(ObsTestCase):
    """Ratios are derived only from valid counters; raw values are always kept as logged."""

    RATIO_KEYS = ("cache_raw", "cache_delta", "reasoning_share_raw", "reasoning_share_delta")

    def assertNoRatios(self, call):
        for key in self.RATIO_KEYS:
            self.assertIsNone(call["ratios"][key], key)

    def test_impossible_subsets_in_a_fresh_row_give_no_ratios(self):
        # input 100 < cached 150 and output 50 < reasoning 100: previously 1.5 and 2.0
        self.ws.write_calls([success(1, 100, 150, 50, 100, resumed=False)])
        result, _ = account(self.ws)
        c = by_id(result, 1)
        self.assertNoRatios(c)
        self.assertEqual(c["usage_raw"]["input_tokens"], 100)       # raw preserved as logged
        self.assertEqual(c["usage_raw"]["cached_input_tokens"], 150)
        self.assertEqual(c["usage_raw"]["output_tokens"], 50)
        self.assertEqual(c["usage_raw"]["reasoning_output_tokens"], 100)
        self.assertIsNone(c["usage_delta"])
        self.assertEqual(status(c), "not_estimated")
        self.assertIn("usage_invalid", reason_codes(c))
        self.assertEqual(reason_codes(c).count("usage_invalid"), 1)

    def test_each_single_subset_violation_suppresses_every_ratio(self):
        cases = {"cached_gt_input": (100, 150, 50, 10), "reasoning_gt_output": (100, 20, 50, 100)}
        for label, usage in cases.items():
            with self.subTest(label):
                ws = support.Workspace()
                self.addCleanup(ws.cleanup)
                ws.write_calls([success(1, *usage, resumed=False)])
                result, _ = account(ws)
                self.assertNoRatios(by_id(result, 1))
                self.assertIn("usage_invalid", reason_codes(by_id(result, 1)))

    def test_malformed_counters_suppress_all_ratios_even_for_the_valid_pair(self):
        cases = {
            "negative_cached": (1000, -5, 100, 10),
            "string_input": ("1000", 500, 100, 10),
            "float_output": (1000, 500, 100.5, 10),
            "bool_reasoning": (1000, 500, 100, True),
        }
        for label, usage in cases.items():
            with self.subTest(label):
                ws = support.Workspace()
                self.addCleanup(ws.cleanup)
                ws.write_calls([success(1, *usage, resumed=False)])
                result, _ = account(ws)
                self.assertNoRatios(by_id(result, 1))
                self.assertIsNone(by_id(result, 1)["usage_delta"])
                self.assertIn("usage_invalid", reason_codes(by_id(result, 1)))

    def test_missing_parent_fields_leave_ratios_unknown(self):
        self.ws.write_calls([success(1, None, 20, None, 10, resumed=False)])
        result, _ = account(self.ws)
        c = by_id(result, 1)
        self.assertNoRatios(c)
        self.assertEqual(c["usage_raw"]["cached_input_tokens"], 20)  # raw child kept, no parent
        self.assertIsNone(c["usage_raw"]["input_tokens"])

    def test_valid_raw_ratios_remain_when_the_interval_is_unknown(self):
        # first observed resumed call: no baseline, so no per-call estimate, but valid counters
        self.ws.write_calls([success(1, 1000, 500, 100, 10, resumed=True)])
        result, _ = account(self.ws)
        c = by_id(result, 1)
        self.assertEqual(status(c), "not_estimated")
        self.assertIsNone(c["usage_delta"])
        self.assertAlmostEqual(c["ratios"]["cache_raw"], 0.5)
        self.assertAlmostEqual(c["ratios"]["reasoning_share_raw"], 0.1)
        self.assertIsNone(c["ratios"]["cache_delta"])
        self.assertIsNone(c["ratios"]["reasoning_share_delta"])

    def test_unsupported_cli_stays_without_derived_figures_even_when_valid(self):
        self.ws.write_calls([success(1, 1000, 500, 100, 10, resumed=False, cli="9.9.9")])
        result, _ = account(self.ws)
        self.assertNoRatios(by_id(result, 1))
        self.assertEqual(by_id(result, 1)["usage_raw"]["input_tokens"], 1000)

    def test_failed_and_uncertain_rows_with_invalid_counters_derive_nothing(self):
        bad = {"input_tokens": 100, "cached_input_tokens": 150, "output_tokens": 50,
               "reasoning_output_tokens": 100, "total_tokens": None}
        timeout = failure(2, outcome="timeout", inference=True, thread=None, usage=dict(bad))
        partial = success(3, 100, 150, 50, 100, resumed=True, outcome="success_partial_persistence",
                          session_state="in_doubt", thread="t-3", name="three")
        self.ws.write_calls([success(1, 1000, 500, 100, 10, resumed=False), timeout, partial])
        result, _ = account(self.ws)
        for n in (2, 3):
            c = by_id(result, n)
            self.assertNoRatios(c)
            self.assertEqual(c["usage_raw"]["cached_input_tokens"], 150)  # raw still shown
            self.assertIn("usage_invalid", reason_codes(c))
        self.assertIn("inference_started_unmeasured", reason_codes(by_id(result, 2)))
        self.assertIn("recovery_uncertain", reason_codes(by_id(result, 3)))

    def test_valid_counters_on_a_failed_row_keep_their_raw_ratio_and_no_invalid_reason(self):
        ok = {"input_tokens": 1000, "cached_input_tokens": 500, "output_tokens": 100,
              "reasoning_output_tokens": 10, "total_tokens": None}
        self.ws.write_calls([failure(1, outcome="timeout", inference=True, thread=None, usage=ok)])
        result, _ = account(self.ws)
        c = by_id(result, 1)
        self.assertAlmostEqual(c["ratios"]["cache_raw"], 0.5)
        self.assertNotIn("usage_invalid", reason_codes(c))

    def test_three_turn_values_and_ratios_are_unchanged(self):
        self.ws.write_calls(three_turn_records())
        result, _ = account(self.ws)
        self.assertEqual([by_id(result, n)["usage_delta"]["input_tokens"] for n in (1, 2, 3)],
                         [181960, 172553, 118896])
        self.assertAlmostEqual(by_id(result, 2)["ratios"]["cache_delta"], 167424 / 172553, delta=1e-4)
        self.assertAlmostEqual(by_id(result, 1)["ratios"]["cache_raw"], 136448 / 181960, delta=1e-4)


class UsageBasisLabels(ObsTestCase):
    def test_basis_is_unknown_for_unsupported_cli_missing_cli_schema_and_conflict(self):
        missing_cli = success(3, 1000, 500, 100, 10, resumed=False)
        del missing_cli["cli_version"]
        future = {"schema_version": 2, "call_id": support.call_id(4), "started_at": "2026-10-14T08:00:00Z"}
        dup = success(5, 1000, 500, 100, 10, resumed=False)
        dup_other = dict(dup, run_ms=7)
        self.ws.write_calls([success(1, 1000, 500, 100, 10, resumed=False),
                             success(2, 1000, 500, 100, 10, resumed=False, cli="9.9.9", thread="t-2",
                                     name="two"), missing_cli, future, dup, dup_other])
        result, _ = account(self.ws)
        self.assertEqual(by_id(result, 1)["usage_basis"], "session_cumulative")
        for n in (2, 3, 4, 5):
            self.assertEqual(by_id(result, n)["usage_basis"], "unknown", n)
        # raw counters stay visible for the unsupported-CLI rows without derived claims
        self.assertEqual(by_id(result, 2)["usage_raw"]["input_tokens"], 1000)
        self.assertIsNone(by_id(result, 2)["usage_delta"])
        self.assertIsNone(by_id(result, 2)["ratios"]["cache_raw"])


class Inflight(ObsTestCase):
    def test_running_marker_projection_and_elapsed(self):
        self.ws.write_marker("a.json", {
            "state": "running", "phase": "running", "call_id": support.call_id(9),
            "owner_pid": 1234, "owner_identity": "secret-ish", "started_at": "2026-10-14T08:50:00Z",
            "resumed": False, "thread_id": None, "ownership": "pending", "model": "gpt-6.1-sol",
            "repo_path": "C:/private/repo"})
        result, _ = account(self.ws)
        m = result["inflight"][0]
        self.assertEqual(m["kind"], "running")
        self.assertEqual(m["elapsed_s"], 600)
        self.assertNotIn("owner_pid", m)
        self.assertNotIn("owner_identity", m)
        self.assertNotIn("repo_path", m)
        self.assertFalse(m["possibly_stale"])

    def test_marker_for_recorded_call_is_flagged_stale(self):
        self.ws.write_calls(three_turn_records())
        self.ws.write_marker("a.json", {"state": "running", "phase": "turn_completed",
                                        "call_id": support.call_id(1), "started_at": "2026-10-14T07:37:45Z"})
        result, _ = account(self.ws)
        self.assertTrue(result["inflight"][0]["recorded_in_telemetry"])
        self.assertTrue(result["inflight"][0]["possibly_stale"])

    def test_uncertain_state_and_reason_code(self):
        self.ws.write_marker("a.json", {"state": "uncertain", "reason": "owner_not_alive",
                                        "since": "2026-10-14T08:00:00Z", "call_id": support.call_id(5)})
        self.ws.write_marker("b.json", {"state": "uncertain", "reason": "C:\\Users\\x\\<script>",
                                        "since": "2026-10-14T08:00:00Z", "call_id": support.call_id(6)})
        result, _ = account(self.ws)
        by_call = {m["call_id"]: m for m in result["inflight"]}
        self.assertEqual(by_call[support.call_id(5)]["reason"], "owner_not_alive")
        self.assertEqual(by_call[support.call_id(6)]["reason"], "unrecognized")
        self.assertEqual(by_call[support.call_id(5)]["kind"], "uncertain")

    def test_future_or_invalid_timestamp_gives_unknown_elapsed(self):
        self.ws.write_marker("a.json", {"state": "running", "started_at": "2030-01-01T00:00:00Z"})
        self.ws.write_marker("b.json", {"state": "running", "started_at": "nope"})
        result, _ = account(self.ws)
        self.assertTrue(all(m["elapsed_s"] is None for m in result["inflight"]))


if __name__ == "__main__":
    unittest.main()
