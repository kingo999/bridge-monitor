"""GPT Architect <-> Claude Code MCP bridge, v2 entry point.

Same invocation as v1 (``python architect_bridge.py``); the implementation lives
in the ``bridge`` package next to this file.  Uses the ChatGPT-authenticated
Codex CLI only: there is no API client, API key or API fallback.

Do not print to stdout here or anywhere else: stdout is the MCP channel.
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bridge.server import consult_architect, main, mcp, review_implementation  # noqa: E402,F401

if __name__ == "__main__":
    main()
