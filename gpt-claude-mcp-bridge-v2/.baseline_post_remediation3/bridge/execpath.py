"""Resolve host-side executables without trusting the current directory or the reviewed tree (V6).

The bridge runs as a child of Claude Code with the *reviewed project* as its working directory.
Windows ``CreateProcess`` and ``shutil.which`` both look in the current directory first, so a
bare ``git`` / ``codex`` / ``taskkill`` could resolve to a file the repository ships and then run
with the user's full permissions, outside the Codex sandbox, before any containment flag matters.

Rules (the only place these decisions are made):

* only **absolute** ``PATH`` entries are considered; ``""``, ``"."`` and relative entries are skipped;
* an entry that is, or is inside, the current directory, ``CLAUDE_PROJECT_DIR`` or an explicitly
  excluded root (the reviewed repository) is skipped - a tool that lives in the reviewed tree is
  never trusted;
* on Windows only ``.exe .cmd .bat .com`` are candidates (not ``.vbs``/``.js``/...);
* Windows system tools (``taskkill``, ``tasklist``) are addressed by absolute
  ``%SystemRoot%\\System32`` path, not looked up.

An explicit override such as ``GPT_ARCHITECT_CODEX_PATH`` is the user's own statement of trust
and is not subject to this search.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Iterable, Mapping, Sequence

_WINDOWS_EXTS = (".exe", ".cmd", ".bat", ".com")


def _norm(path: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.realpath(os.fspath(path)))


def _inside(candidate: str, root: str) -> bool:
    if candidate == root:
        return True
    try:
        return os.path.commonpath([candidate, root]) == root
    except ValueError:               # different drives
        return False


def _path_value(env: Mapping[str, str]) -> str:
    for key, value in env.items():
        if key.upper() == "PATH":
            return value or ""
    return ""


def excluded_roots(env: Mapping[str, str], extra: Iterable[str | os.PathLike[str]] = ()) -> list[str]:
    """Normalised directories that executables must not be taken from."""
    roots: list[str] = []
    for item in (os.getcwd(), env.get("CLAUDE_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR"), *extra):
        if item:
            try:
                roots.append(_norm(item))
            except OSError:
                continue
    return roots


def trusted_which(
    names: Sequence[str],
    env: Mapping[str, str],
    *,
    exclude: Iterable[str | os.PathLike[str]] = (),
) -> str | None:
    """Absolute path of the first match among ``names`` found in a trusted PATH directory."""
    roots = excluded_roots(env, exclude)
    seen: set[str] = set()
    for entry in _path_value(env).split(os.pathsep):
        entry = entry.strip().strip('"')
        if not entry or entry == "." or not os.path.isabs(entry):
            continue
        try:
            directory = _norm(entry)
        except OSError:
            continue
        if directory in seen or any(_inside(directory, root) for root in roots):
            continue
        seen.add(directory)
        for name in names:
            if sys.platform == "win32":
                has_ext = Path(name).suffix.lower() in _WINDOWS_EXTS
                candidates = [name] if has_ext else [name + ext for ext in _WINDOWS_EXTS]
            else:
                candidates = [name]
            for candidate in candidates:
                full = os.path.join(entry, candidate)
                if os.path.isfile(full) and (sys.platform == "win32" or os.access(full, os.X_OK)):
                    return full
    return None


def system_tool(name: str) -> str:
    """Absolute path of a Windows system tool (``taskkill`` -> ``C:\\Windows\\System32\\taskkill.exe``).

    On other platforms the plain name is returned (POSIX lookups never include the current
    directory unless PATH says so, and the bridge's POSIX paths do not call these tools)."""
    if sys.platform != "win32":
        return name
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    return os.path.join(root, "System32", name if name.lower().endswith(".exe") else name + ".exe")


def excluded_matches(
    names: Sequence[str],
    env: Mapping[str, str],
    *,
    exclude: Iterable[str | os.PathLike[str]] = (),
) -> list[str]:
    """Matches that exist on PATH but lie inside an excluded root.  Diagnostics only: lets an error
    say *why* a tool was not accepted (it is never returned as a result)."""
    roots = excluded_roots(env, exclude)
    found: list[str] = []
    for entry in _path_value(env).split(os.pathsep):
        entry = entry.strip().strip('"')
        if not entry or entry == "." or not os.path.isabs(entry):
            continue
        try:
            directory = _norm(entry)
        except OSError:
            continue
        if not any(_inside(directory, root) for root in roots):
            continue
        for name in names:
            if sys.platform == "win32":
                has_ext = Path(name).suffix.lower() in _WINDOWS_EXTS
                candidates = [name] if has_ext else [name + ext for ext in _WINDOWS_EXTS]
            else:
                candidates = [name]
            found.extend(os.path.join(entry, c) for c in candidates if os.path.isfile(os.path.join(entry, c)))
    return found
