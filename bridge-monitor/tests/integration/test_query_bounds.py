"""Dashboard history stays paged and bodies remain on-demand."""

from pathlib import Path
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from contracts.queries import CohortQuery
from database.migrate import migrate
from scripts.replay_fixtures import replay_fixture
from service.queries.history import list_work_items, read_work_item_detail
from service.queries.snapshot import read_publications, read_snapshot


def test_history_and_publication_pages_are_bounded_without_embedded_bodies(test_dsn):
    migrate(test_dsn)
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE publication_stream, events, content_objects, "
                     "producer_sequences, tasks RESTART IDENTITY CASCADE")
        conn.execute("UPDATE publication_counter SET next_position=1 WHERE singleton=true")
    replay_fixture(test_dsn, Path(__file__).parents[1] / "fixtures" / "demonstration.json")
    cohort = CohortQuery(origin="synthetic")
    page = list_work_items(test_dsn, cohort, limit=1)
    assert len(page) == 1
    detail = read_work_item_detail(test_dsn, page[0]["work_item_id"])
    assert detail is not None
    assert "raw_bytes" not in str(detail)
    assert len(detail["events"]) <= 500
    assert len(read_publications(test_dsn, 0, limit=1)) == 1
    assert len(read_snapshot(test_dsn, cohort, limit=1).work_items) <= 1
    with pytest.raises(ValueError, match="bounds"):
        list_work_items(test_dsn, cohort, limit=101)
    with pytest.raises(ValueError, match="bounds"):
        read_publications(test_dsn, 0, limit=501)


def test_filtered_history_scans_in_bounded_pages(test_dsn, monkeypatch):
    migrate(test_dsn)
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE tasks RESTART IDENTITY CASCADE")
        now = datetime.now(timezone.utc)
        for index in range(230):
            task_id = f"paged-task-{index:03d}"
            work_id = f"paged-work-{index:03d}"
            created = now + timedelta(microseconds=index)
            conn.execute("INSERT INTO tasks(task_id,origin,created_at) "
                "VALUES(%s,'synthetic',%s)", (task_id, created))
            conn.execute("INSERT INTO work_items(work_item_id,task_id,origin,created_at) "
                "VALUES(%s,%s,'synthetic',%s)", (work_id, task_id, created))
    import service.queries.history as history
    actual = history.work_guidance
    batch_sizes = []

    def bounded(conn, ids, as_of):
        batch_sizes.append(len(ids))
        assert len(ids) <= 100
        return actual(conn, ids, as_of)

    monkeypatch.setattr(history, "work_guidance", bounded)
    # The future timestamp used for deterministic ordering must be inside the cohort.
    cohort = CohortQuery(origin="synthetic", guidance_group="not_dispatched",
                         as_of=now + timedelta(seconds=1))
    page = list_work_items(test_dsn, cohort, limit=5, offset=210)
    assert [item["work_item_id"] for item in page] == [
        f"paged-work-{index:03d}" for index in range(19, 14, -1)]
    assert len(batch_sizes) >= 3
