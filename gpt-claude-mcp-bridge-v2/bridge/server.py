"""MCP surface: two compatible architect tools and one optional LOCAL-00 tool.

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
from contextlib import asynccontextmanager

import anyio
from mcp.server.fastmcp import FastMCP

from . import config
from .service import ArchitectService
from .local_worker.models import LocalWorkerRequest, LocalWorkerResult
from .local_worker.service import WorkerService


@asynccontextmanager
async def _lifespan(app):
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(_local_service.startup)
        try:
            yield {}
        finally:
            tasks.cancel_scope.cancel()
            with anyio.CancelScope(shield=True):
                await _local_service.aclose()


mcp = FastMCP("GPT Architect via Codex", lifespan=_lifespan)
_service = ArchitectService()
_local_service = WorkerService()

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


@mcp.tool()
async def local_worker(request: LocalWorkerRequest) -> LocalWorkerResult:
    """Explicitly use optional LOCAL-00 for a bounded approved task or worker recovery.
    Responses are unvalidated drafts. The supervisor owns independent checks,
    one feedback retry, acceptance and final changes. No automatic delegation.
    """
    return await _local_service.handle(request)


def main() -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    # stdio is owned by the MCP protocol: never print debug output to stdout.
    mcp.run(transport="stdio")
