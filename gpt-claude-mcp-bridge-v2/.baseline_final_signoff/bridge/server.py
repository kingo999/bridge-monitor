"""MCP surface: exactly two tools, schemas identical to v1.

Stdout is reserved for MCP protocol traffic.  Nothing in this package prints;
diagnostics go to the ``logging`` module configured onto stderr in ``main``.

Tools are ``async`` and push the (long, blocking) Codex turn onto a worker
thread so the MCP event loop keeps serving pings and cancellation.  If the
client cancels the request, the worker is told to kill the Codex process tree
and the handler waits (shielded) until locks are released and the terminal
telemetry record is written.
"""

from __future__ import annotations

import logging
import sys
import threading

import anyio
from mcp.server.fastmcp import FastMCP

from . import config
from .service import ArchitectService

mcp = FastMCP("GPT Architect via Codex")
_service = ArchitectService()

_CLEANUP_WAIT_S = 25.0


async def _run(tool: str, text: str) -> str:
    cancel = threading.Event()
    finished = threading.Event()

    def work() -> str:
        try:
            return _service.execute(tool, text, cancel)
        finally:
            finished.set()

    try:
        return await anyio.to_thread.run_sync(work, abandon_on_cancel=True)
    except anyio.get_cancelled_exc_class():
        cancel.set()
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(finished.wait, _CLEANUP_WAIT_S)
        raise


@mcp.tool()
async def consult_architect(question: str) -> str:
    """Ask the GPT architecture agent about repository design, contracts, and trade-offs.
    GPT reads this repository in a read-only sandbox; it cannot approve changes
    to an agreed design on the human architect's behalf.
    """
    return await _run(config.CONSULT, question)


@mcp.tool()
async def review_implementation(focus: str) -> str:
    """Ask GPT to review the current Git changes for architectural regressions.
    GPT should check actual diffs/source and return findings with paths and
    suggested tests, without modifying files.
    """
    return await _run(config.REVIEW, focus)


def main() -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    # stdio is owned by the MCP protocol: never print debug output to stdout.
    mcp.run(transport="stdio")
