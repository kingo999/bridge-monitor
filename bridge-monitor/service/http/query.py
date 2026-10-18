from datetime import datetime

from fastapi import APIRouter, Header, HTTPException, Request

from contracts.queries import CohortQuery
from service.http.auth import require_browser_operator
from service.queries.history import list_work_items, read_output_preview, read_work_item_detail
from service.queries.metrics import query_metrics
from service.queries.snapshot import read_snapshot


def query_router(dsn: str, operator_token: str, sessions: dict, *,
                 preview_max_chunks: int = 80, preview_max_bytes: int = 16384,
                 timeline_head_events: int = 100, timeline_tail_events: int = 400,
                 work_item_max_limit: int = 100) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    def cohort(origin: str, consumer_stage: str, created_from: datetime | None,
               created_until: datetime | None, as_of: datetime | None,
               completed_only: bool, guidance_group: str | None = None,
               failure_category: str | None = None, repair_effort: str | None = None,
               example_reuse_status: str | None = None) -> CohortQuery:
        return CohortQuery(origin=origin, consumer_stage=consumer_stage,
                           created_from=created_from, created_until=created_until,
                           as_of=as_of, completed_only=completed_only,
                           guidance_group=guidance_group, failure_category=failure_category,
                           repair_effort=repair_effort,
                           example_reuse_status=example_reuse_status)

    @router.get("/snapshot")
    def snapshot(request: Request, authorization: str | None = Header(default=None),
                 origin: str = "observed", limit: int = 50):
        require_browser_operator(request, authorization, operator_token, sessions)
        try:
            return read_snapshot(dsn, CohortQuery(origin=origin), limit=limit)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/work-items")
    def work_items(request: Request, authorization: str | None = Header(default=None),
                   origin: str = "observed", consumer_stage: str = "claude",
                   created_from: datetime | None = None, created_until: datetime | None = None,
                   as_of: datetime | None = None, completed_only: bool = False,
                   guidance_group: str | None = None, failure_category: str | None = None,
                   repair_effort: str | None = None, example_reuse_status: str | None = None,
                   limit: int = 50, offset: int = 0):
        require_browser_operator(request, authorization, operator_token, sessions)
        try:
            return list_work_items(dsn, cohort(origin, consumer_stage, created_from,
                                               created_until, as_of, completed_only,
                                               guidance_group, failure_category, repair_effort,
                                               example_reuse_status),
                                   limit=limit, offset=offset,
                                   max_limit=work_item_max_limit)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/work-items/{work_item_id}")
    def work_item(work_item_id: str, request: Request,
                  authorization: str | None = Header(default=None)):
        require_browser_operator(request, authorization, operator_token, sessions)
        detail = read_work_item_detail(dsn, work_item_id,
                                       head_events=timeline_head_events,
                                       tail_events=timeline_tail_events)
        if detail is None:
            raise HTTPException(404, "work item not found")
        return detail

    @router.get("/work-items/{work_item_id}/calls/{call_id}/output-preview")
    def output_preview(work_item_id: str, call_id: str, request: Request,
                       authorization: str | None = Header(default=None)):
        require_browser_operator(request, authorization, operator_token, sessions)
        preview = read_output_preview(dsn, work_item_id, call_id,
                                      role="operator",
                                      max_chunks=preview_max_chunks,
                                      max_bytes=preview_max_bytes)
        if preview is None:
            raise HTTPException(404, "call not found in selected work item")
        return preview

    @router.get("/metrics")
    def metrics(request: Request, authorization: str | None = Header(default=None),
                origin: str = "observed", consumer_stage: str = "claude",
                created_from: datetime | None = None, created_until: datetime | None = None,
                as_of: datetime | None = None, completed_only: bool = False,
                guidance_group: str | None = None, failure_category: str | None = None,
                repair_effort: str | None = None, example_reuse_status: str | None = None):
        require_browser_operator(request, authorization, operator_token, sessions)
        return query_metrics(dsn, cohort(origin, consumer_stage, created_from,
                                         created_until, as_of, completed_only,
                                         guidance_group, failure_category, repair_effort,
                                         example_reuse_status))

    return router
