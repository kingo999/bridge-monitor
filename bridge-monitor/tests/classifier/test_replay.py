"""Replay scoring keeps raw recommendations, policy verdicts and reference labels separate."""

import json
from pathlib import Path

import pytest

from bridge.classifier.replay import run_replay, schedule
from tests.classifier.fakes import FakeOllama, reply_json

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
REGRESSIONS = FIXTURES / "classifier-regressions-20261018.json"
HELD_OUT = FIXTURES / "classifier-heldout-20261019.json"


def test_c08_and_t08_regressions_are_preserved_with_original_labels():
    c08, t08 = json.loads(REGRESSIONS.read_text(encoding="utf-8"))
    assert (c08["id"], c08["preferred_route"], c08["facts"]["code_available"],
            c08["facts"]["paid_allowed"]) == ("C08", "clarify", False, False)
    assert (t08["id"], t08["preferred_route"], t08["facts"]["general_available"]) == ("T08", "paid", False)


def test_unsafe_raw_recommendations_are_not_scored_as_successes(tmp_path):
    cases = json.loads(REGRESSIONS.read_text(encoding="utf-8"))
    fake = FakeOllama(reply_json(route="code_local", category="code"))
    summary = run_replay(fake.classifier(), cases, [42, 43], tmp_path / "run", label="unit",
                         cases_path=REGRESSIONS)
    first = summary["by_pass"][0]
    assert first["decisions"] == 2 and first["valid_contract"] == 2
    assert first["raw_route_acceptable"] == 0 and first["allowed_and_acceptable"] == 0
    # C08: code worker unavailable and paid prohibited -> blocked. T08 asks for code_local while
    # only the general worker is unavailable: policy allows it, yet the reference label rejects it.
    assert first["unsafe_recommendations"] == 1 and first["policy_allowed"] == 1
    assert {u["case_id"] for u in summary["unsafe"]} == {"C08"}
    assert summary["unsafe"][0]["blocks"] == ["code_worker_unavailable"]
    assert {f["case_id"] for f in summary["failures"]} == {"C08", "T08"}
    records = [json.loads(line) for line in (tmp_path / "run" / "responses.jsonl").read_text().splitlines()]
    assert {r["recommendation"]["route"] for r in records} == {"code_local"}   # never relabelled
    assert (tmp_path / "run" / "warmup.json").exists() and (tmp_path / "run" / "manifest.json").exists()


def test_replay_requires_a_fresh_evidence_directory(tmp_path):
    cases = json.loads(REGRESSIONS.read_text(encoding="utf-8"))
    run_replay(FakeOllama().classifier(), cases, [42], tmp_path / "run", label="a", cases_path=REGRESSIONS)
    with pytest.raises(FileExistsError):
        run_replay(FakeOllama().classifier(), cases, [42], tmp_path / "run", label="b",
                   cases_path=REGRESSIONS)


def test_replay_refuses_to_run_against_the_wrong_model_digest(tmp_path):
    cases = json.loads(REGRESSIONS.read_text(encoding="utf-8"))
    fake = FakeOllama(digest="2" * 64)
    with pytest.raises(RuntimeError, match="model_digest_mismatch"):
        run_replay(fake.classifier(), cases, [42], tmp_path / "run", label="x", cases_path=REGRESSIONS)
    assert fake.chat_calls == 0


def test_pass_order_matches_the_original_seeded_shuffle():
    ids = [f"C{i}" for i in range(10)]
    assert schedule(ids, [42, 43])[0]["case_ids"] == schedule(ids, [42, 43])[0]["case_ids"]
    assert [b["seed"] for b in schedule(ids, [42, 43])] == [42, 43]


def test_held_out_set_is_well_formed_and_covers_the_required_situations():
    cases = json.loads(HELD_OUT.read_text(encoding="utf-8"))
    from contracts.classifier import ClassifierInput, ReferenceLabel
    for case in cases:
        ClassifierInput(request=case["request"], source=case["source"], facts=case["facts"])
        ReferenceLabel(category=case["category"], preferred_route=case["preferred_route"],
                       acceptable_routes=case["acceptable_routes"])
    facts = [c["facts"] for c in cases]
    assert any(f.get("context_complete") is False for f in facts)          # incomplete context
    assert any(f.get("tests_present") is True for f in facts)              # trusted tests present
    assert any(f.get("tests_present") is False for f in facts)             # trusted tests absent
    assert any(f.get("security_sensitive") for f in facts)                 # security-sensitive
    assert any(f.get("scope") == "multi_file" for f in facts)              # multi-file
    assert any(f["code_available"] is False or f["general_available"] is False for f in facts)
    assert any(c["preferred_route"] == "clarify" and c["category"] == "ambiguous" for c in cases)
