import sqlite3

import pytest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from skynet_agent import jobs
from skynet_agent.jobs import JobJournal, OutputBuffer


def test_duplicate_submit_returns_same_job_without_replacing_identity(tmp_path):
    journal = JobJournal(tmp_path / "jobs.sqlite")
    first, created = journal.accept("child-1", "a" * 64, "qwen")
    repeated, repeated_created = journal.accept("child-1", "a" * 64, "qwen")

    assert created is True
    assert repeated_created is False
    assert repeated == first
    with pytest.raises(ValueError, match="conflicting request"):
        journal.accept("child-1", "b" * 64, "qwen")


def test_restart_marks_nonterminal_job_unknown_without_replay(tmp_path):
    path = tmp_path / "jobs.sqlite"
    first = JobJournal(path)
    first.accept("child-1", "a" * 64, "qwen")
    first.set_state("child-1", "streaming")

    restarted = JobJournal(path)
    assert restarted.get("child-1")["state"] == "unknown"


def test_journal_closes_sqlite_connections_during_streaming_and_errors(tmp_path, monkeypatch):
    class TrackedConnection(sqlite3.Connection):
        opened = 0
        closed = 0

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            type(self).opened += 1

        def close(self):
            super().close()
            type(self).closed += 1

    real_connect = sqlite3.connect

    def tracked_connect(*args, **kwargs):
        return real_connect(*args, factory=TrackedConnection, **kwargs)

    monkeypatch.setattr(jobs.sqlite3, "connect", tracked_connect)
    journal = JobJournal(tmp_path / "jobs.sqlite")
    journal.accept("child-1", "a" * 64, "qwen")
    for sequence in range(1, 51):
        journal.note_output("child-1", sequence)
    with pytest.raises(ValueError, match="output sequence did not advance"):
        journal.note_output("child-1", 50)

    assert TrackedConnection.opened == TrackedConnection.closed


def test_output_buffer_reports_missing_sequence_instead_of_skipping():
    output = OutputBuffer(max_bytes=8)
    output.append("abcd")
    output.append("efgh")
    output.append("ij")

    assert output.page(0)["gap"] is True
    assert output.page(1)["deltas"] == [
        {"sequence": 2, "text": "efgh"}, {"sequence": 3, "text": "ij"}]


def test_concurrent_duplicate_submit_creates_one_job(tmp_path):
    journal = JobJournal(tmp_path / "jobs.sqlite")
    gate = Barrier(8)

    def submit(_):
        gate.wait()
        return journal.accept("child-1", "a" * 64, "qwen")[1]

    with ThreadPoolExecutor(max_workers=8) as pool:
        created = list(pool.map(submit, range(8)))
    assert created.count(True) == 1
    assert created.count(False) == 7


def test_terminal_state_cannot_be_overwritten_by_late_cancel(tmp_path):
    journal = JobJournal(tmp_path / "jobs.sqlite")
    journal.accept("child-1", "a" * 64, "qwen")
    journal.set_state("child-1", "completed", '{"done":true}')
    journal.set_state("child-1", "cancel_requested")
    assert journal.get("child-1")["state"] == "completed"
    assert journal.get("child-1")["terminal_json"] == '{"done":true}'


def test_retention_prunes_only_old_proven_terminal_receipts(tmp_path):
    journal = JobJournal(tmp_path / "jobs.sqlite")
    for job, state in (("done", "completed"), ("uncertain", "unknown")):
        journal.accept(job, "a" * 64, "qwen")
        journal.set_state(job, state)
    with journal._connect() as conn:
        conn.execute("UPDATE jobs SET updated_at='2000-01-01T00:00:00+00:00'")
    assert journal.prune_terminal(30) == ["done"]
    assert journal.get("done") is None
    assert journal.get("uncertain") is not None
