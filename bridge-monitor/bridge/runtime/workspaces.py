"""Source checkout arbitration, independent of model capacity."""

from typing import Literal
from hashlib import sha256
from pathlib import Path
import shutil

import psycopg

from bridge.runtime.admission import ARBITRATION_LOCK


def _source_files(source_root: Path, max_bytes: int) -> list[Path]:
    if max_bytes <= 0:
        raise ValueError("positive workspace byte limit required")
    if source_root.is_symlink() or getattr(source_root, "is_junction", lambda: False)():
        raise ValueError("source boundary crossed")
    root = source_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("source root must be a directory")
    files = []
    total = 0
    for entry in root.rglob("*"):
        relative = entry.relative_to(root)
        if ".git" in relative.parts:
            continue
        if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
            raise ValueError("source boundary crossed")
        if entry.is_file():
            total += entry.stat().st_size
            if total > max_bytes:
                raise ValueError("source exceeds workspace byte limit")
            files.append(relative)
    return sorted(files, key=lambda item: item.as_posix())


def fingerprint_source(source_root: str | Path, *, max_bytes: int) -> str:
    """Hash the exact regular-file tree admitted for a source-aware call."""
    root = Path(source_root).resolve(strict=True)
    digest = sha256()
    for relative in _source_files(Path(source_root), max_bytes):
        content = (root / relative).read_bytes()
        name = relative.as_posix().encode("utf-8")
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return "tree-sha256:" + digest.hexdigest()


def materialize_source(source_root: str | Path, workspace_base: str | Path,
                       call_id: str, snapshot_ref: str, *, max_bytes: int) -> Path:
    """Create a per-call copy and reject any external change during copying."""
    source = Path(source_root).resolve(strict=True)
    if fingerprint_source(source_root, max_bytes=max_bytes) != snapshot_ref:
        raise ValueError("source snapshot changed before launch")
    base = Path(workspace_base).resolve()
    target = isolated_workspace_target(base, call_id, "source")
    owner = target.parent
    if owner.exists():
        raise ValueError("workspace already exists for this call")
    try:
        target.mkdir(parents=True)
        for relative in _source_files(Path(source_root), max_bytes):
            destination = isolated_workspace_target(base, call_id, Path("source") / relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, destination)
        if (fingerprint_source(source_root, max_bytes=max_bytes) != snapshot_ref or
                fingerprint_source(target, max_bytes=max_bytes) != snapshot_ref):
            raise ValueError("source snapshot changed during copy")
        return target
    except Exception:
        if owner.resolve().is_relative_to(base) and owner.exists():
            shutil.rmtree(owner)
        raise


def isolated_workspace_target(workspace_base, call_id, relative_path):
    """Resolve one target inside the caller's separate checkout directory.

    This is the path boundary used when creating/using isolated checkouts; it
    does not itself create a checkout or grant source permissions.
    """
    if not call_id or not str(relative_path):
        raise ValueError("workspace boundary requires call and relative path")
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("workspace boundary crossed")
    base = Path(workspace_base).resolve()
    owner = (base / sha256(call_id.encode("utf-8")).hexdigest()).resolve()
    target = (owner / relative).resolve()
    if not owner.is_relative_to(base) or not target.is_relative_to(owner):
        raise ValueError("workspace boundary crossed")
    return target


def acquire_source(dsn: str, call_id: str, source_id: str,
                   mode: Literal["read", "write"], snapshot_ref: str) -> bool:
    if not source_id or not snapshot_ref:
        raise ValueError("source identity and fingerprint are required")
    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (ARBITRATION_LOCK,))
        job = conn.execute("SELECT source_snapshot,source_mode,state FROM runtime_jobs WHERE call_id=%s",
                           (call_id,)).fetchone()
        if not job or job[2] in ("cancelled", "terminated", "completed", "fallback"):
            raise ValueError("source lock requires a live admitted job")
        if job[0] != snapshot_ref or (job[1] and job[1] != mode):
            raise ValueError("source access conflicts with immutable admission pin")
        prior = conn.execute("SELECT mode,snapshot_ref FROM runtime_source_locks "
                             "WHERE source_id=%s AND holder_call_id=%s", (source_id, call_id)).fetchone()
        if prior:
            return prior == (mode, snapshot_ref)
        others = conn.execute("SELECT mode,snapshot_ref FROM runtime_source_locks "
                              "WHERE source_id=%s", (source_id,)).fetchall()
        if others and (mode == "write" or any(other[0] == "write" or other[1] != snapshot_ref for other in others)):
            return False
        conn.execute("INSERT INTO runtime_source_locks(source_id,holder_call_id,mode,snapshot_ref) "
                     "VALUES(%s,%s,%s,%s)", (source_id, call_id, mode, snapshot_ref))
        return True


def release_source(dsn: str, call_id: str, source_id: str) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute("DELETE FROM runtime_source_locks WHERE source_id=%s AND holder_call_id=%s",
                     (source_id, call_id))
