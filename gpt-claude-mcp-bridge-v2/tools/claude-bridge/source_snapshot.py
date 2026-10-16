"""Safe repository-relative paths and exact, content-free source snapshots.

A snapshot records identities (SHA-256 of bytes, never the bytes) for the tracked and
non-ignored working-tree files plus explicitly declared ``source_paths`` (needed because
this bridge directory is itself Git-ignored). It is a point-in-time observation of what was
read, not continuous immutability and not OS isolation.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path

SCHEMA = 1
MAX_FILES = 20000
MAX_TOTAL_BYTES = 1 << 30
MAX_DECLARED_PATHS = 100
MAX_PATH_CHARS = 400
REASONS = frozenset({'git_unavailable', 'git_failed', 'too_many_files', 'too_large', 'unreadable',
                     'unsafe_path', 'other_repository', 'not_requested', 'unexpected'})
COVERAGE = ('tracked and non-ignored working-tree files plus declared source_paths; other ignored '
            'files are not covered; a point-in-time observation, not continuous immutability')

_ROOTED = re.compile(r'^([A-Za-z]:|[\\/])')
_UNSAFE_COMPONENT = re.compile(r'[:<>"|?*\x00-\x1f]')
_RESERVED_NAME = re.compile(r'^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(\..*)?$', re.I)


class Unsafe(ValueError):
    """A path is not a safe relative path inside its base."""


class SnapshotError(Exception):
    """Source evidence is incomplete, unreadable or unsafe; `code` is a sanitized reason."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def is_reserved_name(name: str) -> bool:
    return bool(_RESERVED_NAME.match(name))


def safe_relative(rel: str) -> str:
    """Return the normalized portable POSIX form of `rel` or raise Unsafe.

    Vetting is lexical and runs before any filesystem call: rooted/UNC/drive syntax, parent
    escapes, embedded drives and stream syntax, Windows device names and trailing dots or
    spaces are rejected. Backslashes are separators. Git metadata is never a valid target.
    """
    if not isinstance(rel, str) or not rel or len(rel) > MAX_PATH_CHARS:
        raise Unsafe('invalid path')
    if '\0' in rel or _ROOTED.match(rel) or os.path.isabs(rel):
        raise Unsafe('absolute or drive-relative path')
    parts = []
    for part in rel.replace('\\', '/').split('/'):
        if part in ('', '.'):
            continue
        if part == '..':
            raise Unsafe('escapes the base')
        if _UNSAFE_COMPONENT.search(part) or _RESERVED_NAME.match(part) or part.endswith(('.', ' ')):
            raise Unsafe('unsafe path component')
        if part.lower() == '.git':
            raise Unsafe('Git metadata')
        parts.append(part)
    if not parts:
        raise Unsafe('empty path')
    return '/'.join(parts)


_REPARSE_LINK_TAGS = frozenset({0xA000000C, 0xA0000003})  # Windows symlink, mount point (junction)


def is_link(path: str | os.PathLike) -> bool:
    """True for symlinks and junctions (also on Pythons without `os.path.isjunction`).

    A path that does not exist is not a link; one that cannot be examined is treated as unsafe.
    """
    try:
        info = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except (OSError, ValueError):
        return True
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_reparse_tag', 0) in _REPARSE_LINK_TAGS:
        return True
    try:
        isjunction = getattr(os.path, 'isjunction', None)
        return bool(isjunction and isjunction(path))
    except (OSError, ValueError):
        return True


def pin(base: str | os.PathLike) -> str:
    """Vet `base` and every absolute ancestor, then return its resolved identity.

    The base and each ancestor component up to the drive/share/root anchor are examined with
    lstat *before* any resolution; a symlink or junction anywhere in the chain is refused.
    The returned resolved path lets `contained(..., pinned=...)` detect a later swap.
    """
    text = str(base)
    if '\0' in text:
        raise Unsafe('invalid base')
    chain = Path(os.path.abspath(text))
    # Root to leaf: a linked ancestor is rejected before any of its descendants is examined.
    for candidate in (*reversed(chain.parents), chain):
        if candidate.parent == candidate:  # the anchor (drive, share or root)
            continue
        if is_link(str(candidate)):
            raise Unsafe('linked base or ancestor')
    try:
        return os.path.normcase(os.path.realpath(text))
    except (OSError, ValueError):
        raise Unsafe('unreadable base') from None


