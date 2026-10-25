"""Independent read-only Codex/Claude cohort; historical Experiment metrics stay unchanged."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import csv
import io
import json
import math
import statistics
import psycopg
from psycopg.rows import dict_row
from contracts.queries import CohortQuery
from service.queries.cohorts import cutoff, leaf_work_items
from service.queries.direct_execution import execution_metadata
from service.queries.history import _gpt_work_judgments
from service.queries.codex_claude_usage import observed_counters

DEFINITION="codex-claude-v1"

def _duration(values):
    values=sorted(values)
    return {"samples":len(values),"median":statistics.median(values) if values else None,
            "p95":values[max(0,math.ceil(len(values)*.95)-1)] if values else None}

def query_codex_claude(dsn, query: CohortQuery, *, category="", project="", model="",
                       profile="", policy_digest="", evidence=""):
    as_of=cutoff(query)
    rows=[]
    readiness={"routing_enabled":False,"reason":"Automatic routing disabled by active policy; spending and token values are statistics only"}
    with psycopg.connect(dsn) as conn,conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        active=conn.execute("SELECT c.policy FROM runtime_active_policy a JOIN config_versions c "
            "ON c.version=a.version WHERE a.singleton=true").fetchone()
        extension=(active[0].get("codex_claude") or {}) if active else {}
        if extension.get("enabled"):
            readiness={"routing_enabled":True,"reason":"Policy enabled; dispatch requires an approved runtime model and authorized supplied context"}
        ids=leaf_work_items(conn,query,as_of)
        facts=execution_metadata(conn,ids,as_of)
        judgments=_gpt_work_judgments(conn,ids,as_of)
        migrated=bool(conn.execute("SELECT to_regclass('cc_decisions')").fetchone()[0])
        decisions={}
        if migrated:
            decisions={w:{"decision_id":d,"decision":p,"created_at":created} for w,d,p,created in conn.execute(
                "SELECT work_item_id,decision_id,payload,recorded_at FROM cc_decisions WHERE recorded_at<=%s",
                (as_of,))}
        with conn.cursor(row_factory=dict_row) as cur:
            work=[dict(r) for r in cur.execute("SELECT work_item_id,task_id,category,origin,created_at FROM work_items "
                "WHERE work_item_id=ANY(%s) ORDER BY created_at DESC,work_item_id",(ids,))]
            ev=[dict(r) for r in cur.execute("SELECT e.event_id,e.work_item_id,e.call_id,e.event_type,"
                "e.occurred_at,e.requested_model,e.reported_model,e.payload,c.parent_call_id,"
                "e.model_execution_id,e.payload_hash,e.producer_id,e.producer_incarnation,e.origin,"
                "e.capture_completeness,e.unresolved_references "
                "FROM events e LEFT JOIN calls c USING(call_id) "
                "WHERE e.work_item_id=ANY(%s) AND e.occurred_at<=%s AND e.received_at<=%s "
                "AND (c.call_id IS NULL OR c.execution_mode='production') "
                "ORDER BY e.occurred_at,e.event_id",(ids,as_of,as_of))]
        by_work=defaultdict(list)
        for event in ev:by_work[event["work_item_id"]].append(event)
        receipts=defaultdict(list)
        if migrated:
            for w,amount,counters,receipt in conn.execute("SELECT h.work_item_id,r.amount,r.counters,r.receipt_sha256 "
                "FROM cc_receipts r JOIN cc_reservations h USING(call_id) WHERE r.observed_at<=%s AND r.recorded_at<=%s",
                (as_of,as_of)):
                receipts[w].append((amount,counters,receipt))
        from service.queries.provider_cost import provider_quotes
        settled_calls={row[0] for row in conn.execute("SELECT call_id FROM cc_receipts WHERE recorded_at<=%s",(as_of,))} if migrated else set()
        for quote in provider_quotes(conn,as_of,exclude_calls=settled_calls):
            receipts[quote["work_item_id"]].append((quote["amount"],quote["counters"],quote["receipt_sha256"]))
        # Include blocked/keep-owner decisions even when no worker call was admitted.
        existing={w["work_item_id"] for w in work}
        for w,item in decisions.items():
            if w in existing:continue
            created=item["created_at"]
            if query.origin not in ("observed","all") or query.completed_only:continue
            if query.created_from and created<query.created_from:continue
            if query.created_until and created>=query.created_until:continue
            work.append(dict(work_item_id=w,task_id="",category=item["decision"]["facts"]["category"],
                origin="observed",created_at=created))
        for w in work:
            wid=w["work_item_id"];meta=facts.get(wid,{})
            direct=meta.get("execution_path")=="direct_codex"
            events=by_work[wid]
            claude=any(e["event_type"]=="call.dispatched" and e["payload"].get("destination")=="claude" for e in events)
            if not(direct or claude or wid in decisions):continue
            record=meta.get("direct_execution") or {}
            primary=[e for e in events if e["parent_call_id"]=="gpt:"+w["task_id"]]
            requested=next((e["requested_model"] for e in reversed(primary) if e["requested_model"]),meta.get("requested_model"))
            reported=next((e["reported_model"] for e in reversed(primary) if e["reported_model"]),None)
            if direct:
                reported=next((e["payload"]["usage"].get("runtime_model") for e in reversed(events)
                    if e["event_type"]=="direct_task.recorded" and e["payload"].get("usage")
                    and e["payload"]["usage"].get("runtime_model")),None)
            decision_entry=decisions.get(wid)
            decision=decision_entry["decision"] if decision_entry else {}
            selected=decision.get("selected_profile")
            chosen_model=(decision.get("policy_snapshot",{}).get("profiles",{}).get(selected,{}) or {}).get("model_id")
            requested=chosen_model or requested
            complete=record.get("status")=="completed" if direct else any(e["event_type"]=="call.completed" for e in events)
            failed=any(e["event_type"] in ("call.failed","attempt.failed","attempt.uncertain") for e in events)
            terminal=[e["occurred_at"] for e in events if e["event_type"] in ("call.completed","call.failed","call.cancelled")]
            if direct and record.get("observed_at"):
                end=datetime.fromisoformat(record["observed_at"].replace("Z","+00:00")) if record.get("status") in ("completed","failed","cancelled") else None
            else:end=max(terminal) if terminal else None
            duration=max(0,(end-w["created_at"]).total_seconds()) if end else None
            known=receipts[wid]
            cost=sum((r[0] for r in known),Decimal(0)) if known else None
            counters=observed_counters(events,direct=direct)
            judgement=judgments.get(wid)
            row={**w,"created_at":w["created_at"].isoformat(),"executor":"codex" if direct else "claude" if claude else "not_dispatched",
                "requested_model":requested,"reported_model":reported,
                "project":record.get("project_root") or decision.get("facts",{}).get("project_root"),"complexity":decision.get("facts",{}).get("complexity"),
                "risk":decision.get("facts",{}).get("risk"),"profile":selected,
                "policy_digest":decision.get("policy_digest"),"route_reason":decision.get("reason") or meta.get("bypass_reason") or "legacy_route_evidence",
                "override":decision.get("requested_override"),"decision_id":decision_entry["decision_id"] if decision_entry else None,
                "status":record.get("status") if direct else "failed_or_uncertain" if failed else "completed" if complete else decision.get("action","in_progress"),
                "caller_reported_complete":complete,"parent_accepted":judgement=="accepted",
                "human_accepted":None,"checks_passed":None,"rework":judgement in ("revised","replaced"),
                "duration_seconds":duration,"cost_usd_equivalent":str(cost) if cost is not None else None,
                "usage":counters,"receipt_hashes":[r[2] for r in known],
                "evidence_status":"parent_accepted" if judgement=="accepted" else "caller_reported" if complete else "unverified"}
            if category and row["category"]!=category:continue
            if project and row["project"]!=project:continue
            if model and model not in (row["requested_model"],row["reported_model"]):continue
            if profile and row["profile"]!=profile:continue
            if policy_digest and row["policy_digest"]!=policy_digest:continue
            if evidence and row["evidence_status"]!=evidence:continue
            rows.append(row)
    rows.sort(key=lambda r:(r["created_at"],r["work_item_id"]),reverse=True)
    known=[Decimal(r["cost_usd_equivalent"]) for r in rows if r["cost_usd_equivalent"] is not None]
    filters={**query.model_dump(mode="json"),"category":category,"project":project,"model":model,
             "profile":profile,"policy_digest":policy_digest,"evidence":evidence}
    return {"definition_version":DEFINITION,"as_of":as_of.isoformat(),"filters":filters,"total":len(rows),
        "rows":rows,"models":dict(Counter(r["reported_model"] or r["requested_model"] or "Unknown" for r in rows)),
        "executors":dict(Counter(r["executor"] for r in rows)),
        "outcomes":{"caller_reported_complete":sum(r["caller_reported_complete"] for r in rows),
            "parent_accepted":sum(r["parent_accepted"] for r in rows),"rework":sum(r["rework"] for r in rows),
            "checks_passed":None,"human_accepted":None},
        "latency_seconds":_duration([r["duration_seconds"] for r in rows if r["duration_seconds"] is not None]),
        "cost":{"basis":"USD API-equivalent","known_subtotal":str(sum(known,Decimal(0))),
                "known_workflows":len(known),"unknown_workflows":len(rows)-len(known)},
        "readiness":readiness,
        "scope_note":"Observed Codex/Claude workflows only. Missing costs/checks stay unknown. Receipt-covered subtotals include recorded attempts; parent capture may be incomplete."}

def export_codex_claude(report, format):
    metadata={k:report[k] for k in ("definition_version","as_of","filters","total","scope_note")}
    if format=="jsonl":
        return "\n".join(json.dumps(r,ensure_ascii=False) for r in [{"type":"metadata",**metadata},*report["rows"]])+"\n"
    if format!="csv":raise ValueError("format must be jsonl or csv")
    fields=["record_type","definition_version","as_of","filters","total","work_item_id","category","executor",
        "requested_model","reported_model","project","profile","complexity","route_reason","override",
        "status","evidence_status","duration_seconds","cost_usd_equivalent"]
    stream=io.StringIO(newline="")
    writer=csv.DictWriter(stream,fieldnames=fields,extrasaction="ignore")
    writer.writeheader()
    writer.writerow({"record_type":"metadata",**metadata,"filters":json.dumps(report["filters"],sort_keys=True)})
    for row in report["rows"]:
        safe={k:("'"+v if isinstance(v,str) and v.lstrip().startswith(("=","+","-","@")) else v) for k,v in row.items()}
        writer.writerow({"record_type":"work_item",**safe})
    return stream.getvalue()
