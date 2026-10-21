from collections import defaultdict

import psycopg

from contracts.queries import CohortQuery, MetricSet, MetricValue
from service.queries.cohorts import cutoff, leaf_work_items
from service.queries.guidance import work_guidance, matches_guidance_filters, result_guidance_group


DEFINITION_VERSION = "phase1-v4"


def _metric(numerator: int, denominator: int) -> MetricValue:
    return MetricValue(numerator=numerator, denominator=denominator,
                       percent=round(100 * numerator / denominator, 1) if denominator else None)


def query_metrics(dsn: str, query: CohortQuery) -> MetricSet:
    as_of = cutoff(query)
    with psycopg.connect(dsn) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            all_work_ids = set(leaf_work_items(conn, query, as_of))
            guidance = work_guidance(conn, all_work_ids, as_of)
            work_ids = {work_id for work_id in all_work_ids
                        if matches_guidance_filters(guidance[work_id], query)}
            from service.queries.direct_execution import execution_metadata
            execution_facts = execution_metadata(conn, work_ids, as_of)
            total_work = len(work_ids)
            direct_count = sum(f["execution_path"] == "direct_codex" for f in execution_facts.values())
            bridge_count = sum(f["execution_path"] == "bridge" for f in execution_facts.values())
            # Direct observations never become pending Skynet delegations or skew its ratios.
            work_ids = {identifier for identifier in work_ids
                        if execution_facts[identifier]["execution_path"] != "direct_codex"}
            dispatched = {row[0] for row in conn.execute("""SELECT DISTINCT e.work_item_id
                FROM events e LEFT JOIN calls c ON c.call_id=e.call_id
                WHERE e.event_type='call.dispatched' AND e.payload->>'destination'='skynet'
                AND COALESCE(c.execution_mode,e.payload->>'execution_mode','production')='production'
                AND e.occurred_at<=%s AND e.work_item_id IS NOT NULL""", (as_of,)) if row[0] in work_ids}
            paid_decisions = {row[0] for row in conn.execute("""SELECT DISTINCT work_item_id FROM delegation_decisions
                WHERE route='paid' AND execution_mode='production' AND decided_at<=%s""", (as_of,)) if row[0] in work_ids}
            eligible = {row[0] for row in conn.execute("""SELECT DISTINCT work_item_id FROM delegation_decisions
                WHERE eligibility='eligible' AND execution_mode='production' AND decided_at<=%s""", (as_of,)) if row[0] in work_ids}
            decided = {row[0] for row in conn.execute("""SELECT DISTINCT work_item_id FROM delegation_decisions
                WHERE execution_mode='production' AND decided_at<=%s""", (as_of,)) if row[0] in work_ids}
            not_routed = paid_decisions - dispatched
            if query.consumer_stage == "gpt":
                result_rows = conn.execute("""SELECT r.result_id,c.work_item_id,c.call_id,
                    r.delivered_at FROM results r JOIN calls c ON c.call_id=r.call_id
                    JOIN work_items w ON w.work_item_id=c.work_item_id
                    WHERE c.execution_mode='production' AND r.delivered_at IS NOT NULL AND r.delivered_at<=%s
                    AND r.delivered_content_id IS NOT NULL
                    AND c.parent_call_id='gpt:' || w.task_id""", (as_of,)).fetchall()
            else:
                result_rows = conn.execute("""SELECT r.result_id, c.work_item_id,c.call_id,r.delivered_at
                    FROM results r JOIN calls c ON c.call_id=r.call_id
                    WHERE c.execution_mode='production' AND r.delivered_at IS NOT NULL AND r.delivered_at<=%s
                    AND EXISTS (SELECT 1 FROM events dispatch
                        WHERE dispatch.call_id=c.call_id AND dispatch.event_type='call.dispatched'
                        AND dispatch.payload->>'destination'='skynet'
                        AND dispatch.occurred_at<=%s)""", (as_of, as_of)).fetchall()
            pack_filter = (query.guidance_group and query.guidance_group not in
                           ("no_guidance", "unknown", "mixed", "not_dispatched"))
            if query.consumer_stage == "gpt":
                result_ids = {result_id for result_id, work_id, _, _ in result_rows
                              if work_id in work_ids and
                              (not pack_filter or
                               guidance[work_id]["guidance_group"] == query.guidance_group)}
            else:
                result_ids = {result_id for result_id, work_id, call_id, delivered_at in result_rows
                              if work_id in all_work_ids and
                              (result_guidance_group(guidance[work_id], call_id, delivered_at)
                               == query.guidance_group if pack_filter else work_id in dispatched)}
            disposition_rows = defaultdict(list)
            if query.consumer_stage == "gpt":
                dispositions = conn.execute("""SELECT d.disposition_id,d.result_id,
                    d.judgment,d.supersedes_id FROM dispositions d
                    JOIN results r ON r.result_id=d.result_id
                    JOIN calls c ON c.call_id=r.call_id
                    JOIN work_items w ON w.work_item_id=c.work_item_id
                    JOIN content_objects content ON content.content_id=r.delivered_content_id
                    LEFT JOIN runtime_disposition_obligations o ON o.result_id=r.result_id
                      AND o.consumer_call_id=c.parent_call_id AND o.stage='gpt'
                      AND o.part_id IS NULL
                    WHERE d.stage='gpt' AND d.recorded_at<=%s
                      AND d.actor_id='gpt-parent:' || c.parent_call_id
                      AND d.input_sha256=content.sha256
                      AND (w.origin<>'observed' OR
                           (o.content_sha256=content.sha256 AND
                            d.details->>'provenance'='parent_mcp_report'))""",
                    (as_of,))
            else:
                dispositions = conn.execute("""SELECT disposition_id,result_id,judgment,
                    supersedes_id FROM dispositions WHERE stage=%s AND recorded_at<=%s""",
                    (query.consumer_stage, as_of))
            for disposition_id, result_id, judgment, supersedes_id in dispositions:
                if result_id in result_ids:
                    disposition_rows[result_id].append((disposition_id, judgment, supersedes_id))
            judgments = {}
            for result_id, rows in disposition_rows.items():
                superseded = {parent for _, _, parent in rows if parent}
                effective = [judgment for identifier, judgment, _ in rows if identifier not in superseded]
                if len(effective) == 1:
                    judgments[result_id] = effective[0]
            resolution = defaultdict(set)
            for work_id, outcome in conn.execute("""SELECT work_item_id,outcome FROM work_resolutions
                WHERE recorded_at<=%s""", (as_of,)):
                if work_id in work_ids:
                    resolution[work_id].add(outcome)
    W = len(work_ids)
    D = len(dispatched)
    R = len(result_ids)
    K = len(judgments)
    A = sum(value == "accepted" for value in judgments.values())
    C = sum(value in ("accepted", "revised") for value in judgments.values())
    takeover = sum("paid_replaced" in resolution[work_id] for work_id in dispatched)
    local_resolution = sum("local_retained" in resolution[work_id] for work_id in work_ids)
    resolved_without_delegation_record = sum(bool(resolution[work_id]) and work_id not in decided
        and work_id not in dispatched for work_id in work_ids)
    final_resolution = sum(bool(resolution[work_id] & {"local_retained", "paid_revision_with_contribution", "paid_replaced"})
                           for work_id in dispatched)
    return MetricSet(definition_version=DEFINITION_VERSION, cohort=query, as_of=as_of,
                     guidance_groups={group: sum(facts["guidance_group"] == group
                         for work_id, facts in guidance.items() if work_id in work_ids)
                         for group in sorted({guidance[work_id]["guidance_group"] for work_id in work_ids})},
                     metrics={
                         "direct_execution": _metric(direct_count, total_work),
                         "bridge_execution": _metric(bridge_count, total_work),
                         "unknown_execution": _metric(total_work - direct_count - bridge_count, total_work),
                         "routed": _metric(D, W),
                         "not_routed": _metric(len(not_routed), W),
                         "pending": _metric(len(work_ids - dispatched - not_routed), W),
                         "eligible_delegation": _metric(len(dispatched & eligible), len(eligible)),
                         "confirmed_acceptance": _metric(A, R),
                         "reported_acceptance": _metric(A, K),
                         "contribution": _metric(C, R),
                         "takeover": _metric(takeover, D),
                         "local_resolution": _metric(local_resolution, W),
                     },
                     coverage={"disposition": _metric(K, R),
                               "eligibility": _metric(len(eligible), W),
                               "resolved_without_delegation_record": _metric(
                                   resolved_without_delegation_record, W),
                               "final_resolution": _metric(final_resolution, D)})
