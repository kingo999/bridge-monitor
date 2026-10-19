"""Windows process identity and termination helpers (ctypes, no third-party packages).

Identity is PID + full image path + creation time at 100 ns FILETIME precision, so a
reused PID or a replaced executable never matches a launcher receipt.
"""

import ctypes
import os
import socket
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
SYNCHRONIZE = 0x00100000
TH32CS_SNAPPROCESS = 0x2
_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)

_k32 = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None


class _ProcessEntry(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    image_path: str
    created_ticks: int  # 100 ns units since 1601-01-01 UTC

    @property
    def created_utc(self) -> str:
        """ISO-8601 with 7 fractional digits; never rounded to milliseconds."""
        whole, ticks = divmod(self.created_ticks, 10_000_000)
        moment = _EPOCH + timedelta(seconds=whole)
        return moment.strftime("%Y-%m-%dT%H:%M:%S") + f".{ticks:07d}Z"


def _open(pid: int, access: int):
    handle = _k32.OpenProcess(access, False, pid)
    return handle or None


def identify(pid: int) -> ProcessIdentity | None:
    handle = _open(pid, PROCESS_QUERY_LIMITED_INFORMATION)
    if not handle:
        return None
    try:
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not _k32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                    ctypes.byref(kernel), ctypes.byref(user)):
            return None
        if exited.dwHighDateTime or exited.dwLowDateTime:
            return None  # already exited
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not _k32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return ProcessIdentity(pid, buffer.value, ticks)
    finally:
        _k32.CloseHandle(handle)


def same_image(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def children(parent_pid: int) -> list[int]:
    """Direct children from a process snapshot (descendants found by repeated calls)."""
    snapshot = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    found: list[int] = []
    entry = _ProcessEntry()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        ok = _k32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.th32ParentProcessID == parent_pid and entry.th32ProcessID != parent_pid:
                found.append(entry.th32ProcessID)
            ok = _k32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        _k32.CloseHandle(snapshot)
    return found


def descendants(parent_pid: int) -> list[int]:
    out, queue = [], [parent_pid]
    while queue:
        for child in children(queue.pop()):
            if child not in out:
                out.append(child)
                queue.append(child)
    return out


def terminate(pid: int) -> bool:
    handle = _open(pid, PROCESS_TERMINATE)
    if not handle:
        return False
    try:
        return bool(_k32.TerminateProcess(handle, 1))
    finally:
        _k32.CloseHandle(handle)


def wait_exit(pid: int, timeout: float) -> bool:
    handle = _open(pid, SYNCHRONIZE)
    if not handle:
        return True  # cannot be opened: already gone
    try:
        return _k32.WaitForSingleObject(handle, int(timeout * 1000)) == 0
    finally:
        _k32.CloseHandle(handle)


def port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0
