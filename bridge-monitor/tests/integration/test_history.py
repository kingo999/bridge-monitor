from pathlib import Path

import psycopg
import pytest

from contracts.queries import CohortQuery
from database.migrate import migrate
from scripts.replay_fixtures import replay_fixture
from service.queries.history import list_work_items, inspect_exchange


FIXTURE = Path(__file__).parents[1] / "fixtures" / "demonstration.json"


@pytest.fixture
def seeded(test_dsn):
    migrate(test_dsn)
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE publication_stream, events, content_objects, producer_sequences, tasks RESTART IDENTITY CASCADE")
        conn.execute("UPDATE publication_counter SET next_position=1 WHERE singleton=true")
    replay_fixture(test_dsn, FIXTURE)
    return test_dsn


def test_history_is_bounded_and_inspector_returns_exact_observed_bodies(seeded):
    page = list_work_items(seeded, CohortQuery(origin="synthetic"), limit=10, offset=0)
    assert len(page) == 10
    assert "raw_bytes" not in page[0]
    selected = inspect_exchange(seeded, "demo-work-061", role="operator")
    assert selected["work_item_id"] == "demo-work-061"
    assert selected["original_prompt"] == "What is 2 + 2?\n"
    assert selected["local_response"] == "4\n"


def test_inspection_rejects_non_operator_and_unknown_item(seeded):
    with pytest.raises(PermissionError):
        inspect_exchange(seeded, "demo-work-061", role="producer")
    assert inspect_exchange(seeded, "missing", role="operator") is None


def test_capacity_and_capture_fixtures_project_without_runtime_migration(seeded):
    with psycopg.connect(seeded) as conn:
        assert conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall() == [
            ("001_event_store",)]
        assert conn.execute("""SELECT active,queued,held_uncertain,blocked,
            capture_state,known_gaps,spool_pressure FROM dashboard_status WHERE singleton=true""").fetchone() == (
            1, 2, 1, 3, "healthy", 1, 0.8)


def test_each_delegated_demonstration_item_has_visible_prompt_and_response(seeded):
    selected = inspect_exchange(seeded, "demo-work-100", role="operator")
    assert selected["original_prompt"] == "What is 2 + 2?\n"
    assert selected["local_response"] == "4\n"
