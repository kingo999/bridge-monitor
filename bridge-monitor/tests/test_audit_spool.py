import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from bridge.outbound_audit import Journal


def consumer(tmp_path):
    from bridge.audit_spool import SpoolConsumer
    spool = tmp_path / 'metadata.tsv'
    spool.write_bytes(b'')
    journal = Journal(tmp_path / 'audit.sqlite')
    cursor = tmp_path / 'cursor.json'
    return SpoolConsumer(journal, spool, cursor), journal, spool, cursor


def transfers(journal):
    return [r['evidence'] for r in journal.records() if r['evidence']['kind'] == 'transfer']


FRAME = b'net\t134000000000000010\t123\t10\t192.0.2.8\t443\t12\n'


def test_partial_line_is_not_consumed_until_complete(tmp_path):
    reader, journal, spool, _ = consumer(tmp_path)
    spool.write_bytes(FRAME[:-1])
    reader.tick()
    assert not transfers(journal)
    with spool.open('ab') as out:
        out.write(b'\n')
    reader.tick()
    assert len(transfers(journal)) == 1
    reader.close()


def test_checkpoint_prevents_replay_after_clean_restart(tmp_path):
    from bridge.audit_spool import SpoolConsumer
    reader, journal, spool, cursor = consumer(tmp_path)
    spool.write_bytes(FRAME)
    reader.tick()
    reader.close()
    resumed = SpoolConsumer(journal, spool, cursor)
    resumed.tick()
    assert len(transfers(journal)) == 1
    assert any('resumed' in r['evidence'].get('operation', '') for r in journal.records())
    resumed.close()


def test_replaced_and_truncated_spools_report_gaps(tmp_path):
    reader, journal, spool, _ = consumer(tmp_path)
    spool.write_bytes(FRAME * 2)
    reader.tick()
    spool.write_bytes(FRAME)
    reader.tick()
    replacement = tmp_path / 'replacement'
    replacement.write_bytes(FRAME)
    replacement.replace(spool)
    reader.tick()
    assert len(transfers(journal)) == 4
    assert sum('spool changed' in r['evidence'].get('operation', '') for r in journal.records()) == 2
    reader.close()


def test_busy_database_does_not_advance_durable_cursor(tmp_path):
    reader, journal, spool, cursor = consumer(tmp_path)
    reader.tick()
    prior = cursor.read_bytes()
    spool.write_bytes(FRAME)
    with journal.connect() as locked:
        locked.execute('BEGIN IMMEDIATE')
        reader.tick()
        assert cursor.read_bytes() == prior
        reader.tick()
        assert cursor.read_bytes() == prior
    reader.tick()
    assert len(transfers(journal)) == 1
    assert cursor.read_bytes() != prior
    reader.close()


def test_oversized_line_is_bounded_and_following_record_survives(tmp_path):
    reader, journal, spool, _ = consumer(tmp_path)
    spool.write_bytes(b'x' * 100000 + b'\n' + FRAME)
    for _ in range(5):
        reader.tick()
    assert len(transfers(journal)) == 1
    assert any('oversized' in r['evidence'].get('operation', '') for r in journal.records())
    reader.close()


def test_malicious_text_is_only_invalid_metadata(tmp_path):
    reader, journal, spool, _ = consumer(tmp_path)
    spool.write_bytes(b'Invoke-Expression evil\n' + FRAME)
    reader.tick()
    assert len(transfers(journal)) == 1
    assert any(r['evidence'].get('operation') == 'invalid reader metadata' for r in journal.records())
    reader.close()


def test_invalid_checkpoint_replays_from_start_with_gap(tmp_path):
    from bridge.audit_spool import SpoolConsumer
    reader, journal, spool, cursor = consumer(tmp_path)
    reader.close()
    cursor.write_text('{"version": 1, "offset": -1, "identity": [1,2]}')
    spool.write_bytes(FRAME)
    recovered = SpoolConsumer(journal, spool, cursor)
    recovered.tick()
    recovered.close()
    assert len(transfers(journal)) == 1
    assert any('invalid checkpoint' in row['evidence'].get('operation', '') for row in journal.records())


def test_missing_spool_waits_without_creating_it(tmp_path):
    reader, _, spool, _ = consumer(tmp_path)
    spool.unlink()
    reader.tick()
    assert not spool.exists()
    reader.close()


