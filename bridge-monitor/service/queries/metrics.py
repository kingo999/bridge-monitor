from collections import defaultdict

import psycopg

from contracts.queries import CohortQuery, MetricSet, MetricValue
from service.queries.cohorts import cutoff, leaf_work_items
from service.queries.guidance import work_guidance, matches_guidance_filters, result_guidance_group


DEFINITION_VERSION = "phase1-v1"


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
            dispatched = {row[0] for row in conn.execute("""SELECT DISTINCT work_item_id FROM events
                WHERE event_type='call.dispatched' AND payload->>'destination'='skynet'
                AND occurred_at<=%s AND work_item_id IS NOT NULL""", (as_of,)) if row[0] in work_ids}
            paid_decisions = {row[0] for row in conn.execute("""SELECT DISTINCT work_item_id FROM delegation_decisions
                WHERE route='paid' AND decided_at<=%s""", (as_of,)) if row[0] in work_ids}
            eligible = {row[0] for row in conn.execute("""SELECT DISTINCT work_item_id FROM delegation_decisions
                WHERE eligibility='eligible' AND decided_at<=%s""", (as_of,)) if row[0] in work_ids}
            not_routed = paid_decisions - dispatched
            result_rows = conn.execute("""SELECT r.result_id, c.work_item_id,c.call_id,r.delivered_at
                FROM results r JOIN calls c ON c.call_id=r.call_id
                WHERE r.delivered_at IS NOT NULL AND r.delivered_at<=%s
                AND EXISTS (SELECT 1 FROM events dispatch
                    WHERE dispatch.call_id=c.call_id AND dispatch.event_type='call.dispatched'
                    AND dispatch.payload->>'destination'='skynet'
                    AND dispatch.occurred_at<=%s)""", (as_of, as_of)).fetchall()
            pack_filter = (query.guidance_group and query.guidance_group not in
                           ("no_guidance", "unknown", "mixed", "not_dispatched"))
            result_ids = {result_id for result_id, work_id, call_id, delivered_at in result_rows
                          if (work_id in all_work_ids and
                              (result_guidance_group(guidance[work_id], call_id, delivered_at) == query.guidance_group
                               if pack_filter else work_id in dispatched))}
            disposition_rows = defaultdict(list)
            for disposition_id, result_id, judgment, supersedes_id in conn.execute("""SELECT
                disposition_id,result_id,judgment,supersedes_id FROM dispositions
                WHERE stage=%s AND recorded_at<=%s""", (query.consumer_stage, as_of)):
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
    final_resolution = sum(bool(resolution[work_id] & {"local_retained", "paid_revision_with_contribution", "paid_replaced"})
                           for work_id in dispatched)
    return MetricSet(definition_version=DEFINITION_VERSION, cohort=query, as_of=as_of,
                     guidance_groups={group: sum(facts["guidance_group"] == group
                         for work_id, facts in guidance.items() if work_id in work_ids)
                         for group in sorted({guidance[work_id]["guidance_group"] for work_id in work_ids})},
                     metrics={
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
                               "final_resolution": _metric(final_resolution, D)})
