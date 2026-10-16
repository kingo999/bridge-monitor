"""Process-tree ownership for one Codex turn (Windows Job Object; POSIX process group).

Why a Job Object (F3): ``taskkill /T`` walks parent links and only works while the parent
is alive; it cannot tell us whether descendants are really gone, and it does nothing when
the bridge itself is killed.  A Job Object with ``KILL_ON_JOB_CLOSE`` gives

* **ownership** - every process the Codex CLI starts afterwards is a member (we do not allow
  breakaway), so descendants outlive neither ``TerminateJobObject`` nor the bridge;
* **confirmation** - ``ActiveProcesses`` in the job's accounting is an authoritative count
  of live members, so "terminated" can be *confirmed* instead of merely attempted;
* **hard-termination safety** - if the bridge is killed, the OS closes the job handle and
  kills the members.

Limits (do not overclaim):

* assignment happens just after ``Popen`` returns; a process spawned by the child in that
  window is not a member (milliseconds; the node launcher needs far longer to start);
* processes started **outside** our tree - notably commands launched by the Windows
  sandbox *service* (``codex-windows-sandbox-service.exe``) - are not job members, cannot
  be enumerated here, and their termination can never be confirmed by this mechanism;
* nested jobs need Windows 8+; if assignment fails the caller falls back to ``taskkill``
  and must report ownership as unavailable.
"""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
from typing import Any

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    from ctypes import wintypes as wt

    _JobObjectBasicAccountingInformation = 1
    _JobObjectExtendedLimitInformation = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _BASIC_LIMIT(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wt.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wt.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wt.DWORD),
            ("SchedulingClass", wt.DWORD),
        ]

    class _EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BASIC_LIMIT),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _BASIC_ACCOUNTING(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_longlong),
            ("TotalKernelTime", ctypes.c_longlong),
            ("ThisPeriodTotalUserTime", ctypes.c_longlong),
            ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
            ("TotalPageFaultCount", wt.DWORD),
            ("TotalProcesses", wt.DWORD),
            ("ActiveProcesses", wt.DWORD),
            ("TotalTerminatedProcesses", wt.DWORD),
        ]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
    _k32.CreateJobObjectW.restype = wt.HANDLE
    _k32.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
    _k32.SetInformationJobObject.restype = wt.BOOL
    _k32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
    _k32.AssignProcessToJobObject.restype = wt.BOOL
    _k32.QueryInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD,
                                               ctypes.POINTER(wt.DWORD)]
    _k32.QueryInformationJobObject.restype = wt.BOOL
    _k32.TerminateJobObject.argtypes = [wt.HANDLE, wt.UINT]
    _k32.TerminateJobObject.restype = wt.BOOL
    _k32.CloseHandle.argtypes = [wt.HANDLE]
    _k32.CloseHandle.restype = wt.BOOL


class ProcessOwnership:
    """Owns (and can terminate / count) the process tree of one child.

    ``owned`` is False when ownership could not be established; callers must then treat
    any termination as *attempted*, never *confirmed*.
    """

    def __init__(self) -> None:
        self._job: Any = None
        self._pgid: int | None = None
        self.owned = False
        self.mechanism = "none"
        self.error: str | None = None

    # ------------------------------------------------------------- acquire
    def attach(self, proc: subprocess.Popen[bytes]) -> bool:
        if IS_WINDOWS:
            return self._attach_windows(proc)
        try:
            self._pgid = os.getpgid(proc.pid)
            self.owned = True
            self.mechanism = "process_group"
        except OSError as exc:
            self.error = f"getpgid:{exc.__class__.__name__}"
        return self.owned

    def _attach_windows(self, proc: subprocess.Popen[bytes]) -> bool:
        job = _k32.CreateJobObjectW(None, None)
        if not job:
            self.error = f"CreateJobObject:{ctypes.get_last_error()}"
            return False
        info = _EXTENDED_LIMIT()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _k32.SetInformationJobObject(job, _JobObjectExtendedLimitInformation, ctypes.byref(info),
                                            ctypes.sizeof(info)):
            self.error = f"SetInformationJobObject:{ctypes.get_last_error()}"
            _k32.CloseHandle(job)
            return False
        process_handle = wt.HANDLE(int(proc._handle))  # type: ignore[attr-defined]  # noqa: SLF001
        if not _k32.AssignProcessToJobObject(job, process_handle):
            self.error = f"AssignProcessToJobObject:{ctypes.get_last_error()}"
            _k32.CloseHandle(job)
            return False
        self._job = job
        self.owned = True
        self.mechanism = "job_object"
        return True

    # ------------------------------------------------------------- observe
    def active_processes(self) -> int | None:
        """Live members of the owned tree; None when it cannot be determined."""
        if not self.owned:
            return None
        if IS_WINDOWS and self._job:
            acct = _BASIC_ACCOUNTING()
            ok = _k32.QueryInformationJobObject(self._job, _JobObjectBasicAccountingInformation,
                                                ctypes.byref(acct), ctypes.sizeof(acct), None)
            return int(acct.ActiveProcesses) if ok else None
        if self._pgid is not None:
            try:
                os.killpg(self._pgid, 0)
                return 1  # group still has at least one live member
            except ProcessLookupError:
                return 0
            except OSError:
                return None
        return None

    # ------------------------------------------------------------- act
    def terminate(self) -> None:
        """Kill every owned process (never anything outside the owned tree)."""
        if IS_WINDOWS and self._job:
            _k32.TerminateJobObject(self._job, 1)
        elif self._pgid is not None:
            try:
                os.killpg(self._pgid, signal.SIGKILL)
            except OSError:
                pass

    def close(self) -> None:
        """Release the handle; with KILL_ON_JOB_CLOSE this also kills any remaining member."""
        if IS_WINDOWS and self._job:
            _k32.CloseHandle(self._job)
        self._job = None
