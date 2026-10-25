from datetime import datetime
import psycopg

from fastapi import APIRouter, Header, HTTPException, Request, Response

from contracts.queries import CohortQuery
from service.http.auth import require_browser_operator
from service.queries.history import list_work_items, read_output_preview, read_work_item_detail
from service.queries.metrics import query_metrics
from service.queries.snapshot import read_snapshot
from service.queries.router import (RouterCohortFilter, query_shadow_cohort,
                                    read_router_audit)
from service.queries.classifier import (ClassifierCohortFilter, query_classifier_cohort,
                                        read_classifier_observation)
from service.queries.classifier_activity import query_classifier_activity


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

    @router.get("/router/cohort")
    def router_cohort(request: Request, authorization: str | None = Header(default=None),
                      origin: str = "observed", created_from: datetime | None = None,
                      created_until: datetime | None = None,
                      model_profile: str | None = None, prompt_version: str | None = None,
                      verifier_ref: str | None = None, work_item_id: str | None = None):
        require_browser_operator(request, authorization, operator_token, sessions)
        try:
            filters = RouterCohortFilter(origin=origin, created_from=created_from,
                created_until=created_until, model_profile=model_profile,
                prompt_version=prompt_version, verifier_ref=verifier_ref,
                work_item_id=work_item_id)
            return query_shadow_cohort(dsn, filters)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/router/audit/{work_item_id}")
    def router_audit(work_item_id: str, request: Request,
                     authorization: str | None = Header(default=None)):
        require_browser_operator(request, authorization, operator_token, sessions)
        audit = read_router_audit(dsn, work_item_id)
        if audit is None:
            raise HTTPException(404, "work item not found")
        return audit

    @router.get("/codex-claude/usage-notices")
    def usage_notices(request: Request, authorization: str | None = Header(default=None)):
        require_browser_operator(request, authorization, operator_token, sessions)
        from service.queries.usage_notices import query_usage_notices
        return query_usage_notices(dsn)

    @router.get("/codex-claude/cohort")
    @router.get("/codex-claude/export")
    def codex_claude_report(request: Request, authorization: str | None = Header(default=None),
                           origin: str = "observed", created_from: datetime | None = None,
                           created_until: datetime | None = None, as_of: datetime | None = None,
                           completed_only: bool = False,
                           category: str = "", project: str = "", model: str = "",
                           profile: str = "", policy_digest: str = "", evidence: str = "",
                           format: str = "jsonl"):
        require_browser_operator(request, authorization, operator_token, sessions)
        from service.queries.codex_claude import query_codex_claude, export_codex_claude
        try:
            for value in (created_from, created_until, as_of):
                if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                    raise ValueError("timezone-aware filter timestamps required")
            report = query_codex_claude(dsn, CohortQuery(origin=origin, created_from=created_from,
                created_until=created_until, as_of=as_of, completed_only=completed_only,
                consumer_stage="gpt"), category=category, project=project,
                model=model, profile=profile, policy_digest=policy_digest, evidence=evidence)
            if request.url.path.endswith("/export"):
                media = "application/x-ndjson" if format == "jsonl" else "text/csv"
                return Response(export_codex_claude(report, format), media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="codex-claude.{format}"',
                             "Cache-Control": "no-store"})
            return report
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/codex-claude/decisions/{work_item_id}")
    def codex_claude_decision(work_item_id: str, request: Request,
                             authorization: str | None = Header(default=None)):
        require_browser_operator(request, authorization, operator_token, sessions)
        with psycopg.connect(dsn) as conn:
            if not conn.execute("SELECT to_regclass('cc_decisions')").fetchone()[0]:
                raise HTTPException(404, "No Codex/Claude decision recorded")
            row = conn.execute("SELECT decision_id,payload,prompt_sha256,recorded_at FROM cc_decisions "
                               "WHERE work_item_id=%s", (work_item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "No Codex/Claude decision recorded")
        return dict(decision_id=row[0], decision=row[1], prompt_sha256=row[2], recorded_at=row[3])

    @router.get("/classifier/activity")
    def classifier_activity(request: Request, authorization: str | None = Header(default=None)):
        require_browser_operator(request, authorization, operator_token, sessions)
        try:
            return query_classifier_activity(dsn)
        except psycopg.Error as exc:
            raise HTTPException(503, 'Classifier activity unavailable') from exc

    @router.get("/classifier/cohort")
    def classifier_cohort(request: Request, authorization: str | None = Header(default=None),
                          origin_kind: str = "all", model: str | None = None,
                          capture_method: str = 'all',
                          profile_id: str | None = None, rubric_version: str | None = None,
                          created_from: datetime | None = None,
                          created_until: datetime | None = None):
        require_browser_operator(request, authorization, operator_token, sessions)
        try:
            filters = ClassifierCohortFilter(origin_kind=origin_kind, model=model, capture_method=capture_method,
                profile_id=profile_id, rubric_version=rubric_version,
                created_from=created_from, created_until=created_until)
            return query_classifier_cohort(dsn, filters)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except psycopg.Error as exc:
            raise HTTPException(503, 'Classifier cohort unavailable') from exc

    @router.get("/classifier/observations/{observation_id}")
    def classifier_observation(observation_id: str, request: Request,
                               authorization: str | None = Header(default=None)):
        require_browser_operator(request, authorization, operator_token, sessions)
        detail = read_classifier_observation(dsn, observation_id)
        if detail is None:
            raise HTTPException(404, "classifier observation not found")
        return detail

    return router
