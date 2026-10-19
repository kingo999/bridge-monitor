"""Explicit, opt-in capture of classifier inputs.

Only inputs handed to these functions are observed. Nothing here intercepts ordinary
conversations or touches the authoritative router path; the live ``route_work`` is
unchanged and never imports this module.
"""

from datetime import datetime
from pathlib import Path

import psycopg

from bridge.classifier import store
from bridge.classifier.config import ClassifierShadowConfig
from contracts.classifier import ClassifierInput, ObservationCapture
from contracts.router import RouterCase


def configured_facts(cfg: ClassifierShadowConfig, *, dsn: str | None = None,
                     overrides: dict | None = None, now: datetime | None = None) -> tuple[dict, dict]:
    """Trusted facts from reviewed config plus a read-only capacity probe, never from text.

    Returns ``(facts, provenance)``. A failed probe leaves ``code_available`` unknown.
    """
    now = now or store.utcnow()
    provenance = {"source": "classifier_shadow.facts", "captured_at": now.isoformat(),
                  "code_available": "config_only"}
    code = cfg.facts.code_worker_configured
    if code and dsn:
        try:
            with psycopg.connect(dsn, connect_timeout=3) as conn:
                held = conn.execute("SELECT count(*) FROM runtime_jobs WHERE pool='qwen' "
                                    "AND (slot_held OR state='uncertain')").fetchone()[0]
            code = held == 0
            provenance["code_available"] = ("db_probe:qwen_capacity_free" if code
                                            else "db_probe:qwen_capacity_held")
        except Exception:
            code = None
            provenance["code_available"] = "db_probe_failed"
    facts = {"general_available": cfg.facts.general_available, "code_available": code,
             "paid_allowed": cfg.facts.paid_allowed}
    if cfg.facts.tools_available is not None:
        facts["tools_available"] = cfg.facts.tools_available
    for key, value in (overrides or {}).items():
        facts[key] = value
        provenance[f"override:{key}"] = "caller"
    return facts, provenance


def capture_text(dsn: str, cfg: ClassifierShadowConfig, *, origin_kind: str, origin_ref: str,
                 request: str, source: str = "", facts: dict | None = None,
                 facts_provenance: dict | None = None, task_id: str | None = None,
                 work_item_id: str | None = None, replay_id: str | None = None,
                 now: datetime | None = None) -> store.CaptureResult:
    if not cfg.enabled:
        return store.CaptureResult("disabled")
    if facts is None:
        facts, facts_provenance = configured_facts(cfg, dsn=dsn, now=now)
    try:
        item = ClassifierInput(request=request, source=source, facts=facts)
    except ValueError as exc:
        raise ValueError(f"invalid trusted facts or request: {exc}") from exc
    cap = ObservationCapture(origin_kind=origin_kind, origin_ref=origin_ref, replay_id=replay_id,
                             task_id=task_id, work_item_id=work_item_id, input=item,
                             facts_provenance=facts_provenance or {})
    return store.capture(dsn, cap, cfg, now=now)


def capture_router_case(dsn: str, cfg: ClassifierShadowConfig, case_path: str | Path, *,
                        work_item_id: str | None = None, replay_id: str | None = None,
                        now: datetime | None = None) -> store.CaptureResult:
    """Capture a pre-solution router case (the file registered at task arrival)."""
    case = RouterCase.model_validate_json(Path(case_path).read_bytes())
    facts, provenance = configured_facts(cfg, dsn=dsn, now=now)
    facts.update(tests_present=len(case.tests) > 0, context_complete=bool(case.baseline_source.strip()),
                 scope="single_file",
                 verification=(f"{len(case.tests)} trusted executable test vector(s) registered "
                               "at task arrival in an isolated verifier."))
    provenance.update(task_registration="router_case", case_captured_at=case.captured_at.isoformat(),
                      tests_present="router_case.tests", context_complete="router_case.baseline_source",
                      scope="router_case_single_file_contract")
    return capture_text(dsn, cfg, origin_kind=case.origin_kind, origin_ref=case.origin_ref,
                        request=case.task_text, source=case.baseline_source, facts=facts,
                        facts_provenance=provenance, task_id=case.task_id,
                        work_item_id=work_item_id, replay_id=replay_id, now=now)
