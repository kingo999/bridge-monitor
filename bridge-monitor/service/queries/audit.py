"""Indexed audit metadata and immutable operator-owned result snapshots."""
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.types.json import Jsonb

from bridge.outbound_audit import Observation
from contracts.queries import CohortQuery
from service.queries.codex_claude import query_codex_claude

LOG = logging.getLogger(__name__)
TTL_SECONDS = 1800
PAGE_SIZE = 50


class SnapshotExpired(ValueError):
    pass

SORTS = {"time": "created_at", "project": "project", "status": "status",
         "destination": "destination", "bytes": "bytes"}
FIELDS = ("view", "id", "source_order", "created_at", "project", "handler", "status",
          "has_usage", "destination", "bytes", "layer", "kind", "process",
          "requested_name", "aliases", "run", "attribution", "ids", "search_text", "data", "error")
SAFE_NETWORK = tuple(Observation.model_fields)


def stamp(ns):
    return datetime.fromtimestamp(ns // 1_000_000_000, timezone.utc) + timedelta(
        microseconds=(ns % 1_000_000_000) // 1000)


def measured(row):
    return any(type(v) is int for v in row.get("usage", {}).values())


def store_entry(db, entry):
    fields = ", ".join(FIELDS)
    values = [Jsonb(entry[k]) if k == "data" else entry.get(k) for k in FIELDS]
    assignments = ", ".join(f"{k}=EXCLUDED.{k}" for k in FIELDS if k not in ("view", "id"))
    db.execute(f"INSERT INTO audit_ui.entries ({fields}) VALUES ({','.join(['%s'] * len(FIELDS))}) "
               f"ON CONFLICT(view,id) DO UPDATE SET {assignments}", values)


def network_entry(identifier, body, version, decision, matched):
    entry = dict(view="network", id=str(identifier), source_order=identifier,
                 has_usage=False, aliases=[], ids=[str(identifier)], search_text="", data={})
    try:
        raw = json.loads(body)
        if not isinstance(raw, dict) or type(raw.get("at_ns")) is not int:
            raise ValueError("missing event timestamp")
        # Validate captured metadata without inventing default evidence.
        raw = Observation.model_validate(raw).model_dump(exclude_unset=True)
        data = {k: raw[k] for k in SAFE_NETWORK if k in raw}
        event_time = stamp(raw["at_ns"])
        peer = raw.get("ip") or raw.get("peer_ip")
        entry.update(
            created_at=event_time, destination=raw.get("hostname") or peer,
            bytes=raw.get("bytes_out") if raw.get("layer", "host") == "host"
            and raw.get("kind") == "transfer" else None,
            layer=raw.get("layer", "host"), kind=raw["kind"],
            process=(raw.get("image") or "").lower(), requested_name=raw.get("hostname"),
            aliases=[name.rstrip('.').lower() for name in raw.get("cached_names", [])], run=raw.get("run"),
            attribution=("application" if raw.get("layer") == "application" and raw.get("run")
                         else raw.get("attribution") or "none"),
            ids=[str(x) for x in (identifier, raw.get("request_id"), raw.get("run"), peer) if x is not None],
        )
        data.update(id=str(identifier), at_ns=str(raw["at_ns"]), policy_version=version,
                    policy_decision=decision, policy_match=matched)
        if raw.get("layer") == "application" and raw.get("bytes_out") is not None:
            data["application_reported_bytes"] = raw["bytes_out"]
            data.pop("bytes_out", None)
        entry["data"] = data
        entry["search_text"] = " ".join(str(raw.get(k) or "") for k in
            ("host", "image", "hostname", "ip", "peer_ip", "run", "task", "request_id", "operation"))
        entry["search_text"] += " " + " ".join(entry["aliases"])
    except (ValueError, TypeError, OverflowError, OSError):
        entry["error"] = "Captured metadata failed validation"
        entry["data"] = {"id": str(identifier), "error": entry["error"]}
    return entry


def execution_search_metadata(db, work_ids, as_of):
    captured = {wid: {"ids": set(), "calls": [], "direct": None, "timeline": []} for wid in work_ids}
    for wid, call in db.execute("SELECT work_item_id,call_id FROM calls WHERE work_item_id=ANY(%s) "
                               "AND created_at<=%s AND execution_mode='production'", (work_ids, as_of)):
        captured[wid]["ids"].add(call)
        captured[wid]["calls"].append(call)
    for wid, execution, report, kind, occurred in db.execute(
        "SELECT work_item_id,model_execution_id,CASE WHEN event_type='direct_task.recorded' "
        "THEN payload->'report' ELSE NULL END,event_type,occurred_at FROM events WHERE work_item_id=ANY(%s) "
        "AND occurred_at<=%s AND received_at<=%s ORDER BY occurred_at,event_id", (work_ids, as_of, as_of)):
        target = captured[wid]
        if execution:
            target["ids"].add(execution)
        # Timeline contains event metadata, never input/output content.
        target["timeline"].append({"type": kind, "at": occurred.isoformat(), "execution": execution})
        if not isinstance(report, dict):
            continue
        for key in ("session_id", "execution_id"):
            if report.get(key):
                target["ids"].add(report[key])
        target["direct"] = {k: report.get(k) for k in
            ("session_id", "execution_id", "summary", "bypass_reason", "reason")}
    return captured


def execution_entry(db, row, as_of, metadata=None):
    """Capture search identifiers at the same evidence cutoff as the report."""
    wid = row["work_item_id"]
    details = dict((metadata or execution_search_metadata(db, [wid], as_of))[wid])
    identifiers = details.pop("ids") | {wid, row.get("task_id") or ""}
    data = {**row, **details}
    search = " ".join(str(row.get(k) or "") for k in
                      ("project", "category", "route_reason", "requested_model", "reported_model"))
    if details["direct"]:
        search += " " + str(details["direct"].get("summary") or "")
    return dict(view="executions", id=wid, created_at=row["created_at"], project=row.get("project"),
                handler=row["executor"], status=row.get("status"), has_usage=measured(row),
                aliases=[], ids=sorted(identifiers - {""}), search_text=search, data=data)


def refresh(dsn, *, batch=1000):
    """One bounded indexing step. Claims/checkpoints commit with derived rows."""
    with psycopg.connect(dsn) as db, db.transaction():
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        db.execute("SET LOCAL statement_timeout='30s'")
        if not db.execute("SELECT to_regclass('audit_ui.entries')").fetchone()[0]:
            return False
        # Only one projection publisher; entirely separate from importer lock.
        if not db.execute("SELECT pg_try_advisory_xact_lock(673244013,15)").fetchone()[0]:
            return False
        high = db.execute("SELECT network_high FROM audit_ui.state WHERE singleton FOR UPDATE").fetchone()[0]
        source = db.execute("SELECT last_id FROM outbound_audit.source").fetchone()
        if source:
            for row in db.execute("SELECT id,evidence,version,decision,matched "
                                  "FROM outbound_audit.observations WHERE id>%s AND id<=%s "
                                  "ORDER BY id LIMIT %s", (high, source[0], batch)).fetchall():
                entry = network_entry(*row)
                if entry.get("run") and entry.get("attribution") != "none":
                    linked = db.execute("SELECT call_id,work_item_id FROM calls WHERE call_id=%s "
                                        "AND execution_mode='production'", (entry["run"],)).fetchone()
                    if linked:
                        entry["data"]["linked_call"] = linked[0]
                        entry["data"]["linked_work_item"] = linked[1]
                store_entry(db, entry)
                high = row[0]
            db.execute("UPDATE audit_ui.state SET network_high=%s,updated_at=now()", (high,))
    return refresh_executions(dsn)


def refresh_executions(dsn):
    with psycopg.connect(dsn) as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        db.execute("SET LOCAL statement_timeout='15s'")
        if not db.execute("SELECT pg_try_advisory_xact_lock(673244013,16)").fetchone()[0]:
            return False
        claims = db.execute("SELECT invalidation_id,work_item_id FROM audit_ui.dirty WHERE ready_at<=now() "
                            "ORDER BY invalidation_id LIMIT 200").fetchall()
        claims_ids = [r[0] for r in claims]
        dirty = sorted({r[1] for r in claims})
        if '*' in dirty:
            db.execute("INSERT INTO audit_ui.dirty(work_item_id) SELECT work_item_id FROM work_items "
                       "UNION SELECT work_item_id FROM cc_decisions")
            dirty.remove('*')
        if dirty:
            report = query_codex_claude(dsn, CohortQuery(origin="all", consumer_stage="gpt"),
                                       connection=db, work_item_ids=dirty)
            db.execute("DELETE FROM audit_ui.entries WHERE view='executions' AND id=ANY(%s)", (dirty,))
            metadata = execution_search_metadata(db, [r["work_item_id"] for r in report["rows"]], report["as_of"])
            for row in report["rows"]:
                store_entry(db, execution_entry(db, row, report["as_of"], metadata))
            db.execute("UPDATE audit_ui.state SET execution_version=execution_version+1")
        db.execute("DELETE FROM audit_ui.dirty WHERE invalidation_id=ANY(%s)", (claims_ids,))
        # Future-dated evidence is reconsidered when its cutoff is reached.
        future = db.execute("""
            WITH clock_values AS (
              SELECT work_item_id,greatest(occurred_at,received_at) AS at FROM events WHERE work_item_id=ANY(%s)
              UNION ALL SELECT work_item_id,created_at FROM work_items WHERE work_item_id=ANY(%s)
              UNION ALL SELECT work_item_id,created_at FROM calls WHERE work_item_id=ANY(%s)
              UNION ALL SELECT work_item_id,recorded_at FROM cc_decisions WHERE work_item_id=ANY(%s)
              UNION ALL SELECT work_item_id,recorded_at FROM work_resolutions WHERE work_item_id=ANY(%s)
              UNION ALL SELECT h.work_item_id,greatest(r.observed_at,r.recorded_at)
                FROM cc_receipts r JOIN cc_reservations h USING(call_id) WHERE h.work_item_id=ANY(%s)
              UNION ALL SELECT c.work_item_id,d.recorded_at FROM dispositions d
                JOIN results r USING(result_id) JOIN calls c USING(call_id) WHERE c.work_item_id=ANY(%s)
            ) SELECT work_item_id,min(at) FROM clock_values WHERE at>now() GROUP BY work_item_id
            """, (dirty,) * 7).fetchall()
        for wid, ready in future:
            db.execute("INSERT INTO audit_ui.dirty(work_item_id,ready_at) VALUES(%s,%s)", (wid, ready))

        db.execute("DELETE FROM audit_ui.snapshots WHERE expires_at<now()")
    return True


def _network_status(db):
    source = db.execute("SELECT last_id,synced_at FROM outbound_audit.source").fetchone()
    high, revision = db.execute("SELECT network_high,execution_version FROM audit_ui.state").fetchone()
    count, invalid, health = db.execute(
        "SELECT count(*),count(*) FILTER(WHERE error IS NOT NULL),"
        "max(created_at) FILTER(WHERE layer='collector' AND kind='health') "
        "FROM audit_ui.entries WHERE view='network'").fetchone()
    pending = db.execute("SELECT count(DISTINCT work_item_id) FROM audit_ui.dirty").fetchone()[0]
    return dict(source_high=source[0] if source else None, indexed_high=high,
                execution_version=revision,
                indexed=count, invalid=invalid, execution_pending=pending,
                synced_at=source[1].isoformat() if source and source[1] else None,
                health_processed_at=health.isoformat() if health else None,
                complete=bool(source and high == source[0]), scope="One configured local journal / host")


def network_status(dsn):
    with psycopg.connect(dsn) as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        return _network_status(db)


def predicate(filters, *, alias=""):
    conditions, params = ["view=%s"], [filters.get("view", "executions")]
    if params[0] not in ("executions", "network"):
        raise ValueError("unknown audit view")
    query = str(filters.get("q", "")).strip()
    if len(query) > 200:
        raise ValueError("search is limited to 200 characters")
    mode = filters.get("mode", "words")
    prefix = query.lower().replace(chr(92), chr(92) * 2).replace("%", chr(92) + "%").replace("_", chr(92) + "_") + "%"
    if query:
        if mode == "exact":
            conditions.append("ids @> ARRAY[%s]"); params.append(query)
        elif mode == "words":
            conditions.append("(search @@ plainto_tsquery('simple',%s) OR ids @> ARRAY[%s])")
            params.extend([query, query])
        elif mode == "process":
            conditions.append("process LIKE %s"); params.append(prefix)
        elif mode == "requested":
            conditions.append("requested_name LIKE %s"); params.append(prefix)
        elif mode == "alias":
            conditions.append("aliases @> ARRAY[%s]"); params.append(query.lower())
        else:
            raise ValueError("unknown search mode")
    for key in ("project", "handler", "status", "layer", "kind", "run", "attribution", "destination"):
        if filters.get(key):
            conditions.append(key + "=%s"); params.append(filters[key])
    if filters.get("port"):
        if not str(filters["port"]).isdigit() or not 0 <= int(filters["port"]) <= 65535:
            raise ValueError("invalid port")
        conditions.append("data->>'port'=%s"); params.append(str(filters["port"]))
    if filters.get("usage") in ("yes", "no"):
        conditions.append("has_usage=%s"); params.append(filters["usage"] == "yes")
    scope = filters.get("range", "recent")
    if scope == "recent":
        conditions.append("created_at>=now()-interval '24 hours'")
    elif scope == "custom":
        for key, op in (("from", ">="), ("until", "<")):
            if filters.get(key):
                value = datetime.fromisoformat(filters[key].replace("Z", "+00:00"))
                if value.tzinfo is None:
                    raise ValueError("range must include a timezone")
                conditions.append("created_at" + op + "%s"); params.append(value)
    elif scope != "all":
        raise ValueError("unknown range")
    return " AND ".join(conditions), params


def _new_snapshot(db, owner, filters, coverage):
    db.execute("DELETE FROM audit_ui.snapshots WHERE expires_at<now()")
    # Bound retained snapshot storage per session. An evicted token reports expiry.
    db.execute("DELETE FROM audit_ui.snapshots WHERE token IN "
               "(SELECT token FROM audit_ui.snapshots WHERE owner=%s AND NOT activated ORDER BY expires_at DESC OFFSET 4)", (owner,))
    if db.execute("SELECT count(*) FROM audit_ui.snapshots WHERE owner=%s AND activated", (owner,)).fetchone()[0] >= 100:
        raise ValueError("Too many active audit searches; allow earlier searches to expire")
    token = secrets.token_urlsafe(32)
    db.execute("INSERT INTO audit_ui.snapshots(token,owner,expires_at,view,filters,coverage) VALUES(%s,%s,now()+interval '30 minutes',%s,%s,%s)",
               (token, owner, filters.get("view", "executions"), Jsonb(filters), Jsonb(coverage)))
    return token


def start_search(dsn, owner, filters):
    allowed = {"view", "range", "q", "mode", "sort", "direction", "project", "handler", "status",
               "usage", "layer", "kind", "run", "attribution", "destination", "port", "from", "until", "cohort_token"}
    if (set(filters) - allowed or len(filters) > 22 or
            any(not isinstance(v, str) or len(v) > 500 for v in filters.values())):
        raise ValueError("Audit filters must be supported text fields of at most 500 characters")
    sort = SORTS.get(filters.get("sort", "time"))
    if not sort or filters.get("direction", "desc") not in ("asc", "desc"):
        raise ValueError("unknown sort")
    direction = filters.get("direction", "desc")
    where, params = predicate(filters)
    with psycopg.connect(dsn) as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        db.execute("SET LOCAL statement_timeout='15s'")
        coverage = _network_status(db)
        token = _new_snapshot(db, owner, filters, coverage)
        if filters.get("cohort_token"):
            return refine_cohort(db, owner, filters, token, sort, direction)
        db.execute(f"INSERT INTO audit_ui.snapshot_rows "
                   f"SELECT %s,row_number() OVER(ORDER BY {sort} {direction} NULLS LAST,id),"
                   "jsonb_build_object('id',id,'time',created_at,'project',project,'handler',handler,"
                   "'status',status,'usage',has_usage,'destination',destination,'bytes',bytes,"
                   "'layer',layer,'kind',kind,'process',process,'run',run,'attribution',attribution,"
                   "'error',error,'data',data || CASE WHEN view='network' AND attribution<>'none' THEN "
                   "coalesce((SELECT jsonb_build_object('linked_call',c.call_id,'linked_work_item',c.work_item_id) "
                   "FROM calls c WHERE c.call_id=run AND c.execution_mode='production'), '{}'::jsonb) "
                   "ELSE '{}'::jsonb END,'ids',ids,'search_text',search_text) FROM audit_ui.entries WHERE " + where,
                   [token, *params])
        total = db.execute("SELECT count(*) FROM audit_ui.snapshot_rows WHERE token=%s", (token,)).fetchone()[0]
    return dict(token=token, total=total, coverage=coverage, filters=filters)


def refine_cohort(db, owner, filters, token, sort, direction):
    source = db.execute("SELECT coverage FROM audit_ui.snapshots WHERE token=%s AND owner=%s "
                        "AND expires_at>now()", (filters["cohort_token"], owner)).fetchone()
    if source is None:
        raise ValueError("Overview cohort expired; return to the overview to regenerate it")
    db.execute("UPDATE audit_ui.snapshots SET activated=true WHERE token=%s", (filters["cohort_token"],))
    # Immutable historical values enter the same indexed-query vocabulary.
    db.execute("CREATE TEMP TABLE cohort_entries (LIKE audit_ui.entries INCLUDING GENERATED INCLUDING DEFAULTS) ON COMMIT DROP")
    db.execute("INSERT INTO cohort_entries(view,id,created_at,project,handler,status,has_usage,ids,search_text,data) "
               "SELECT 'executions',entry->>'id',(entry->>'time')::timestamptz,entry->>'project',"
               "entry->>'handler',entry->>'status',(entry->>'has_usage')::boolean,"
               "ARRAY(SELECT jsonb_array_elements_text(entry->'ids')),entry->>'search_text',entry->'data' "
               "FROM audit_ui.snapshot_rows WHERE token=%s", (filters["cohort_token"],))
    where, args = predicate({**filters, "view": "executions", "range": filters.get("range", "all")})
    db.execute(f"INSERT INTO audit_ui.snapshot_rows SELECT %s,row_number() OVER(ORDER BY {sort} "
               f"{direction} NULLS LAST,id),jsonb_build_object('id',id,'time',created_at,'project',project,"
               "'handler',handler,'status',status,'usage',has_usage,'has_usage',has_usage,'ids',ids,"
               "'search_text',search_text,'data',data) FROM cohort_entries WHERE " + where, [token, *args])
    db.execute("UPDATE audit_ui.snapshots SET coverage=%s WHERE token=%s", (Jsonb(source[0]), token))
    total = db.execute("SELECT count(*) FROM audit_ui.snapshot_rows WHERE token=%s", (token,)).fetchone()[0]
    return dict(token=token, total=total, coverage=source[0], filters=filters)


def cohort_snapshot(db, owner, report):
    """Persist complete historical search values while the report transaction is still open."""
    import hashlib
    metadata = execution_search_metadata(db, [r["work_item_id"] for r in report["rows"]], report["as_of"])
    captured = [execution_entry(db, row, report["as_of"], metadata) for row in report["rows"]]
    signature = hashlib.sha256(json.dumps([report["filters"], captured], sort_keys=True, default=str).encode()).hexdigest()
    previous = db.execute("SELECT token FROM audit_ui.snapshots WHERE owner=%s "
                          "AND filters->>'signature'=%s AND expires_at>now()+interval '1 minute' LIMIT 1",
                          (owner, signature)).fetchone()
    if previous:
        return previous[0]
    token = _new_snapshot(db, owner, {"view": "executions", "range": "all", "cohort": report["filters"], "signature": signature,
                                    "as_of": report["as_of"]}, _network_status(db))
    records = [(token, rank, Jsonb({**row, "id": row["work_item_id"], "time": row["created_at"],
                 "handler": row["executor"], "has_usage": measured(row), "ids": entry["ids"],
                 "search_text": entry["search_text"], "data": entry["data"]}))
               for rank, (row, entry) in enumerate(zip(report["rows"], captured), 1)]
    with db.cursor() as cursor:
        cursor.executemany("INSERT INTO audit_ui.snapshot_rows VALUES(%s,%s,%s)", records)
    return token


def page_snapshot(dsn, owner, token, *, after=0, limit=PAGE_SIZE, q="", usage=""):
    if not 0 <= after or not 1 <= limit <= 100 or len(q) > 200:
        raise ValueError("invalid page")
    with psycopg.connect(dsn) as db:
        db.execute("SET LOCAL statement_timeout='15s'")
        row = db.execute("SELECT filters,coverage FROM audit_ui.snapshots WHERE token=%s AND owner=%s "
                         "AND expires_at>now()", (token, owner)).fetchone()
        if row is None:
            raise SnapshotExpired("Audit snapshot expired or unavailable; refresh the search")
        db.execute("UPDATE audit_ui.snapshots SET activated=true WHERE token=%s", (token,))
        conditions = "token=%s"
        args = [token]
        if q:
            conditions += " AND (entry->'ids' ? %s OR to_tsvector('simple',coalesce(entry->>'search_text','')) "
            conditions += "@@ plainto_tsquery('simple',%s))"
            args.extend([q, q])
        if usage in ("yes", "no"):
            conditions += " AND coalesce((entry->>'has_usage')::boolean,(entry->>'usage')::boolean,false)=%s"
            args.append(usage == "yes")
        total = db.execute("SELECT count(*) FROM audit_ui.snapshot_rows WHERE " + conditions, args).fetchone()[0]
        rows = db.execute("SELECT rank,entry FROM audit_ui.snapshot_rows WHERE " + conditions +
                          " AND rank>%s ORDER BY rank LIMIT %s", [*args, after, limit + 1]).fetchall()
    return dict(rows=[r[1] for r in rows[:limit]], total=total,
                next=rows[limit - 1][0] if len(rows) > limit else None,
                filters=row[0], coverage=row[1], token=token)
