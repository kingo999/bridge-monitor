import sqlite3
import subprocess
from pathlib import Path
import pytest

from bridge.audit_collector import Collector, EPOCH
from bridge.outbound_audit import Journal


def worker(journal):
    born = EPOCH + 1000
    journal.register(10, born, {"agent": "A", "run": "run-A"})
    collector = Collector(journal)
    collector.line(f"start\t10\t{born}\t1\tworker.exe")
    collector.line(f"start\t11\t{born+1}\t10\thelper.exe")
    return collector, born


def send(collector, born):
    collector.line(f"net\t{born+10}\t11\t10\t192.0.2.8\t443\t12")


def test_helper_is_unknown_after_root_ends(tmp_path):
    journal = Journal(tmp_path / "audit.db")
    collector, born = worker(journal)
    collector.line(f"end\t10\t{born}\t{born+5}")
    send(collector, born)
    rows = [r["evidence"] for r in journal.records()]
    assert not any(r["kind"] == "gap" for r in rows)
    assert "run" not in rows[-1]


def test_explicit_shared_helper_does_not_inherit_root_owner(tmp_path):
    journal = Journal(tmp_path / "audit.db")
    collector, born = worker(journal)
    journal.register_shared(11, born+1)
    send(collector, born)
    assert "run" not in list(journal.records())[-1]["evidence"]


def test_old_unknown_does_not_remove_reused_pid(tmp_path):
    journal = Journal(tmp_path / "audit.db")
    collector, born = worker(journal)
    collector.line(f"unknown\t10\t{born-1}")
    collector.line(f"net\t{born+10}\t10\t10\t192.0.2.8\t443\t12")
    assert list(journal.records())[-1]["evidence"]["run"] == "run-A"


def test_busy_writer_buffers_and_recovers_without_duplicates(tmp_path):
    journal = Journal(tmp_path / "audit.db")
    collector, born = worker(journal)
    with journal.connect() as locked:
        locked.execute("BEGIN IMMEDIATE")
        send(collector, born)
        assert collector.pending
    collector.flush()
    rows = [r["evidence"] for r in journal.records()]
    assert len(rows) == 1 and rows[0]["run"] == "run-A"


def test_busy_buffer_overflow_is_reported_on_recovery(tmp_path):
    journal = Journal(tmp_path / "audit.db")
    collector, born = worker(journal)
    collector.max_pending = 2
    with journal.connect() as locked:
        locked.execute("BEGIN IMMEDIATE")
        for _ in range(4):
            send(collector, born)
    collector.flush()
    rows = [r["evidence"] for r in journal.records()]
    assert len([r for r in rows if r["kind"] == "transfer"]) == 2
    assert sum(r.get("lost_events", 0) for r in rows if r["kind"] == "gap") == 2


def test_native_cache_restores_python_identity_after_malformed_input(tmp_path):
    reader = Path(__file__).parents[1] / "var/outbound-audit-build/decoder-test.exe"
    if not reader.is_file():
        pytest.skip("build the native decoder fixture first")
    frames = subprocess.check_output([str(reader), "--recover"], text=True).splitlines()
    first = next(row.split("\t") for row in frames if row.startswith("start\t"))
    journal = Journal(tmp_path / "audit.db")
    journal.register(int(first[1]), int(first[2]), {"agent": "fixture", "run": "fixture-run"})
    collector = Collector(journal)
    for frame in frames:
        collector.line(frame)
    transfers = [r["evidence"] for r in journal.records() if r["evidence"]["kind"] == "transfer"]
    assert len(transfers) == 2 and all(row.get("run") == "fixture-run" for row in transfers)


def test_lifetime_overflow_also_removes_buffered_attribution(tmp_path):
    journal = Journal(tmp_path / "audit.db")
    collector, born = worker(journal)
    collector.max_pending = 2
    with journal.connect() as locked:
        locked.execute("BEGIN IMMEDIATE")
        send(collector, born)
        for pid in (10, 20, 30):
            collector.line(f"end\t{pid}\t{born}\t{born+5}")
    collector.flush()
    rows = [r["evidence"] for r in journal.records()]
    assert collector.lifetimes_unreliable
    assert all("run" not in row for row in rows)
