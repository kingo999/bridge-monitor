from datetime import datetime
import psycopg

from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

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


def validate_design_mode_owner_id(value) -> str | None:
    """Optional single-operator display binding for the Monitor decision filter; not authorization.

    None (setting omitted) disables the filter. Any present value must be an exact owner_id string.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("design_mode_owner_id must be a non-empty string without surrounding whitespace; "
                         "comment the setting out to disable the decision filter")
    return value


def query_router(dsn: str, operator_token: str, sessions: dict, *,
                 preview_max_chunks: int = 80, preview_max_bytes: int = 16384,
                 timeline_head_events: int = 100, timeline_tail_events: int = 400,
                 work_item_max_limit: int = 100, design_mode_owner_id: str | None = None) -> APIRouter:
    design_mode_owner_id = validate_design_mode_owner_id(design_mode_owner_id)
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
                           format: str = "jsonl", audit_snapshot: bool = False):
        require_browser_operator(request, authorization, operator_token, sessions)
        from service.queries.codex_claude import query_codex_claude, export_codex_claude
        try:
            for value in (created_from, created_until, as_of):
                if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                    raise ValueError("timezone-aware filter timestamps required")
            filters = CohortQuery(origin=origin, created_from=created_from,
                created_until=created_until, as_of=as_of, completed_only=completed_only, consumer_stage="gpt")
            if audit_snapshot and not request.url.path.endswith("/export"):
                from service.queries.audit import cohort_snapshot
                from service.http.audit import audit_owner
                with psycopg.connect(dsn) as conn:
                    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                    report = query_codex_claude(dsn, filters, category=category, project=project,
                        model=model, profile=profile, policy_digest=policy_digest, evidence=evidence, connection=conn)
                    try:
                        with conn.transaction():
                            report["audit_token"] = cohort_snapshot(conn, audit_owner(request, authorization), report)
                    except (psycopg.Error, ValueError):
                        report["audit_unavailable"] = True
            else:
                report = query_codex_claude(dsn, filters, category=category, project=project,
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

    # Design Mode Monitor: read-only, behind the existing operator boundary. No actions.
    @router.get("/audit/design-handoffs")
    def design_handoffs(request: Request, authorization: str | None = Header(default=None),
                        workflow: str = "", before: int | None = None, limit: int = 50):
        require_browser_operator(request, authorization, operator_token, sessions)
        return _design_handoffs(dsn, workflow, before, max(0, min(limit, 100)))

    @router.get("/design-mode/workflows")
    def design_mode_workflows(request: Request, authorization: str | None = Header(default=None),
                              limit: int = 100, repository_id: str | None = None,
                              awaiting_my_decision: bool = False):
        require_browser_operator(request, authorization, operator_token, sessions)
        return _design_mode_list(dsn, max(1, min(limit, 500)), repository_id or None,
                                 owner_id=design_mode_owner_id, awaiting_my_decision=awaiting_my_decision)

    @router.get("/design-mode/workflows/{workflow_id}")
    def design_mode_workflow(workflow_id: str, request: Request,
                             authorization: str | None = Header(default=None)):
        require_browser_operator(request, authorization, operator_token, sessions)
        detail = _design_mode_detail(dsn, workflow_id)
        if detail is None:
            raise HTTPException(404, "design mode workflow not found")
        return detail

    @router.get("/design-mode/view", response_class=HTMLResponse)
    def design_mode_view(request: Request, authorization: str | None = Header(default=None)):
        require_browser_operator(request, authorization, operator_token, sessions)
        return HTMLResponse(_DESIGN_MODE_PAGE, headers={"Cache-Control": "no-store"})

    return router


_DM_WORKFLOW = ("workflow_id", "owner_id", "repository_id", "project_root", "request_key", "status",
                "status_actor", "status_reason", "current_iteration", "version", "created_at", "updated_at")


def _design_mode_available(conn) -> bool:
    return bool(conn.execute("SELECT to_regclass('dm_workflows')").fetchone()[0])


_DM_LABELS = {"awaiting_planner": "Planning (Codex) on iteration {n}", "paused": "Paused on iteration {n}",
              "review_claimed": "Claude review in progress on iteration {n}",
              "review_unknown": "Review outcome unknown on iteration {n}", "ready": "Ready for Chat",
              "delivered": "Delivered to Chat on iteration {n}", "accepted": "Accepted on iteration {n}",
              "rejected": "Rejected on iteration {n}", "stale": "Stale on iteration {n}",
              "cancelled": "Cancelled on iteration {n}", "failed": "Failed on iteration {n}"}
# Decision counts come from the immutable audit trail (chat accept/reject/revise actions).
_DM_COUNTS = ("(SELECT count(*) FROM dm_audit a WHERE a.workflow_id=w.workflow_id AND a.action='accept'),"
              "(SELECT count(*) FROM dm_audit a WHERE a.workflow_id=w.workflow_id AND a.action='reject'),"
              "(SELECT count(*) FROM dm_audit a WHERE a.workflow_id=w.workflow_id AND a.action='revise')")


def _dm_row(row) -> dict:
    item = dict(zip(_DM_WORKFLOW, row[:len(_DM_WORKFLOW)]))
    item["accepted_count"], item["rejected_count"], item["revision_count"] = row[len(_DM_WORKFLOW):]
    template = _DM_LABELS.get(item["status"], "{s} on iteration {n}")
    if item["status"] == "paused" and item["status_reason"] == "revision_requested":
        template = "Awaiting revised brief on iteration {n}"
    item["outcome_label"] = template.format(n=item["current_iteration"], s=item["status"])
    return item


def _design_handoffs(dsn: str, workflow: str, before: int | None, limit: int) -> dict:
    # Successful commands are recorded once by the Design Mode transaction. Resume
    # names the receiving planner as actor. Package retrieval is an outbound delivery.
    source = (" FROM dm_audit a WHERE ((actor='chat' AND action IN "
              "('submit','revise','revise_requested','accept','reject','cancel','pause','delivery_receipt')) "
              "OR (actor='codex' AND action='resume'))")
    params = ()
    if workflow:
        source += " AND workflow_id=%s"
        params = (workflow,)
    with psycopg.connect(dsn) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        if not _design_mode_available(conn):
            return {"available": False, "total": None, "rows": [], "next": None}
        total = conn.execute("SELECT count(*)" + source, params).fetchone()[0]
        last_brief = chat_activity = None
        if limit and before is None:
            brief_scope = " AND workflow_id=%s" if workflow else ""
            last_brief = conn.execute("SELECT recorded_at,detail->'chat_selection' FROM dm_audit "
                "WHERE actor='chat' AND action IN ('submit','revise')" + brief_scope +
                " ORDER BY audit_id DESC LIMIT 1", params).fetchone()
            chat_activity = _design_chat_activity(conn, workflow)
        if before is not None:
            source += " AND audit_id < %s"
            params += (before,)
        rows = conn.execute("SELECT audit_id::text,recorded_at,workflow_id,"
                            "COALESCE((detail->>'new_iteration')::integer,iteration),action"
                            + source + " ORDER BY a.audit_id DESC LIMIT %s",
                            params + (limit + 1,)).fetchall() if limit else []
    keys = ("id", "time", "workflow_id", "iteration", "action")
    report = {"available": True, "total": total, "rows": [dict(zip(keys, r)) for r in rows[:limit]],
              "next": rows[limit - 1][0] if limit and len(rows) > limit else None}
    if last_brief and isinstance(last_brief[1], dict):
        selection = {key: last_brief[1][key] for key in ("model", "effort")
                     if isinstance(last_brief[1].get(key), str)}
        if selection:
            report["chat_selection"] = {**selection, "time": last_brief[0]}
    if chat_activity:
        report["chat_activity"] = chat_activity
    return report


# ChatGPT's work is observable only between retrieving the current iteration's review
# package and confirming its delivery. The expiry bounds an abandoned retrieval.
DESIGN_CHAT_PREPARING_SECONDS = 300


def _design_chat_activity(conn, workflow: str) -> dict | None:
    # Status-bound: advancing (revise/accept/reject), cancelling or staling the workflow
    # leaves both 'ready' and 'delivered', which clears the state.
    row = conn.execute(
        "SELECT w.workflow_id,w.current_iteration,"
        "CASE WHEN w.status='delivered' THEN 'awaiting_decision' ELSE 'preparing' END,"
        "COALESCE(d.delivered_at,r.recorded_at) FROM dm_workflows w "
        "LEFT JOIN LATERAL (SELECT a.recorded_at FROM dm_audit a WHERE a.workflow_id=w.workflow_id "
        "AND a.iteration=w.current_iteration AND a.actor='chat' AND a.action='package_retrieved' "
        "ORDER BY a.audit_id DESC LIMIT 1) r ON true "
        "LEFT JOIN dm_deliveries d ON d.workflow_id=w.workflow_id AND d.iteration=w.current_iteration "
        "WHERE (%s::text = '' OR w.workflow_id=%s) AND ("
        "(w.status='ready' AND d.receipt_id IS NULL AND r.recorded_at <= now() "
        "AND r.recorded_at > now() - make_interval(secs => %s)) "
        "OR (w.status='delivered' AND d.receipt_id IS NOT NULL)) "
        "ORDER BY w.status='ready' DESC, COALESCE(d.delivered_at,r.recorded_at) DESC LIMIT 1",
        (workflow, workflow, DESIGN_CHAT_PREPARING_SECONDS)).fetchone()
    if not row:
        return None
    activity = {"state": row[2], "workflow_id": row[0], "iteration": row[1], "since": row[3]}
    if row[2] == "preparing":
        activity["expires_in_seconds"] = DESIGN_CHAT_PREPARING_SECONDS
    return activity


def _design_mode_list(dsn: str, limit: int, repository_id: str | None = None, *,
                      owner_id: str | None = None, awaiting_my_decision: bool = False) -> dict:
    columns = ",".join("w." + k for k in _DM_WORKFLOW) + "," + _DM_COUNTS
    with psycopg.connect(dsn) as conn:
        if not _design_mode_available(conn):  # missing schema wins: exact legacy response
            return {"available": False, "workflows": [], "projects": []}
        rows = []
        if not awaiting_my_decision:
            rows = conn.execute("SELECT " + columns + " FROM dm_workflows w "
                                "WHERE (%s::text IS NULL OR w.repository_id=%s) "
                                "ORDER BY w.updated_at DESC LIMIT %s",
                                (repository_id, repository_id, limit)).fetchall()
        elif owner_id is not None:
            # "Awaiting my decision": exact configured owner and delivered only, filtered before LIMIT.
            # Without a binding no workflow query runs at all.
            rows = conn.execute("SELECT " + columns + " FROM dm_workflows w "
                                "WHERE (%s::text IS NULL OR w.repository_id=%s) "
                                "AND w.owner_id=%s AND w.status='delivered' "
                                "ORDER BY w.updated_at DESC LIMIT %s",
                                (repository_id, repository_id, owner_id, limit)).fetchall()
        projects = conn.execute("SELECT DISTINCT repository_id,project_root FROM dm_workflows "
                                "ORDER BY repository_id,project_root").fetchall()
    return {"available": True, "decision_filter_configured": owner_id is not None,
            "workflows": [_dm_row(row) for row in rows],
            "projects": [{"repository_id": r, "project_root": p} for r, p in projects]}


def _design_mode_detail(dsn: str, workflow_id: str) -> dict | None:
    with psycopg.connect(dsn) as conn:
        if not _design_mode_available(conn):
            return None
        row = conn.execute("SELECT " + ",".join("w." + k for k in _DM_WORKFLOW) + "," + _DM_COUNTS +
                           " FROM dm_workflows w WHERE w.workflow_id=%s", (workflow_id,)).fetchone()
        if not row:
            return None

        def rows(sql, keys):
            return [dict(zip(keys, r)) for r in conn.execute(sql, (workflow_id,)).fetchall()]
        iteration_keys = ("iteration", "request_key", "payload_sha256", "task_id", "work_item_id",
                          "source_head", "created_at")
        hop_keys = ("iteration", "sequence", "from_actor", "to_actor", "kind", "attempt", "state",
                    "worker_call_id", "decision_id", "result_id", "artifact_sha256", "reason",
                    "started_at", "finished_at")
        return {**_dm_row(row),
                "iterations": rows("SELECT " + ",".join(iteration_keys) + " FROM dm_iterations "
                                   "WHERE workflow_id=%s ORDER BY iteration", iteration_keys),
                "hops": rows("SELECT " + ",".join(hop_keys) + " FROM dm_hops WHERE workflow_id=%s "
                             "ORDER BY iteration,sequence", hop_keys),
                # Versioned references only: (iteration, kind, sha256). Bodies stay with the owner.
                "artifacts": rows("SELECT iteration,kind,sha256,octet_length(body),created_at FROM dm_artifacts "
                                  "WHERE workflow_id=%s ORDER BY iteration,kind",
                                  ("iteration", "kind", "sha256", "bytes", "created_at")),
                "evidence": rows("SELECT iteration,path,sha256,read_at FROM dm_evidence WHERE workflow_id=%s "
                                 "ORDER BY iteration,path", ("iteration", "path", "sha256", "read_at")),
                "deliveries": rows("SELECT iteration,receipt_id,package_sha256,delivered_at FROM dm_deliveries "
                                   "WHERE workflow_id=%s ORDER BY iteration",
                                   ("iteration", "receipt_id", "package_sha256", "delivered_at")),
                "audit": rows("SELECT audit_id,iteration,actor,action,detail,recorded_at FROM dm_audit "
                              "WHERE workflow_id=%s ORDER BY audit_id DESC LIMIT 200",
                              ("audit_id", "iteration", "actor", "action", "detail", "recorded_at"))}


_DESIGN_MODE_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Design Mode · Bridge Monitor</title>
<style>body{font:14px system-ui,sans-serif;margin:1.5rem;color:#1d2433}table{border-collapse:collapse;width:100%;margin:.5rem 0 1.5rem}
th,td{border-bottom:1px solid #d8dce5;padding:.3rem .5rem;text-align:left;vertical-align:top}tr.pick{cursor:pointer}
tr.pick:hover{background:#f1f4fa}code{font-size:12px}.note{color:#5b6475}</style></head>
<body><h1>Design Mode</h1><p class="note">Read-only view. No actions are available here; workflows are
driven only by the owning parent session. Sign in through the Bridge Monitor dashboard first.</p>
<p class="note">Outcomes: Ready for Chat · Delivered to Chat on iteration N · Accepted on iteration N ·
Rejected on iteration N. Artifacts are listed as versioned references (iteration, kind, sha256); bodies are not served.</p>
<p><label>Project <select id="project-filter"><option value="">All projects</option></select></label>
<input type="checkbox" id="awaiting-my-decision" disabled aria-describedby="awaiting-my-decision-help">
<label for="awaiting-my-decision">Awaiting my decision</label></p>
<p id="awaiting-my-decision-help" class="note">Shown after Chat delivers a plan and review, until you accept, revise or reject.
Not shown: workflows that became stale because the source changed, whose review outcome is unknown, or that are paused awaiting a revised brief.</p>
<p id="error" role="status"></p><p id="filter-status" role="status" aria-live="polite"></p><table id="list"><thead><tr><th>Workflow</th><th>Feature key</th><th>Iteration</th><th>Project</th><th>Outcome</th>
<th>Accepted / Rejected / Revisions</th><th>Actor</th><th>Reason</th><th>Updated</th></tr></thead><tbody></tbody></table>
<section id="detail"></section>
<script>
const $ = (tag, text) => { const e = document.createElement(tag); if (text !== undefined && text !== null) e.textContent = String(text); return e; };
async function get(path) { const r = await fetch(path, {credentials: 'same-origin', cache: 'no-store'});
  if (!r.ok) throw new Error(r.status === 401 ? 'Operator session required' : r.status + ' ' + r.statusText); return r.json(); }
function table(title, rows, keys) { const box = document.createDocumentFragment(); box.append($('h3', title));
  const t = $('table'); const head = $('tr'); keys.forEach(k => head.append($('th', k))); t.append(head);
  rows.forEach(row => { const tr = $('tr'); keys.forEach(k => { const v = row[k];
    tr.append($('td', v !== null && typeof v === 'object' ? JSON.stringify(v) : v)); }); t.append(tr); });
  box.append(t); return box; }
let selected = null, detailSeq = 0, listSeq = 0, timer = null;
async function show(id) { selected = id; const seq = ++detailSeq;
  const d = await get('/api/v1/design-mode/workflows/' + encodeURIComponent(id));
  if (seq !== detailSeq || selected !== id) return;
  const s = document.getElementById('detail'); s.replaceChildren($('h2', 'Workflow ' + d.workflow_id),
    $('p', d.outcome_label + ' · accepted ' + d.accepted_count + ' · rejected ' + d.rejected_count +
      ' · revisions ' + d.revision_count),
    $('p', 'Project ' + d.repository_id + ' · ' + d.project_root + ' · owner ' + d.owner_id + ' · iteration ' +
      d.current_iteration + ' · version ' + d.version + ' · ' + d.status + ' (' + d.status_actor + ')' +
      (d.status_reason ? ' · ' + d.status_reason : '')),
    table('Timeline (logical actors chat → codex → claude → chat)', d.hops.map(h => ({...h,
      actors: h.from_actor + ' → ' + h.to_actor})),
      ['iteration','sequence','actors','from_actor','to_actor','kind','attempt','state','reason','worker_call_id','decision_id','result_id','started_at','finished_at']),
    table('Iterations', d.iterations, ['iteration','request_key','task_id','work_item_id','source_head','created_at']),
    table('Artifacts (versioned references)', d.artifacts, ['iteration','kind','sha256','bytes','created_at']),
    table('Planner evidence', d.evidence, ['iteration','path','sha256','read_at']),
    table('Delivery receipts', d.deliveries, ['iteration','receipt_id','package_sha256','delivered_at']),
    table('Audit', d.audit, ['audit_id','iteration','actor','action','detail','recorded_at'])); }
const filter = document.getElementById('project-filter');
const decision = document.getElementById('awaiting-my-decision'), filterStatus = document.getElementById('filter-status');
function clearDetail() { selected = null; detailSeq++; document.getElementById('detail').replaceChildren(); }
// #error: request errors and missing schema (cleared per load). #filter-status: set only from successful responses.
async function load() { const err = document.getElementById('error'); err.textContent = '';
  const seq = ++listSeq, filtered = decision.checked;
  try { const params = new URLSearchParams();
  if (filter.value) params.set('repository_id', filter.value);
  if (filtered) params.set('awaiting_my_decision', 'true');
  const q = params.toString() ? '?' + params.toString() : '';
  const data = await get('/api/v1/design-mode/workflows' + q);
  if (seq !== listSeq) return;
  if (!data.available) { decision.checked = false; decision.disabled = true; filterStatus.textContent = ''; }
  else if (!data.decision_filter_configured) { decision.disabled = true;
    filterStatus.textContent = 'Decision filter unavailable: configure a Design Mode owner.';
    if (decision.checked) { decision.checked = false; return load(); } }
  else { decision.disabled = false;
    filterStatus.textContent = filtered && !data.workflows.length ? 'No workflows awaiting your decision' : '';
    // A selected row that left the current filtered result closes; detailSeq stops late replies.
    // (A matching row beyond the list limit also counts as having left.)
    if (filtered && selected !== null && !data.workflows.some(w => w.workflow_id === selected)) clearDetail(); }
  const body = document.querySelector('#list tbody'); body.replaceChildren();
  if (!data.available) err.textContent = 'Design Mode tables are not migrated.';
  const known = new Set(Array.from(filter.options).map(o => o.value));
  (data.projects || []).forEach(p => { if (!known.has(p.repository_id)) { known.add(p.repository_id);
    const o = $('option', p.repository_id + ' · ' + p.project_root); o.value = p.repository_id; filter.append(o); } });
  data.workflows.forEach(w => { const tr = $('tr'); tr.className = 'pick';
    const idCell = $('td'), btn = document.createElement('button'); btn.type = 'button';
    btn.textContent = String(w.workflow_id); idCell.append(btn); tr.append(idCell);
    [w.request_key, w.current_iteration, w.repository_id + ' · ' + w.project_root, w.outcome_label,
     w.accepted_count + ' / ' + w.rejected_count + ' / ' + w.revision_count, w.status_actor,
     w.status_reason, w.updated_at].forEach(v => tr.append($('td', v)));
    tr.addEventListener('click', () => show(w.workflow_id).catch(e => { err.textContent = e.message; }));
    body.append(tr); }); }
  catch (e) { if (seq === listSeq) { err.textContent = e.message; filterStatus.textContent = ''; } } }
function refresh() { if (document.hidden) return; load();
  if (selected) { const id = selected; show(id).catch(e => { if (selected === id) document.getElementById('error').textContent = e.message; }); } }
function schedule() { if (timer) { clearInterval(timer); timer = null; }
  if (!document.hidden) timer = setInterval(refresh, 10000); }
filter.addEventListener('change', () => { clearDetail(); load(); });
decision.addEventListener('change', () => { clearDetail(); load(); });
document.addEventListener('visibilitychange', () => { schedule(); if (!document.hidden) refresh(); });
load(); schedule();
</script></body></html>
"""
