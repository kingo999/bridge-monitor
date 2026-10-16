import asyncio
import json

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from contracts.queries import CohortQuery
from service.http.auth import require_browser_operator
from service.queries.snapshot import CursorExpired, read_publications, read_snapshot


def stream_router(dsn: str, operator_token: str, sessions: dict) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/stream")
    async def stream(request: Request, authorization: str | None = Header(default=None),
                     last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
                     cursor: int = 0, origin: str = "observed"):
        require_browser_operator(request, authorization, operator_token, sessions)
        if last_event_id and last_event_id.isdecimal():
            cursor = max(cursor, int(last_event_id))

        async def events():
            position = max(0, cursor)
            while not await request.is_disconnected():
                try:
                    require_browser_operator(request, authorization, operator_token, sessions)
                except HTTPException:
                    yield "event: session-expired\ndata: {}\n\n"
                    break
                try:
                    rows = await asyncio.to_thread(read_publications, dsn, position, limit=100)
                except CursorExpired as exc:
                    snapshot = await asyncio.to_thread(read_snapshot, dsn, CohortQuery(origin=origin))
                    position = snapshot.cursor.position
                    body = {"earliest_position": exc.earliest_position,
                            "snapshot": snapshot.model_dump(mode="json")}
                    yield f"event: reset\ndata: {json.dumps(body)}\n\n"
                    continue
                for row in rows:
                    position = row["position"]
                    payload = json.dumps(jsonable_encoder(row), separators=(",", ":"))
                    yield f"id: {position}\nevent: publication\ndata: {payload}\n\n"
                if not rows:
                    yield ": heartbeat\n\n"
                    await asyncio.sleep(1)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    return router