def contained(base: str | os.PathLike, rel: str, *, pinned: str | None = None) -> Path:
    """Return `rel` below `base`, refusing linked components and realpath escapes.

    Every component below `base` is examined with lstat before it is returned, so callers
    holding a result can re-run this at each read/write boundary. `pinned` (from `pin`)
    additionally fails closed if the resolved base is no longer the one first configured.
    This is a point-in-time check, not protection against a concurrent swap between the
    check and the use.
    """
    clean = safe_relative(rel)
    base_real = pin(base)
    if pinned is not None and base_real != pinned:
        raise Unsafe('the base changed')
    current = str(base)
    for part in clean.split('/'):
        current = os.path.join(current, part)
        if is_link(current):
            raise Unsafe('linked path component')
    try:
        resolved = os.path.normcase(os.path.realpath(current))
        inside = os.path.commonpath([base_real, resolved]) == base_real
    except (OSError, ValueError):
        inside = False
    if not inside:
        raise Unsafe('resolves outside the base')
    return Path(current)


# ---------------------------------------------------------------------- snapshot


class _Budget:
    def __init__(self) -> None:
        self.files = 0
        self.total = 0
        self.dirs: dict[str, bool] = {}  # vetted ancestor path -> exists as a real directory

    def file(self) -> None:
        self.files += 1
        if self.files > MAX_FILES:
            raise SnapshotError('too_many_files')

    def add(self, size: int) -> None:
        self.total += size
        if self.total > MAX_TOTAL_BYTES:
            raise SnapshotError('too_large')


def _git(root: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                                stdin=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.SubprocessError):
        raise SnapshotError('git_unavailable') from None
    if result.returncode:
        raise SnapshotError('git_failed')
    return result.stdout


def _ancestors_exist(root: str, rel: str, budget: _Budget) -> bool:
    """Vet every ancestor of `rel` below `root` before the entry itself is touched.

    A linked ancestor (symlink or junction) fails closed: the entry would otherwise be read
    through it, possibly outside the repository. Returns False when an ancestor is missing or
    is not a directory (the entry cannot exist). Results are cached per capture.
    """
    current = root
    for part in rel.split('/')[:-1]:
        current = os.path.join(current, part)
        exists = budget.dirs.get(current)
        if exists is None:
            if is_link(current):
                raise SnapshotError('unsafe_path')
            try:
                exists = stat.S_ISDIR(os.lstat(current).st_mode)
            except (FileNotFoundError, NotADirectoryError):
                exists = False
            except (OSError, ValueError):
                raise SnapshotError('unreadable') from None
            budget.dirs[current] = exists
        if not exists:
            return False
    return True


