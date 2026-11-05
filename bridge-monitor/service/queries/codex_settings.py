"""Bounded, metadata-only fallback for Codex turns without observed effort."""

import asyncio
import json
import os
import shutil
import subprocess
import time
from threading import Lock, Thread
from uuid import UUID

from bridge.desktop_capture import FLOW_EFFORTS


async def _read_settings(executable, sessions):
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    process = await asyncio.create_subprocess_exec(executable, "app-server", "--listen", "stdio://",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        **options)

    async def send(method, params=None, request_id=None):
        message = {"method": method}
        if params is not None:
            message["params"] = params
        if request_id is not None:
            message["id"] = request_id
        process.stdin.write(json.dumps(message).encode("utf-8") + b"\n")
        await process.stdin.drain()

    async def receive(request_id):
        while line := await process.stdout.readline():
            reply = json.loads(line)
            if reply.get("id") == request_id:
                return reply.get("result") or {}
        raise EOFError("Codex app server closed before replying")

    settings = {}
    try:
        async with asyncio.timeout(5):
            await send("initialize", {"clientInfo": {"name": "bridge_flow_metadata", "version": "1"},
                                      "capabilities": {"experimentalApi": True}}, 1)
            if not await receive(1):
                return settings
            await send("initialized")
            for request_id, session_id in enumerate(sessions, 2):
                await send("thread/read", {"threadId": session_id, "includeTurns": False}, request_id)
                thread = (await receive(request_id)).get("thread") or {}
                if thread.get("id") == session_id and thread.get("reasoningEffort") in FLOW_EFFORTS:
                    settings[session_id] = (thread.get("model"), thread["reasoningEffort"])
    finally:
        process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except TimeoutError:
            if process.returncode is None:
                process.kill()
            await process.wait()
    return settings


def _thread_settings(sessions):
    executable = shutil.which("codex.cmd" if os.name == "nt" else "codex")
    # The hook identifies the turns. The reader asks only for their thread settings.
    valid = []
    for session in sessions:
        try:
            if str(UUID(session)) == session:
                valid.append(session)
        except ValueError:
            continue
    if not executable or not valid:
        return {}
    return asyncio.run(_read_settings(executable, valid))


class CodexSettingsCache:
    """Share one background metadata read across dashboard requests."""

    def __init__(self, reader=_thread_settings):
        self._reader = reader
        self._lock = Lock()
        self._cache = {}
        self._refreshing = False

    @property
    def refreshing(self):
        with self._lock:
            return self._refreshing

    def lookup(self, turns):
        turns = turns[:8]
        with self._lock:
            self._cache = {key: self._cache[key] for key in turns if key in self._cache}
            now = time.monotonic()
            missing = [key for key in turns if key not in self._cache or self._cache[key][1] <= now]
            if missing and not self._refreshing:
                self._refreshing = True
                try:
                    Thread(target=self._refresh, args=(missing,), daemon=True).start()
                except RuntimeError:
                    self._refreshing = False
            return {key: value for key, (value, expiry) in self._cache.items() if value and expiry > now}

    def _refresh(self, turns):
        try:
            settings = self._reader(list(dict.fromkeys(session for session, _, _ in turns)))
        except Exception:
            # An unavailable metadata reader leaves observed model labels usable.
            settings = {}
        with self._lock:
            now = time.monotonic()
            for key in turns:
                session, _, model = key
                configured_model, effort = settings.get(session, (None, None))
                matched = effort if configured_model == model and effort in FLOW_EFFORTS else None
                self._cache[key] = matched, now + (60 if matched else 10)
            self._refreshing = False
