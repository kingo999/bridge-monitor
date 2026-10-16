"""Remove this machine's identifiers from text that is retained as evidence or embedded in a prompt.

The prompt for GPT is sent to a third party; the account name and profile path of the machine that
produced the evidence are not needed to review the code.  Used by ``record_evidence.py`` (CLI
observations) and ``make_verification_prompt.py`` (everything embedded).
"""

from __future__ import annotations

from pathlib import Path
import re

HOME_TOKEN = "<home>"
USER_TOKEN = "<user>"


def identifiers(home: str | None = None, user: str | None = None) -> tuple[str, str]:
    h = home if home is not None else str(Path.home())
    u = user if user is not None else Path.home().name
    return h, u


def scrub_local(text: str, home: str | None = None, user: str | None = None) -> str:
    """Replace the profile directory (any slash style / case) and the account name."""
    h, u = identifiers(home, user)
    out = text
    for variant in {h, h.replace("\\", "/"), h.replace("\\", "\\\\")}:
        if variant:
            out = re.sub(re.escape(variant), HOME_TOKEN, out, flags=re.IGNORECASE)
    if u and len(u) >= 3:
        out = re.sub(re.escape(u), USER_TOKEN, out, flags=re.IGNORECASE)
    return out


def contains_local(text: str, home: str | None = None, user: str | None = None) -> bool:
    return scrub_local(text, home, user) != text
