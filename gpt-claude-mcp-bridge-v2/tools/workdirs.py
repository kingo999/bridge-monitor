"""Disposable workspace directories for the live harnesses.

``tempfile.mkdtemp`` creates a directory whose DACL is PROTECTED and limited to SYSTEM, Administrators and
the OWNER, with nothing inheritable for anyone else.  The elevated Windows sandbox backend's setup refresh
grants its sandbox users read access to the workspace, so such a directory happens to work there; the
unelevated (restricted-token) backend runs no such refresh, and its process then cannot even open its working
directory (observed: ``fatal: Unable to read current working directory``).  A harness that used mkdtemp for
the workspace therefore measured its own directory ACL, not the backend.

``fresh_dir`` makes a plain, uniquely named directory that INHERITS its parent's ACL, like any ordinary
project directory.  Use it for a workspace the sandbox must read; keep ``mkdtemp`` for private state.
"""

from __future__ import annotations

import os
from pathlib import Path
import secrets


def fresh_dir(parent: Path, prefix: str) -> Path:
    """A new, empty, uniquely named directory under ``parent`` that inherits ``parent``'s ACL."""
    parent.mkdir(parents=True, exist_ok=True)
    for _ in range(100):
        candidate = parent / f"{prefix}{os.getpid():x}_{secrets.token_hex(4)}"
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise OSError(f"could not create a unique workspace directory under {parent}")
