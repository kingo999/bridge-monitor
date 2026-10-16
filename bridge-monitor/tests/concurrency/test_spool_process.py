import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from bridge.telemetry.spool import DurableSpool


def _wait_for(path: Path, *, seconds: float = 10) -> None:
    deadline = time.monotonic() + seconds
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"probe marker missing: {path.name}")
        time.sleep(0.01)


def _hold_drain(path: str, ready: str, release: str):
    spool = DurableSpool(path, limit_bytes=100000)
    with spool.drain_lock():
        Path(ready).write_text("ready", encoding="utf-8")
        _wait_for(Path(release))


def _compete_for_drain(path: str, attempting: str, acquired: str):
    spool = DurableSpool(path, limit_bytes=100000)
    Path(attempting).write_text("attempting", encoding="utf-8")
    with spool.drain_lock():
        Path(acquired).write_text("acquired", encoding="utf-8")


def test_two_processes_cannot_drain_one_spool_simultaneously(tmp_path):
    path = tmp_path / "shared.sqlite"
    ready, release = tmp_path / "ready", tmp_path / "release"
    attempting, acquired = tmp_path / "attempting", tmp_path / "acquired"
    with ProcessPoolExecutor(max_workers=2) as processes:
        holder = processes.submit(_hold_drain, str(path), str(ready), str(release))
        _wait_for(ready)
        contender = processes.submit(_compete_for_drain, str(path), str(attempting),
                                     str(acquired))
        _wait_for(attempting)
        try:
            time.sleep(0.1)
            assert not acquired.exists()
        finally:
            release.write_text("release", encoding="utf-8")
        holder.result(timeout=10)
        contender.result(timeout=10)
    assert acquired.exists()