def _identity(root: str, rel: str, budget: _Budget) -> list:
    """[path, kind, size, sha256] for one entry without following links.

    Ancestors are vetted first. A link at the entry itself (Git tracks symlinks) is recorded
    as the digest of its target text only; the target is never opened or traversed.
    """
    path = os.path.join(root, *rel.split('/'))
    if not _ancestors_exist(root, rel, budget):
        return [rel, 'absent', 0, '']
    try:
        info = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return [rel, 'absent', 0, '']
    except (OSError, ValueError):
        raise SnapshotError('unreadable') from None
    if stat.S_ISLNK(info.st_mode) or is_link(path):
        try:
            target = os.readlink(path)
        except OSError:
            raise SnapshotError('unreadable') from None
        return [rel, 'link', 0, hashlib.sha256(target.encode('utf-8', 'surrogatepass')).hexdigest()]
    if stat.S_ISDIR(info.st_mode):
        return [rel, 'dir', 0, '']
    if not stat.S_ISREG(info.st_mode):
        raise SnapshotError('unsafe_path')
    budget.file()
    digest = hashlib.sha256()
    size = 0
    try:
        with open(path, 'rb') as handle:
            while True:
                chunk = handle.read(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                budget.add(len(chunk))
                digest.update(chunk)
    except OSError:
        raise SnapshotError('unreadable') from None
    return [rel, 'file', size, digest.hexdigest()]


def _walk(root: str, rel: str, out: list, budget: _Budget, excluded) -> None:
    path = os.path.join(root, *rel.split('/'))
    try:
        entries = sorted(os.scandir(path), key=lambda entry: entry.name)
    except OSError:
        raise SnapshotError('unreadable') from None
    for entry in entries:
        if entry.name.lower() == '.git':
            continue
        child = f'{rel}/{entry.name}'
        try:
            safe_relative(child)
        except Unsafe:
            raise SnapshotError('unsafe_path') from None
        if excluded(child):
            continue
        if is_link(entry.path):
            raise SnapshotError('unsafe_path')
        if entry.is_dir(follow_symlinks=False):
            _walk(root, child, out, budget, excluded)
        else:
            out.append(_identity(root, child, budget))


def capture(root: str | os.PathLike, declared=(), exclude=()) -> dict:
    """Return the deterministic snapshot summary for `root`, or raise SnapshotError.

    `declared` are repository-relative files or directories read even when ignored.
    `exclude` are directories (typically the receipt store) whose contents never count, so
    receipts and their temporary writes cannot invalidate their own snapshot.
    """
    try:
        pin(root)  # the repository root and its ancestors must not be links
    except Unsafe:
        raise SnapshotError('unsafe_path') from None
    root_path = Path(root).resolve()
    root_text = str(root_path)
    if len(declared) > MAX_DECLARED_PATHS:
        raise SnapshotError('too_many_files')
    declared_paths = []
    for value in declared:
        try:
            declared_paths.append(safe_relative(value))
        except Unsafe:
            raise SnapshotError('unsafe_path') from None
    declared_paths = sorted(set(declared_paths))

    def fold(value: str) -> str:
        # one comparison form on every platform: case-folded as the OS does, '/' separators
        return os.path.normcase(value).replace('\\', '/').rstrip('/')

    prefixes = []
    for directory in exclude:
        try:
            relative = os.path.relpath(os.path.realpath(str(directory)), os.path.realpath(root_text))
        except ValueError:  # another drive: cannot be inside the root
            continue
        parts = relative.replace('\\', '/').split('/')
        if relative != '.' and parts[0] != '..' and not os.path.isabs(relative):
            prefixes.append(fold(relative))

    def excluded(rel: str) -> bool:
        normal = fold(rel)
        return any(normal == prefix or normal.startswith(prefix + '/') for prefix in prefixes)

    branch = _git(root_path, 'rev-parse', '--abbrev-ref', 'HEAD').decode('utf-8', 'replace').strip()
    head = _git(root_path, 'rev-parse', 'HEAD').decode('utf-8', 'replace').strip()
    listed = _git(root_path, 'ls-files', '-z', '--cached', '--others', '--exclude-standard')
    names = sorted({name.decode('utf-8', 'surrogateescape') for name in listed.split(b'\0') if name})

    budget = _Budget()
    cache: dict[str, list] = {}
    tracked = []
    for name in names:
        try:
            rel = safe_relative(name)
        except Unsafe:
            raise SnapshotError('unsafe_path') from None
        if excluded(rel):
            continue
        entry = cache.get(rel) or _identity(root_text, rel, budget)
        cache[rel] = entry
        tracked.append(entry)

    declared_entries = []
    absent = []
    for rel in declared_paths:
        try:
            contained(root_path, rel)
        except Unsafe:
            raise SnapshotError('unsafe_path') from None
        if excluded(rel):
            raise SnapshotError('unsafe_path')
        full = os.path.join(root_text, *rel.split('/'))
        if os.path.isdir(full):
            _walk(root_text, rel, declared_entries, budget, excluded)
            continue
        entry = cache.get(rel) or _identity(root_text, rel, budget)
        if entry[1] == 'absent':
            absent.append(rel)
        declared_entries.append(entry)
    declared_entries.sort(key=lambda entry: entry[0])

    payload = {'schema': SCHEMA, 'root': root_text, 'branch': branch, 'head': head,
               'tracked': tracked, 'declared_paths': declared_paths, 'declared': declared_entries}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                       ensure_ascii=True).encode('ascii')).hexdigest()
    return {'state': 'captured', 'root': root_text, 'branch': branch, 'head': head, 'digest': digest,
            'file_count': len(tracked), 'declared_paths': declared_paths,
            'declared_file_count': len(declared_entries), 'absent_declared': absent}


def capture_or_unavailable(root, declared=(), exclude=()) -> dict:
    """Like capture() but returns {'state': 'unavailable', 'reason': code} on failure."""
    try:
        return capture(root, declared, exclude)
    except SnapshotError as exc:
        return {'state': 'unavailable', 'reason': exc.code if exc.code in REASONS else 'unexpected'}
    except Exception:  # evidence capture must never turn a finished job into a bridge crash
        return {'state': 'unavailable', 'reason': 'unexpected'}
