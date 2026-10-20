"""Windows status-file contention must not turn a transient reader into a crash."""
import ctypes
from ctypes import wintypes
import errno
import json
import os
import threading

import pytest

from bridge.classifier import supervisor as service

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows rename contention')


def test_real_reader_without_delete_sharing_is_tolerated(tmp_path, monkeypatch):
    path = tmp_path / 'service.json'
    path.write_text('{"status":"old"}')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    # Read/write sharing is allowed; only delete/rename sharing is denied.
    handle = kernel.CreateFileW(str(path), 0x80000000, 0x1 | 0x2, None, 3, 0, None)
    assert handle != wintypes.HANDLE(-1).value
    release = threading.Event()
    errors = []
    original = os.replace
    def replace(source, destination):
        try:
            return original(source, destination)
        except OSError as exc:
            errors.append(exc.winerror)
            release.set()
            raise
    monkeypatch.setattr(service.os, 'replace', replace)
    def close_reader():
        release.wait(5)
        # Keep the real Windows handle open across at least one retry.
        threading.Event().wait(.025)
        kernel.CloseHandle(handle)
    reader = threading.Thread(target=close_reader)
    reader.start()
    try:
        service.atomic_json(path, {'status': 'new'})
    finally:
        release.set()
        reader.join(timeout=6)
    assert not reader.is_alive()
    assert errors and set(errors) <= {5, 32, 33}
    assert json.loads(path.read_text()) == {'status': 'new'}


@pytest.mark.parametrize('winerror', [5, 32, 33])
def test_transient_replace_errors_retry_only_the_closed_complete_file(tmp_path, monkeypatch, winerror):
    path = tmp_path / 'service.json'
    path.write_text('{"status":"old"}')
    expected = {'status': 'new', 'text': 'Příliš žluťoučký'}
    attempts, delays = [], []
    def replace(source, destination):
        attempts.append(source)
        assert json.loads(source.read_text(encoding='utf-8')) == expected
        assert json.loads(destination.read_text()) == {'status': 'old'}
        if len(attempts) < 3:
            raise ctypes.WinError(winerror)
        # The real rename is covered above. Keep injected-error accounting
        # independent of additional scanner interference on the test host.
        return None
    monkeypatch.setattr(service.os, 'replace', replace)
    monkeypatch.setattr(service.time, 'sleep', delays.append)
    service.atomic_json(path, expected)
    assert len(attempts) == 3 and len(set(attempts)) == 1
    assert delays == [.01, .02]
    assert json.loads(attempts[0].read_text(encoding='utf-8')) == expected
    assert json.loads(path.read_text()) == {'status': 'old'}


@pytest.mark.parametrize('winerror', [5, 32, 33])
def test_persistent_denial_is_bounded_and_preserves_last_valid_status(tmp_path, monkeypatch, winerror, caplog):
    path = tmp_path / 'service.json'
    path.write_text('{"status":"old"}')
    error = ctypes.WinError(winerror)
    attempts, delays = [], []
    def denied(*args):
        attempts.append(1)
        raise error
    monkeypatch.setattr(service.os, 'replace', denied)
    monkeypatch.setattr(service.time, 'sleep', delays.append)
    with pytest.raises(OSError) as raised:
        service.atomic_json(path, {'status': 'new', 'private': 'must-not-appear-in-logs'})
    assert raised.value is error
    assert len(attempts) == 6
    assert delays == [.01, .02, .04, .08, .16]
    assert sum(delays) <= .311
    assert json.loads(path.read_text()) == {'status': 'old'}
    assert f'winerror={winerror}' in caplog.text
    assert 'must-not-appear-in-logs' not in caplog.text


@pytest.mark.parametrize('winerror', [3, 112, None])
def test_other_io_errors_are_not_retried(tmp_path, monkeypatch, winerror):
    error = (ctypes.WinError(winerror) if winerror is not None
             else PermissionError(errno.EACCES, 'ordinary permission denial'))
    path = tmp_path / 'service.json'
    path.write_text('{"status":"old"}')
    attempts, delays = [], []
    def denied(*args):
        attempts.append(1)
        raise error
    monkeypatch.setattr(service.os, 'replace', denied)
    monkeypatch.setattr(service.time, 'sleep', delays.append)
    with pytest.raises(OSError) as raised:
        service.atomic_json(path, {'status': 'new'})
    assert raised.value is error
    assert len(attempts) == 1 and delays == []
    assert json.loads(path.read_text()) == {'status': 'old'}
