"""Ordinary-user ingestion of the privileged reader's append-only metadata spool.

Spool contents are parsed as data. Local network-table refresh runs under the
ordinary user. Checkpoints follow committed batches; crash recovery may replay.
"""
import argparse
import ctypes
import json
import os
import subprocess
import time
from pathlib import Path
from contextlib import contextmanager

from bridge.audit_collector import Collector
from bridge.outbound_audit import Journal


class SpoolConsumer:
    def __init__(self, journal, spool, checkpoint):
        self.collector = Collector(journal, batch_size=128)
        self.spool, self.checkpoint = Path(spool), Path(checkpoint)
        self.stream = None
        self.identity = None
        self.offset = 0
        self.discarding = False
        if self.checkpoint.exists():
            try:
                if self.checkpoint.stat().st_size > 4096:
                    raise ValueError('oversized checkpoint')
                saved = json.loads(self.checkpoint.read_text(encoding='utf-8'))
                if (not isinstance(saved, dict) or saved.get('version') != 1
                        or type(saved.get('offset')) is not int or saved['offset'] < 0
                        or not isinstance(saved.get('identity'), list) or len(saved['identity']) != 2
                        or any(type(n) is not int for n in saved['identity'])
                        or type(saved.get('discarding', False)) is not bool):
                    raise ValueError('invalid checkpoint')
                self.identity = tuple(saved['identity'])
                self.offset = saved['offset']
                self.discarding = saved.get('discarding', False)
            except ValueError:
                self.collector.status('gap', 'invalid checkpoint; replaying retained spool from start')
        self.collector.status('gap', 'spool consumer resumed; crash replay and earlier gaps possible')

    def _save(self):
        if self.identity is None:
            return
        state = dict(version=1, identity=self.identity, offset=self.offset, discarding=self.discarding)
        temporary = self.checkpoint.with_name(self.checkpoint.name + '.tmp')
        with temporary.open('w', encoding='utf-8') as output:
            output.write(json.dumps(state))
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(self.checkpoint)

    def tick(self):
        try:
            self._tick()
        finally:
            # Do not prevent administrator rotation/replacement on Windows.
            if self.stream:
                self.stream.close()
                self.stream = None

    def _tick(self):
        # Backpressure on database contention: leave the protected spool on disk.
        if not self.collector.flush():
            return
        self._save()
        try:
            stat = self.spool.stat()
        except FileNotFoundError:
            return
        identity = (stat.st_dev, stat.st_ino)
        if identity != self.identity or stat.st_size < self.offset:
            if self.identity is not None:
                self.collector.processes.clear()
                self.collector.status('gap', 'spool changed or truncated; earlier evidence may be missing')
            if self.stream:
                self.stream.close()
                self.stream = None
            self.identity, self.offset, self.discarding = identity, 0, False
        if self.stream is None:
            self.stream = self.spool.open('rb')
            opened = os.fstat(self.stream.fileno())
            if (opened.st_dev, opened.st_ino) != self.identity:
                self.stream.close()
                self.stream = None
                return  # A replacement raced open; reconcile on the next tick.
            self.stream.seek(self.offset)
        for _ in range(256):
            start = self.stream.tell()
            raw = self.stream.readline(65537)
            if not raw:
                break
            if self.discarding:
                self.discarding = not raw.endswith(b'\n')
            elif len(raw) > 65536:
                self.collector.processes.clear()
                self.collector.status('gap', 'oversized spool metadata discarded')
                self.discarding = not raw.endswith(b'\n')
            elif not raw.endswith(b'\n'):
                self.stream.seek(start)
                break
            else:
                self.collector.line(raw.decode('utf-8', errors='replace'))
            self.offset = self.stream.tell()
            if len(self.collector.pending) >= self.collector.batch_size and not self.collector.flush():
                return
        if self.collector.flush():
            self._save()

    def close(self):
        if self.collector.flush():
            self._save()
        if self.stream:
            self.stream.close()


@contextmanager
def consumer_lock(checkpoint):
    import msvcrt
    with open(str(checkpoint) + '.lock', 'a+b') as lock:
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('--spool', required=True, type=Path)
    parser.add_argument('--checkpoint', required=True, type=Path)
    args = parser.parse_args(argv)
    if os.name != 'nt':
        parser.error('Windows is required for the single-consumer lock')
    if ctypes.windll.shell32.IsUserAnAdmin():
        parser.error('run the spool consumer with an ordinary user token')
    # These commands run as the ordinary operator, never as the SYSTEM producer.
    from bridge.audit_cli import refresh_addresses
    with consumer_lock(args.checkpoint):
        reader = SpoolConsumer(Journal(args.db), args.spool, args.checkpoint)
        try:
            refreshed = -60.0
            while True:
                if time.monotonic() - refreshed >= 60:
                    refreshed = time.monotonic()
                    try:
                        refresh_addresses(reader.collector)
                    except (OSError, ValueError, subprocess.SubprocessError):
                        # Cached names are hints only; preserve the coverage gap.
                        reader.collector.cached_names.clear()
                        reader.collector.status('gap', 'local network tables unavailable')
                reader.tick()
                time.sleep(0.25)
        finally:
            reader.close()


if __name__ == '__main__':
    main()
