from pathlib import Path

import psycopg
import pytest

from contracts.queries import CohortQuery
from database.migrate import migrate
from scripts.replay_fixtures import replay_fixture
from service.queries.snapshot import CursorExpired, read_publications, read_snapshot


@pytest.fixture
def seeded(test_dsn):
    migrate(test_dsn)
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE publication_stream, events, content_objects, producer_sequences, tasks RESTART IDENTITY CASCADE")
        conn.execute("UPDATE publication_counter SET next_position=1 WHERE singleton=true")
    replay_fixture(test_dsn, Path(__file__).parents[1] / "fixtures" / "edge-cases.json")
    return test_dsn


def test_replay_is_commit_ordered_bounded_and_deduplicated(seeded):
    first = read_publications(seeded, 0, limit=2)
    second = read_publications(seeded, first[-1]["position"], limit=2)
    assert len(first) == len(second) == 2
    assert first[-1]["position"] < second[0]["position"]
    assert len({row["event_id"] for row in first + second}) == 4


def test_expired_cursor_requires_explicit_reset(seeded):
    snapshot = read_snapshot(seeded, CohortQuery(origin="synthetic"))
    with psycopg.connect(seeded) as conn:
        conn.execute("DELETE FROM publication_stream WHERE position < 3")
    with pytest.raises(CursorExpired) as error:
        read_publications(seeded, 0, limit=10)
    assert error.value.earliest_position == 3
    assert read_snapshot(seeded, CohortQuery(origin="synthetic")).cursor.position == snapshot.cursor.position


def test_empty_retained_window_still_resets_to_last_committed_cursor(seeded):
    last = read_snapshot(seeded, CohortQuery(origin="synthetic")).cursor.position
    with psycopg.connect(seeded) as conn:
        conn.execute("DELETE FROM publication_stream")
    with pytest.raises(CursorExpired):
        read_publications(seeded, 0)
    assert read_snapshot(seeded, CohortQuery(origin="synthetic")).cursor.position == last