def test_restart_preserves_discard_of_an_oversized_partial_record(tmp_path):
    from bridge.audit_spool import SpoolConsumer
    reader, journal, spool, cursor = consumer(tmp_path)
    spool.write_bytes(b'x' * 65537)
    reader.tick()
    reader.close()
    with spool.open('ab') as out:
        out.write(FRAME + FRAME)
    resumed = SpoolConsumer(journal, spool, cursor)
    resumed.tick()
    resumed.close()
    assert len(transfers(journal)) == 1


def test_failed_checkpoint_flush_keeps_previous_checkpoint(tmp_path, monkeypatch):
    reader, _, _, cursor = consumer(tmp_path)
    reader.tick()
    previous = cursor.read_bytes()
    def fail(_):
        raise OSError('simulated power-loss flush error')
    monkeypatch.setattr(os, 'fsync', fail)
    with pytest.raises(OSError, match='flush error'):
        reader._save()
    assert cursor.read_bytes() == previous


def test_line_size_limit_includes_newline(tmp_path):
    reader, journal, spool, _ = consumer(tmp_path)
    prefix = b'start\t1\t134000000000000000\t0\t'
    spool.write_bytes(prefix + b'x' * (65535-len(prefix)) + b'\n')
    reader.tick()
    assert not any('oversized' in row['evidence'].get('operation', '') for row in journal.records())
    reader.close()


@pytest.mark.skipif(os.name != 'nt', reason='Windows token')
def test_consumer_refuses_admin_before_creating_a_journal(tmp_path, monkeypatch):
    from bridge import audit_spool
    monkeypatch.setattr(audit_spool.ctypes.windll.shell32, 'IsUserAnAdmin', lambda: True)
    database = tmp_path / 'must-not-exist.sqlite'
    with pytest.raises(SystemExit) as error:
        audit_spool.main(['--db', str(database), '--spool', str(tmp_path/'spool'), '--checkpoint', str(tmp_path/'cursor')])
    assert error.value.code == 2
    assert not database.exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows byte-range lock')
def test_single_consumer_lock_is_enforced_between_processes(tmp_path):
    from bridge.audit_spool import consumer_lock
    cursor = tmp_path/'cursor'
    script = 'from pathlib import Path;from bridge.audit_spool import consumer_lock\nwith consumer_lock(Path(__import__("sys").argv[1])): print("locked")'
    with consumer_lock(cursor):
        result = subprocess.run([sys.executable, '-c', script, str(cursor)], capture_output=True, text=True)
        assert result.returncode != 0
        assert 'locked' not in result.stdout
    result = subprocess.run([sys.executable, '-c', script, str(cursor)], capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == 'locked'


def test_concurrent_writer_with_fragmented_lines(tmp_path):
    reader, journal, spool, _ = consumer(tmp_path)
    def write():
        with spool.open('ab', buffering=0) as out:
            for _ in range(50):
                out.write(FRAME[:17])
                out.write(FRAME[17:])
    writer = threading.Thread(target=write)
    writer.start()
    deadline = time.monotonic()+5
    while time.monotonic() < deadline:
        reader.tick()
        if not writer.is_alive() and len(transfers(journal)) == 50:
            break
        time.sleep(.001)
    writer.join(timeout=1)
    reader.close()
    assert len(transfers(journal)) == 50


def test_native_identity_resynchronizes_after_spool_consumer_restart(tmp_path):
    from bridge.audit_spool import SpoolConsumer
    fixture = Path(__file__).parents[1]/'var/outbound-audit-build/decoder-test.exe'
    if not fixture.is_file():
        pytest.skip('native synthetic fixture not built')
    frames = subprocess.check_output([str(fixture),'--recover']).splitlines(keepends=True)
    first = next(row.decode().strip().split('\t') for row in frames if row.startswith(b'start\t'))
    split = next(i+1 for i,row in enumerate(frames) if row.startswith(b'net\t'))
    reader,journal,spool,cursor = consumer(tmp_path)
    journal.register(int(first[1]),int(first[2]),{'agent':'fixture','run':'spool-resync'})
    spool.write_bytes(b''.join(frames[:split]))
    reader.tick(); reader.close()
    with spool.open('ab') as output:
        output.write(b''.join(frames[split:]))
    resumed = SpoolConsumer(journal,spool,cursor)
    resumed.tick(); resumed.close()
    assert len(transfers(journal)) == 2
    assert all(row.get('run') == 'spool-resync' for row in transfers(journal))
