"""v2 session registry: versioned JSON, atomic writes, cross-process locks.

Layout of the state directory (default ``~/.gpt-architect-bridge-v2``)::

    instance.json        identity + `registry_established` (first init vs loss, F4)
    registry.json        versioned registry (see SCHEMA_VERSION); registry.prev.json = last good copy
    registry.lock        short critical sections around registry read/modify/write
    locks/<hash>.lock    held for the whole duration of one call on one session
    inflight/<hash>.json running / uncertain call marker (process ownership, F3)
    pending/<hash>.json  thread produced but pointer not saved (F5); blocks silent new threads
    journal.jsonl        content-free lifecycle journal (thread ids, outcomes)
    maintenance.json     administrative recovery in progress; new calls refuse (F4)
    telemetry/           see telemetry.py

Nothing here ever deletes Codex history, and a missing/corrupt/mismatched
session is never silently replaced: it surfaces as a BridgeError and the
registry file is left exactly as found.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from typing import Any, Mapping
import uuid

from . import config, errors

SCHEMA_VERSION = 1
from .ids import THREAD_RE as _THREAD_RE  # noqa: E402  (single definition, see ids.py)


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------- paths


def canonical_repo(raw: str | None) -> tuple[Path, str]:
    """Return (canonical root, repo key).  Raises BridgeError for non-repos."""
    candidate = raw if raw else os.getcwd()
    real = os.path.realpath(candidate)
    if os.name == "nt" and real.startswith("\\\\?\\") and not real.startswith("\\\\?\\UNC\\"):
        real = real[4:]
    root = Path(real)
    if not root.is_dir():
        raise errors.BridgeError(
            errors.INVALID_CONFIGURATION,
            f"Project directory does not exist: {root}",
            code="no_project_dir",
            recovery="Launch Claude Code from the project root or set CLAUDE_PROJECT_DIR.",
        )
    if not (root / ".git").exists():
        raise errors.BridgeError(
            errors.INVALID_CONFIGURATION,
            f"Expected a Git repository at {root}.",
            code="not_a_repo",
            recovery="Launch Claude Code from the project root (or configure CLAUDE_PROJECT_DIR).",
        )
    key = hashlib.sha256(os.path.normcase(str(root)).encode("utf-8")).hexdigest()[:32]
    return root, key


def _is_within(child: Path, parent: Path) -> bool:
    c = os.path.normcase(os.path.realpath(child))
    p = os.path.normcase(os.path.realpath(parent))
    return c == p or c.startswith(p.rstrip("\\/") + os.sep)


def validate_state_dir(state_dir: Path, repo_root: Path | None, home: Path) -> None:
    """State must be outside reviewed repositories and separate from v1."""
    v1 = home / config.V1_STATE_DIRNAME
    if _is_within(state_dir, v1) or _is_within(v1, state_dir):
        raise errors.BridgeError(
            errors.INVALID_CONFIGURATION,
            f"v2 state directory {state_dir} overlaps the v1 state directory {v1}.",
            code="state_overlaps_v1",
            recovery="Unset GPT_ARCHITECT_V2_STATE_DIR or point it somewhere separate from v1.",
        )
    if repo_root is not None and (_is_within(state_dir, repo_root) or _is_within(repo_root, state_dir)):
        raise errors.BridgeError(
            errors.INVALID_CONFIGURATION,
            f"v2 state directory {state_dir} overlaps the reviewed repository {repo_root}.",
            code="state_in_repo",
            recovery="State must live outside the reviewed repository (default: ~/.gpt-architect-bridge-v2).",
        )


# --------------------------------------------------------------------------- atomic IO


def atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    data = text.encode("utf-8")
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        last: OSError | None = None
        for _ in range(10):  # Windows: replace can transiently fail under AV/indexers
            try:
                os.replace(tmp, path)
                return
            except PermissionError as exc:
                last = exc
                time.sleep(0.02)
        assert last is not None
        raise last
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------- locks


class LockTimeout(Exception):
    pass


class LockCancelled(Exception):
    pass


class FileLock:
    """Advisory cross-process lock on one byte of a file (msvcrt / fcntl).

    The OS releases it if the holder dies, so a killed bridge never leaves a
    stale lock behind.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def acquire(
        self,
        timeout: float,
        cancel: threading.Event | None = None,
        poll: float = 0.05,
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + max(0.0, timeout)
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    raise LockCancelled()
                if self._try_lock(fd):
                    self._fd = fd
                    return
                if time.monotonic() >= deadline:
                    raise LockTimeout(str(self.path))
                time.sleep(poll)
        except BaseException:
            os.close(fd)
            raise

    @staticmethod
    def _try_lock(fd: int) -> bool:
        if sys.platform == "win32":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                return False
        import fcntl

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                try:
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def __enter__(self) -> "FileLock":
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


# --------------------------------------------------------------------------- registry

#: Lock ordering (never violate; documented in README "Locks and what they cover"):
#:     session lock  ->  registry lock (short)  ->  journal/telemetry lock (leaf)
#: Administrative maintenance uses the maintenance flag first, then quiescence probes
#: (non-blocking session-lock tries), then the registry lock.  Nothing takes a session
#: lock while holding the registry lock.


def session_key(repo_key: str, model: str, session: str) -> str:
    return f"{repo_key}:{model}:{session}"


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def process_alive(pid: int) -> bool:
    """True if a process with this PID currently exists (PID reuse makes this conservative)."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


#: returned by process_identity when a process exists but its identity cannot be read
#: (e.g. access denied): it must never be read as "the owner is dead".
UNQUERYABLE = "unqueryable"

_ERROR_INVALID_PARAMETER = 87   # OpenProcess: no process has that PID


def process_identity(pid: int) -> str | None:
    """Stable identity of a running process (creation time), None only when it is *verifiably*
    not running, or UNQUERYABLE when it exists but cannot be queried.

    PID alone is not an identity (Windows reuses PIDs); pid + creation time is."""
    if pid <= 0:
        return None
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes as wt

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wt.HANDLE
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            # only "no such process" proves absence; access denied etc. means it may well exist
            return None if ctypes.get_last_error() == _ERROR_INVALID_PARAMETER else UNQUERYABLE
        try:
            created, exited, kernel, user = wt.FILETIME(), wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
            if not k32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                       ctypes.byref(kernel), ctypes.byref(user)):
                return UNQUERYABLE
            if exited.dwHighDateTime or exited.dwLowDateTime:
                return None      # it has exited (a handle elsewhere merely keeps the object alive)
            return str((created.dwHighDateTime << 32) | created.dwLowDateTime)
        finally:
            k32.CloseHandle(handle)
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[-1].split()[19]
    except Exception:  # noqa: BLE001
        return str(pid) if process_alive(pid) else None


def owner_is_alive(pid: int, identity: str | None) -> bool:
    """True if the recorded owner is (or cannot be ruled out to be) still running.

    Conservative: with no recorded identity, a live PID counts as alive; a process that cannot
    be queried counts as alive; only a reused PID with a *different readable* creation time, or a
    PID that verifiably does not exist, counts as dead."""
    current = process_identity(pid)
    if current is None:
        return False
    if current == UNQUERYABLE or identity in (None, UNQUERYABLE):
        return True          # exists, and the recorded/current identity cannot be compared
    return current == identity


def _empty_registry() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "created_at": utc_now(), "sessions": {}, "retired": []}


class Journal:
    """Append-only, content-free event log of lifecycle facts (never prompt/source/answer).

    Records *what happened to bridge state* (thread bound / not bound, termination
    attempted vs confirmed, reconciliation) so that an operator can reconcile the bridge
    pointer with Codex history.  Best effort: a failure here never changes a call result.
    """

    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / "journal.jsonl"
        self._lock_path = state_dir / "journal.lock"

    def append(self, event: str, **fields: Any) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            record = {"ts": utc_now(), "event": event, **fields}
            line = json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
            lock = FileLock(self._lock_path)
            lock.acquire(5.0)
            try:
                with open(self.path, "ab") as handle:
                    handle.write(line.encode("utf-8"))
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                lock.release()
            return True
        except Exception:  # noqa: BLE001
            return False

    def read(self) -> list[dict[str, Any]]:
        try:
            return [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]
        except (OSError, json.JSONDecodeError):
            return []


class Registry:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.path = state_dir / "registry.json"
        self.prev_path = state_dir / "registry.prev.json"
        self.instance_path = state_dir / "instance.json"
        self.maintenance_path = state_dir / "maintenance.json"
        self._lock_path = state_dir / "registry.lock"
        self.journal = Journal(state_dir)
        self._maint_lock: FileLock | None = None

    # -- plumbing ---------------------------------------------------------
    def ensure_dirs(self) -> None:
        try:
            for sub in ("locks", "telemetry", "inflight", "pending"):
                (self.state_dir / sub).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise errors.BridgeError(
                errors.STATE_ERROR,
                f"Cannot create state directory {self.state_dir}: {exc}",
                code="state_dir_unwritable",
                recovery="Fix permissions or set GPT_ARCHITECT_V2_STATE_DIR to a writable location.",
            ) from exc

    def _registry_lock(self, timeout: float = 10.0) -> FileLock:
        lock = FileLock(self._lock_path)
        try:
            lock.acquire(timeout)
        except LockTimeout:
            raise errors.BridgeError(
                errors.STATE_ERROR,
                "Timed out waiting for the registry lock.",
                code="registry_lock_timeout",
                recovery="Another bridge process is stuck; check for hung `codex` processes and retry.",
            ) from None
        return lock

    def session_lock(self, key: str) -> FileLock:
        return FileLock(self.state_dir / "locks" / f"{key_hash(key)}.lock")

    # -- instance identity (F4: first initialisation vs loss) ---------------
    def instance_state(self) -> tuple[str, dict[str, Any] | None]:
        """("absent" | "unreadable" | "invalid" | "ok", data).  Never conflates the three
        non-ok cases with a first initialisation: only "absent" can be one."""
        try:
            raw = self.instance_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return "absent", None
        except OSError:
            return "unreadable", None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return "invalid", None
        if not (isinstance(data, dict) and isinstance(data.get("instance_id"), str)
                and re.fullmatch(r"[0-9a-f]{32}", data["instance_id"])
                and isinstance(data.get("registry_established"), bool)):
            return "invalid", None
        return "ok", data

    def read_instance(self) -> dict[str, Any] | None:
        status, data = self.instance_state()
        return data if status == "ok" else None

    def _instance_problem(self, status: str) -> errors.BridgeError:
        return errors.BridgeError(
            errors.SESSION_CORRUPT,
            f"{self.instance_path.name} is {status}; the bridge cannot tell first initialisation from "
            "loss of an established registry and will not guess.",
            code=f"instance_{status}",
            recovery="Inspect the file (`bridge_admin.py status`). Nothing was changed; restore it from a "
                     "backup or `bridge_admin.py reinit --yes` after quarantining the registry.",
        )

    def _ensure_instance(self) -> dict[str, Any]:
        status, inst = self.instance_state()
        if status in ("unreadable", "invalid"):
            raise self._instance_problem(status)
        if inst is None:  # absent: first initialisation
            inst = {"instance_id": uuid.uuid4().hex, "created_at": utc_now(), "registry_established": False,
                    "schema_version": SCHEMA_VERSION}
            atomic_write_text(self.instance_path, json.dumps(inst, indent=2, sort_keys=True) + "\n")
        return inst

    # -- maintenance flag (F4: block new calls during administrative recovery) --
    def maintenance(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.maintenance_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"reason": "unreadable"}
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            return {"reason": "unreadable"}

    def assert_not_in_maintenance(self) -> None:
        flag = self.maintenance()
        if flag is not None:
            raise errors.BridgeError(
                errors.STATE_ERROR,
                f"Bridge state is under administrative maintenance ({flag.get('reason', 'unspecified')}).",
                code="state_maintenance",
                recovery="Wait for the administrator to finish (`bridge_admin.py status`), or clear a "
                         "stale flag with `bridge_admin.py end-maintenance --yes` after confirming no "
                         "admin command is running.",
            )

    def begin_maintenance(self, reason: str) -> None:
        """Take *exclusive* ownership: an OS lock held for the whole maintenance lifecycle
        (released by the OS if the admin dies), then the visible flag.  A second admin fails
        immediately; it can neither share nor clear this one's flag."""
        lock = FileLock(self.state_dir / "maintenance.lock")
        try:
            lock.acquire(0.0)
        except LockTimeout:
            raise errors.BridgeError(errors.STATE_ERROR, "Another administrative command is running.",
                                     code="maintenance_busy",
                                     recovery="Wait for it to finish; nothing was changed.") from None
        # Holding the exclusive lock proves no live admin owns a flag, so an EXISTING flag is stale.
        # It is never silently overwritten (V9): the operator clears it explicitly.
        if self.maintenance_path.exists():
            lock.release()
            raise errors.BridgeError(
                errors.STATE_ERROR,
                "A stale maintenance flag exists (an earlier administrative command did not finish); it "
                "is not overwritten automatically.",
                code="maintenance_stale",
                recovery="After confirming no admin command is running: `bridge_admin.py end-maintenance "
                         "--yes`, then retry. Nothing was changed.",
            )
        self._maint_lock = lock
        try:
            atomic_write_text(self.maintenance_path, json.dumps(
                {"reason": reason, "owner_pid": os.getpid(), "owner_identity": process_identity(os.getpid()),
                 "since": utc_now()}, sort_keys=True) + "\n")
        except OSError:
            self.end_maintenance()
            raise

    def end_maintenance(self) -> bool:
        """Clear the flag only if THIS process owns it, then release the lock - the lock is
        released on EVERY path (V9: a failed unlink must not leave it held).  Returns False when
        the flag could not be removed (it then stays as a stale flag for `end-maintenance`)."""
        lock = getattr(self, "_maint_lock", None)
        if lock is None:
            return True
        self._maint_lock = None
        cleared = True
        try:
            try:
                self.maintenance_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                cleared = False
        finally:
            lock.release()
        return cleared

    def end_stale_maintenance(self) -> bool:
        """Operator clears a flag whose owner is gone.  Refuses while any admin still holds the
        maintenance lock.  Returns True if a flag was removed."""
        probe = FileLock(self.state_dir / "maintenance.lock")
        try:
            probe.acquire(0.0)
        except LockTimeout:
            raise errors.BridgeError(errors.STATE_ERROR, "An administrative command is still running.",
                                     code="maintenance_busy") from None
        try:
            existed = self.maintenance_path.exists()
            self.maintenance_path.unlink(missing_ok=True)
            return existed
        finally:
            probe.release()

    def active_sessions(self) -> list[str]:
        """Lock-file names currently held by a live call (non-blocking probes)."""
        held = []
        locks = self.state_dir / "locks"
        if not locks.is_dir():
            return held
        for path in sorted(locks.glob("*.lock")):
            probe = FileLock(path)
            try:
                probe.acquire(0.0)
            except LockTimeout:
                held.append(path.stem)
            except OSError:
                held.append(path.stem)
            else:
                probe.release()
        return held

    # -- registry file ----------------------------------------------------
    def _read(self) -> dict[str, Any]:
        corrupt = lambda code, why: errors.BridgeError(  # noqa: E731
            errors.SESSION_CORRUPT,
            f"Session registry {self.path} is unusable: {why}. It was left untouched.",
            code=code,
            recovery=(
                "Inspect the file; if it is damaged, `bridge_admin.py restore-prev --yes` restores the "
                "previous version, or `bridge_admin.py quarantine --yes` then `reinit --yes` starts an "
                "explicit empty registry (sessions then need `adopt`). Codex history is not affected."
            ),
        )
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            status, inst = self.instance_state()
            if status in ("unreadable", "invalid"):
                raise self._instance_problem(status)
            if inst is not None and inst.get("registry_established"):
                raise errors.BridgeError(
                    errors.SESSION_CORRUPT,
                    f"The session registry {self.path.name} is MISSING but this state directory had an "
                    "established registry. The bridge will not silently start replacement sessions.",
                    code="registry_lost",
                    recovery=(
                        "If you did not do this deliberately, check backups and "
                        "`bridge_admin.py restore-prev --yes` (restores registry.prev.json if present). "
                        "Otherwise `bridge_admin.py reinit --yes` creates an explicit empty registry; "
                        "existing Codex threads stay in Codex history and can be re-attached with "
                        "`bridge_admin.py adopt`. Codex history is not affected."
                    ),
                ) from None
            return _empty_registry()  # first initialisation
        except OSError as exc:
            raise errors.BridgeError(
                errors.STATE_ERROR, f"Cannot read {self.path}: {exc}", code="registry_unreadable",
                recovery="Check file permissions; the file was not modified.",
            ) from exc
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise corrupt("registry_unparseable", f"not valid JSON ({exc.__class__.__name__})") from None
        if not isinstance(data, dict):
            raise corrupt("registry_shape", "top level is not an object")
        version = data.get("schema_version")
        if version != SCHEMA_VERSION:
            raise corrupt("schema_unsupported", f"schema_version {version!r} (expected {SCHEMA_VERSION})")
        if not isinstance(data.get("sessions"), dict) or not isinstance(data.get("retired", []), list):
            raise corrupt("registry_shape", "'sessions'/'retired' have the wrong type")
        status, inst = self.instance_state()
        if status in ("unreadable", "invalid"):
            raise self._instance_problem(status)
        reg_inst = data.get("instance_id")
        if not isinstance(reg_inst, str) or inst is None:
            raise errors.BridgeError(
                errors.SESSION_MISMATCH,
                "The registry's identity cannot be verified "
                + ("(it carries no instance_id)" if not isinstance(reg_inst, str) else "(instance.json is missing)")
                + "; the bridge will not trust it.",
                code="registry_identity_unverifiable",
                recovery="Nothing was modified. Inspect with `bridge_admin.py status`. There is no automatic "
                         "migration: if you trust this registry, `bridge_admin.py quarantine --yes`, then "
                         "`reinit --yes`, and `adopt` the threads you want to keep (Codex history is "
                         "not affected).",
            )
        if reg_inst != inst["instance_id"]:
            raise errors.BridgeError(
                errors.SESSION_MISMATCH,
                "The registry belongs to a different state-directory instance than instance.json "
                "(it was copied or restored from elsewhere).",
                code="registry_instance_mismatch",
                recovery="Do not mix state directories. Inspect with `bridge_admin.py status`; nothing "
                         "was modified.",
            )
        data.setdefault("retired", [])
        return data

    def _write(self, data: dict[str, Any]) -> None:
        try:
            inst = self._ensure_instance()
        except OSError as exc:
            raise errors.BridgeError(
                errors.STATE_ERROR, f"Cannot initialise {self.instance_path.name}: {exc.__class__.__name__}",
                code="registry_unwritable",
                recovery="Check disk space/permissions; no registry change was made.",
            ) from exc
        try:
            if not inst.get("registry_established"):
                inst["registry_established"] = True  # pre-commit: a crash errs towards "lost", never "empty"
                atomic_write_text(self.instance_path, json.dumps(inst, indent=2, sort_keys=True) + "\n")
            data["instance_id"] = inst["instance_id"]
            if self.path.exists():
                try:
                    atomic_write_text(self.prev_path, self.path.read_text(encoding="utf-8"))
                except OSError:
                    pass  # the previous copy is a convenience, never a reason to fail a write
            atomic_write_text(self.path, json.dumps(data, indent=2, sort_keys=True) + "\n")
        except OSError as exc:
            raise errors.BridgeError(
                errors.STATE_ERROR, f"Cannot write {self.path}: {exc}", code="registry_unwritable",
                recovery="Check disk space/permissions; the previous registry content is intact.",
            ) from exc

    @staticmethod
    def _validate_entry(key: str, entry: Any, ident: Mapping[str, str]) -> dict[str, Any]:
        def mismatch(why: str) -> errors.BridgeError:
            return errors.BridgeError(
                errors.SESSION_MISMATCH,
                f"Registry entry for this repository/model/session does not match: {why}.",
                code="entry_mismatch",
                recovery=(
                    "Run `bridge_admin.py status` to inspect it. Use `bridge_admin.py reset --yes` "
                    "to retire the pointer explicitly (Codex history is kept)."
                ),
            )

        if not isinstance(entry, dict):
            raise mismatch("entry is not an object")
        for field_name in ("repo_key", "model", "session"):
            if entry.get(field_name) != ident[field_name]:
                raise mismatch(f"{field_name} differs")
        thread_id = entry.get("thread_id")
        if thread_id is not None and not (isinstance(thread_id, str) and _THREAD_RE.fullmatch(thread_id)):
            raise mismatch("thread_id has an unexpected format")
        return entry

    # -- operations (each takes the registry lock briefly) ----------------
    def get(self, key: str, ident: Mapping[str, str]) -> dict[str, Any] | None:
        with self._registry_lock():
            data = self._read()
            entry = data["sessions"].get(key)
            return None if entry is None else dict(self._validate_entry(key, entry, ident))

    def bind_thread(
        self, key: str, ident: Mapping[str, str], thread_id: str, cli_version: str | None
    ) -> dict[str, Any]:
        if not _THREAD_RE.fullmatch(thread_id):
            raise errors.BridgeError(
                errors.OUTPUT_INCOMPLETE, "Codex reported a thread ID in an unexpected format.",
                code="bad_thread_id",
            )
        with self._registry_lock():
            data = self._read()  # raises registry_lost: an in-flight call cannot repopulate a lost/quarantined registry
            existing = data["sessions"].get(key)
            if existing is not None:
                existing = self._validate_entry(key, existing, ident)
                if existing.get("thread_id") not in (None, thread_id):
                    raise errors.BridgeError(
                        errors.SESSION_MISMATCH,
                        "Registry already points at a different thread; refusing to overwrite.",
                        code="thread_overwrite",
                    )
            now = utc_now()
            entry = {
                "repo_key": ident["repo_key"],
                "repo_path": ident["repo_path"],
                "model": ident["model"],
                "session": ident["session"],
                "thread_id": thread_id,
                "created_at": (existing or {}).get("created_at", now),
                "updated_at": now,
                "turns": int((existing or {}).get("turns", 0)) + 1,
                "cli_version": cli_version,
            }
            data["sessions"][key] = entry
            self._write(data)
            return entry

    def adopt_thread(self, key: str, ident: Mapping[str, str], thread_id: str) -> dict[str, Any]:
        """Explicit operator action: point a (possibly empty/retired) session at a known thread."""
        if not _THREAD_RE.fullmatch(thread_id):
            raise errors.BridgeError(errors.INVALID_CONFIGURATION, "thread id has an unexpected format",
                                     code="bad_thread_id")
        with self._registry_lock():
            data = self._read()
            existing = data["sessions"].get(key)
            if existing is not None:
                existing = self._validate_entry(key, existing, ident)
                if existing.get("thread_id") not in (None, thread_id):
                    raise errors.BridgeError(
                        errors.SESSION_MISMATCH,
                        "This session already points at a different thread; `reset` it first.",
                        code="thread_overwrite",
                    )
            now = utc_now()
            entry = {
                "repo_key": ident["repo_key"], "repo_path": ident["repo_path"], "model": ident["model"],
                "session": ident["session"], "thread_id": thread_id,
                "created_at": (existing or {}).get("created_at", now), "updated_at": now,
                "turns": int((existing or {}).get("turns", 0)), "cli_version": (existing or {}).get("cli_version"),
                "adopted": True,
            }
            data["sessions"][key] = entry
            self._write(data)
            return entry

    def register_new(self, key: str, ident: Mapping[str, str]) -> bool:
        """Create an empty named pointer.  Returns False if one already exists."""
        with self._registry_lock():
            data = self._read()
            if key in data["sessions"]:
                self._validate_entry(key, data["sessions"][key], ident)
                return False
            now = utc_now()
            data["sessions"][key] = {
                "repo_key": ident["repo_key"],
                "repo_path": ident["repo_path"],
                "model": ident["model"],
                "session": ident["session"],
                "thread_id": None,
                "created_at": now,
                "updated_at": now,
                "turns": 0,
                "cli_version": None,
            }
            self._write(data)
            return True

    def retire(self, key: str, reason: str) -> dict[str, Any] | None:
        with self._registry_lock():
            data = self._read()
            entry = data["sessions"].pop(key, None)
            if entry is None:
                return None
            entry = dict(entry) if isinstance(entry, dict) else {"raw": repr(entry)[:200]}
            entry["retired_at"] = utc_now()
            entry["retired_reason"] = reason
            data["retired"].append(entry)
            self._write(data)
            return entry

    def snapshot(self) -> dict[str, Any]:
        with self._registry_lock():
            return self._read()

    # -- administrative recovery (callers must hold the maintenance flag) -----
    def quarantine(self) -> Path | None:
        with self._registry_lock():
            if not self.path.exists():
                return None
            target = self.path.with_name(f"registry.corrupt-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json")
            os.replace(self.path, target)
            return target

    def restore_prev(self) -> bool:
        with self._registry_lock():
            if not self.prev_path.exists():
                return False
            try:
                prev = json.loads(self.prev_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return False
            status, inst = self.instance_state()
            if not (isinstance(prev, dict) and prev.get("schema_version") == SCHEMA_VERSION
                    and isinstance(prev.get("sessions"), dict) and isinstance(prev.get("retired", []), list)
                    and status == "ok" and inst is not None and prev.get("instance_id") == inst["instance_id"]):
                return False  # unsupported/malformed schema or a different instance: never restore
            atomic_write_text(self.path, self.prev_path.read_text(encoding="utf-8"))
            return True

    def reinit(self) -> None:
        """Explicit empty registry that keeps the instance identity.  Existing threads stay
        in Codex history; they must be re-attached with ``adopt``."""
        with self._registry_lock():
            if self.path.exists():
                raise errors.BridgeError(errors.STATE_ERROR, "A registry file exists; quarantine it first.",
                                         code="registry_exists")
            inst = self._ensure_instance()
            data = _empty_registry()
            data["instance_id"] = inst["instance_id"]
            data["reinitialised_at"] = utc_now()
            inst["registry_established"] = True
            atomic_write_text(self.instance_path, json.dumps(inst, indent=2, sort_keys=True) + "\n")
            atomic_write_text(self.path, json.dumps(data, indent=2, sort_keys=True) + "\n")

    # -- in-flight / pending markers (F3, F5) ---------------------------------
    def _marker(self, kind: str, key: str) -> Path:
        return self.state_dir / kind / f"{key_hash(key)}.json"

    def write_marker(self, kind: str, key: str, payload: Mapping[str, Any]) -> bool:
        try:
            self._marker(kind, key).parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(self._marker(kind, key), json.dumps(dict(payload), indent=2, sort_keys=True) + "\n")
            return True
        except OSError:
            return False

    def read_marker(self, kind: str, key: str) -> dict[str, Any] | None:
        try:
            data = json.loads(self._marker(kind, key).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"state": "unreadable"}
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            return {"state": "unreadable"}

    def update_marker(self, kind: str, key: str, **fields: Any) -> bool:
        current = self.read_marker(kind, key) or {}
        current.update(fields)
        return self.write_marker(kind, key, current)

    def clear_marker(self, kind: str, key: str) -> None:
        try:
            self._marker(kind, key).unlink()
        except FileNotFoundError:
            pass

    def list_markers(self, kind: str) -> list[dict[str, Any]]:
        out = []
        d = self.state_dir / kind
        if d.is_dir():
            for p in sorted(d.glob("*.json")):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        out.append({"file": p.stem, **data})
                except (OSError, json.JSONDecodeError):
                    out.append({"file": p.stem, "state": "unreadable"})
        return out
