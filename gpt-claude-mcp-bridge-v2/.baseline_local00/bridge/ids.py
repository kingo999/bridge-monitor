"""Format validation for identifiers that come from outside the bridge (N14).

Anything that originates in CLI output (thread ids) is validated *at ingress*, before it can
reach a callback, journal, marker, telemetry record, banner or argv.  A value that fails is
never echoed.
"""

from __future__ import annotations

import re

THREAD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$")
HEX_RE = re.compile(r"^[0-9a-f]{8,64}$")
CODE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")


def is_thread_id(value: object) -> bool:
    return isinstance(value, str) and bool(THREAD_RE.fullmatch(value))
