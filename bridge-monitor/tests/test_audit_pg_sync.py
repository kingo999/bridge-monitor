"""Run with a disposable PostgreSQL database named bridge_monitor_test_*."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import psycopg
import pytest

from bridge.audit_pg_sync import sync
from bridge.outbound_audit import Journal, Observation, Policy


def test_migrate_retry_concurrency_and_rollback(test_dsn, tmp_path, monkeypatch):
    ddl = Path(__file__).resolve().parents[1] / "database/migrations/014_outbound_audit.sql"
    tables = "outbound_audit." + ", outbound_audit.".join(
        ("source", "policy", "active", "observations", "roots", "process_state"))
    with psycopg.connect(test_dsn) as db:
        created = db.execute("SELECT to_regnamespace('outbound_audit')").fetchone()[0] is None
        if created:
            db.execute(ddl.read_text())
        else:
            db.execute("TRUNCATE " + tables)
    try:
        journal = Journal(tmp_path / "capture.sqlite")
        policy = Policy(default="allow", rules=())
        journal.activate(policy)
        journal.register(123, 456, {"run": "example"})
        journal.register_shared(124, 457)
        journal.record(Observation(kind="attempt"))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: sync(journal.path, test_dsn, verify_all=True), range(2)))
        assert all(r["verified_rows"] == dict(policy=1, active=1, roots=1, process_state=1,
                                               observations=1) for r in results)
        from bridge import audit_pg_sync
        stage = audit_pg_sync.stage_table

        def append_while_reading(source, target, name, columns, after):
            if name == "roots":
                journal.record(Observation(kind="transfer", bytes_out=0))
            return stage(source, target, name, columns, after)

        with monkeypatch.context() as patch:
            patch.setattr(audit_pg_sync, "stage_table", append_while_reading)
            assert sync(journal.path, test_dsn)["high_id"] == 1
        with pytest.raises(psycopg.OperationalError):
            sync(journal.path, "host=127.0.0.1 port=1 dbname=unavailable")
        assert sync(journal.path, test_dsn)["high_id"] == 2
        with closing(sqlite3.connect(journal.path)) as db, db:
            db.execute("UPDATE process_state SET ended_ns=123")
        sync(journal.path, test_dsn)
        with psycopg.connect(test_dsn) as db:
            assert db.execute("SELECT ended_ns FROM outbound_audit.process_state").fetchone() == (123,)
        with closing(sqlite3.connect(journal.path)) as db, db:
            db.execute("INSERT INTO roots VALUES (NULL, 5, 6, '{}')")
        with pytest.raises(ValueError, match="null source key"):
            sync(journal.path, test_dsn)
        with closing(sqlite3.connect(journal.path)) as db, db:
            db.execute("DELETE FROM roots WHERE host IS NULL")
        with pytest.raises(ValueError, match="identity differs"):
            sync(Journal(tmp_path / "other.sqlite").path, test_dsn)
        with closing(sqlite3.connect(journal.path)) as db, db:
            db.execute("UPDATE observations SET decision='changed' WHERE id=1")
        journal.record(Observation(kind="gap"))
        with pytest.raises(ValueError, match="retained rows differ"):
            sync(journal.path, test_dsn, verify_all=True)
        with psycopg.connect(test_dsn) as db:
            assert db.execute("SELECT last_id FROM outbound_audit.source").fetchone() == (2,)
            assert db.execute("SELECT count(*) FROM outbound_audit.observations").fetchone() == (2,)
    finally:
        with psycopg.connect(test_dsn) as db:
            db.execute("DROP SCHEMA outbound_audit CASCADE" if created else "TRUNCATE " + tables)
