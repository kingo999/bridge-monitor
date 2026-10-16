from pathlib import Path

import psycopg
import pytest

from contracts.queries import CohortQuery
from database.migrate import migrate
from scripts.replay_fixtures import replay_fixture
from service.queries.metrics import query_metrics


FIXTURE = Path(__file__).parents[1] / "fixtures" / "edge-cases.json"


@pytest.fixture
def seeded(test_dsn):
    migrate(test_dsn)
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE publication_stream, events, content_objects, producer_sequences, tasks RESTART IDENTITY CASCADE")
        conn.execute("UPDATE publication_counter SET next_position=1 WHERE singleton=true")
    return test_dsn


def test_edge_case_fixture_replays_and_keeps_leaf_accounting(seeded):
    first = replay_fixture(seeded, FIXTURE)
    second = replay_fixture(seeded, FIXTURE)
    assert first and set(first) == {"committed"}
    assert second == ["duplicate"] * len(first)
    report = query_metrics(seeded, CohortQuery(origin="synthetic"))
    assert (report.metrics["routed"].numerator, report.metrics["routed"].denominator) == (5, 7)
    assert report.metrics["not_routed"].numerator == 1
    assert report.metrics["pending"].numerator == 1
    assert (report.coverage["disposition"].numerator,
            report.coverage["disposition"].denominator) == (3, 3)
    assert report.metrics["confirmed_acceptance"].numerator == 2
    assert report.metrics["contribution"].numerator == 3
    with psycopg.connect(seeded) as conn:
        assert conn.execute("SELECT count(*) FROM work_items").fetchone()[0] == 8
        assert conn.execute("SELECT count(*) FROM results").fetchone()[0] == 3
        assert conn.execute("SELECT count(*) FROM results WHERE result_id='edge-late-result'").fetchone()[0] == 0
        states = conn.execute("""SELECT guidance_manifest->>'state' FROM delegation_decisions
            WHERE work_item_id='edge-mixed' ORDER BY decision_id""").fetchall()
        assert states == [("no_guidance",), ("selected",)]
        assert conn.execute("SELECT count(*) FROM dispositions WHERE supersedes_id IS NOT NULL").fetchone()[0] == 1
