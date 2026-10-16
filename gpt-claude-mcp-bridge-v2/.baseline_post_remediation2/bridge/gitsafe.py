"""The only place the bridge builds *host-side* git command lines (N1).

Host-side git runs with the bridge's permissions, **outside the Codex sandbox**, against a
repository the bridge does not control.  Git can execute configured helpers while doing
`status`/`diff`: clean/smudge **filters**, **textconv** and **external diff** drivers
(selected by `.gitattributes`), and `core.fsmonitor` hooks.  This module removes those
routes as far as git allows:

* ``--attr-source=<empty tree>``: attributes are read from the empty tree instead of the
  worktree, so no `.gitattributes` can select a filter/textconv/diff driver;
* ``core.attributesFile=<null>`` and ``GIT_ATTR_NOSYSTEM``: no user/system attribute files;
* ``core.fsmonitor=false``: no filesystem-monitor hook;
* ``diff.external=`` + ``--no-ext-diff --no-textconv`` (callers add the diff flags);
* ``--no-optional-locks`` / ``GIT_OPTIONAL_LOCKS=0``: git does not refresh/write the index
  (this alone is *not* a containment claim: it prevents index writes, nothing more);
* a scrubbed environment (no ``GIT_EXTERNAL_DIFF``, pager, askpass, prompts).

Residual: git itself still parses the repository (object database, config).  If a
repository can make git execute code despite the above (a git bug), that is outside what
this module can prevent.  ``supported()`` lets callers fail closed ("unavailable") on a git
too old to honour ``--attr-source``.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import threading
from typing import Mapping

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

_supported: bool | None = None
_lock = threading.Lock()


def safe_git_argv(root: Path | str, *args: str) -> list[str]:
    return [
        "git",
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
           if k not in ("GIT_EXTERNAL_DIFF", "GIT_DIFF_OPTS", "GIT_PAGER", "GIT_ASKPASS", "GIT_SSH_COMMAND")}
    env.update({
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat",
    })
    return env


def supported() -> bool:
    """True when the installed git honours ``--attr-source`` (git >= 2.40)."""
    global _supported
    with _lock:
        if _supported is None:
            try:
                res = subprocess.run(["git", f"--attr-source={EMPTY_TREE}", "--version"],
                                     stdin=subprocess.DEVNULL, capture_output=True, timeout=15, check=False)
                _supported = res.returncode == 0
            except Exception:  # noqa: BLE001
                _supported = False
        return _supported
