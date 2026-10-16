"""The only place the bridge builds *host-side* git command lines (N1, V6).

Host-side git runs with the bridge's permissions, **outside the Codex sandbox**, against a
repository the bridge does not control.  Two separate boundaries are handled here:

**Which program runs (V6).**  ``trusted_git`` resolves an *absolute* git executable from trusted
PATH directories only (``execpath``): never the current directory, never a directory inside the
reviewed repository, never a relative PATH entry.  Every host-side git call uses that path, so a
``git.exe`` shipped by the repository cannot be selected and then run before any flag matters.

**What git does once it runs (N1).**  Git can execute configured helpers while doing
`status`/`diff`: clean/smudge **filters**, **textconv** and **external diff** drivers (selected by
`.gitattributes`), and `core.fsmonitor` hooks.  This module removes those routes as far as git
allows:

* ``--attr-source=<empty tree>``: attributes are read from the empty tree instead of the
  worktree, so no `.gitattributes` can select a filter/textconv/diff driver;
* ``core.attributesFile=<devnull>`` and ``GIT_ATTR_NOSYSTEM``: no user/system attribute files;
* ``core.fsmonitor=false``: no filesystem-monitor hook;
* ``diff.external=`` + ``--no-ext-diff --no-textconv`` (callers add the diff flags);
* ``--no-optional-locks`` / ``GIT_OPTIONAL_LOCKS=0``: git does not refresh/write the index
  (this alone is *not* a containment claim: it prevents index writes, nothing more);
* a scrubbed environment: every inherited ``GIT_*`` variable is dropped (matched
  case-insensitively, as Windows environment names are), ``NoDefaultCurrentDirectoryInExePath``
  is set, and only the controls above are added.

Residual: git itself still parses the repository (object database, config); attributes supplied
from inside ``$GIT_DIR`` (``info/attributes``) are still read; a git bug that executes code despite
the above is outside what this module can prevent.  ``supported()`` lets callers fail closed
("unavailable") on a git too old to honour ``--attr-source`` or when no trusted git exists.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import threading
from typing import Mapping

from . import execpath

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

_supported: dict[str, bool] = {}
_lock = threading.Lock()


def trusted_git(root: Path | str | None = None) -> str | None:
    """Absolute path of a git executable that is not inside the reviewed tree or the current
    directory, or None when none is available on PATH."""
    return execpath.trusted_which(("git",), os.environ, exclude=[root] if root else [])


def safe_git_argv(root: Path | str, *args: str) -> list[str]:
    exe = trusted_git(root)
    if exe is None:
        raise FileNotFoundError("no trusted git executable was found on PATH (the current directory and "
                                "the reviewed repository are never searched)")
    return [
        exe,
        "--no-optional-locks",
        f"--attr-source={EMPTY_TREE}",
        "-c", "core.fsmonitor=false",
        "-c", f"core.attributesFile={os.devnull}",
        "-c", "diff.external=",
        "-c", "core.pager=cat",
        "-C", str(root),
        *args,
    ]


def safe_git_env(base: Mapping[str, str]) -> dict[str, str]:
    env = {k: v for k, v in base.items()
           if not k.upper().startswith("GIT_") and k.upper() != "NODEFAULTCURRENTDIRECTORYINEXEPATH"}
    env.update({
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat",
        "NoDefaultCurrentDirectoryInExePath": "1",
    })
    return env


def supported(root: Path | str | None = None) -> bool:
    """True when a trusted git exists and honours ``--attr-source`` (git >= 2.40)."""
    exe = trusted_git(root)
    if exe is None:
        return False
    with _lock:
        if exe not in _supported:
            try:
                res = subprocess.run([exe, f"--attr-source={EMPTY_TREE}", "--version"],
                                     stdin=subprocess.DEVNULL, capture_output=True, timeout=15, check=False,
                                     env=safe_git_env(os.environ))
                _supported[exe] = res.returncode == 0
            except Exception:  # noqa: BLE001
                _supported[exe] = False
        return _supported[exe]
