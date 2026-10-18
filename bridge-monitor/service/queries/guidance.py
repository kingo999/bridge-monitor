"""As-of guidance and repair facts for one work-item accounting frontier."""

from collections import defaultdict


def work_guidance(conn, work_ids: set[str], as_of) -> dict[str, dict]:
    if not work_ids:
        return {}
    ids = sorted(work_ids)
    attempts: dict[str, list[dict]] = defaultdict(list)
    rows = conn.execute("""SELECT e.work_item_id,e.call_id,e.occurred_at,d.guidance_manifest
        FROM events e LEFT JOIN calls c ON c.call_id=e.call_id LEFT JOIN LATERAL (
            SELECT guidance_manifest FROM delegation_decisions d
            WHERE d.child_call_id=e.call_id AND d.route='local'
              AND d.execution_mode='production'
              AND d.decided_at<=e.occurred_at
            ORDER BY d.decided_at DESC,d.decision_id DESC LIMIT 1
        ) d ON true
        WHERE e.work_item_id=ANY(%s) AND e.event_type='call.dispatched'
          AND e.payload->>'destination'='skynet' AND e.occurred_at<=%s
          AND COALESCE(c.execution_mode,e.payload->>'execution_mode','production')='production'
        ORDER BY e.occurred_at,e.event_id""", (ids, as_of)).fetchall()
    attempt_groups: dict[str, dict[str, list[tuple]]] = defaultdict(lambda: defaultdict(list))
    for work_id, call_id, dispatched_at, manifest in rows:
        manifest = manifest or {}
        attempts[work_id].append(manifest)
        group = (
            str(manifest["pack_version_id"]) if manifest.get("state") == "supplied" and
            manifest.get("pack_version_id") else
            "no_guidance" if manifest.get("state") == "no_guidance" else "unknown")
        attempt_groups[work_id][call_id].append((dispatched_at, group))
    details: dict[str, dict] = {}
    for work_id in ids:
        manifests = attempts[work_id]
        if not manifests:
            group = "not_dispatched"
        elif any(group == "unknown" for history in attempt_groups[work_id].values()
                 for _, group in history):
            group = "unknown"
        else:
            groups = {group for history in attempt_groups[work_id].values()
                      for _, group in history}
            group = next(iter(groups)) if len(groups) == 1 else "mixed"
        details[work_id] = {"guidance_group": group, "guidance_manifests": manifests,
                            "attempt_groups": attempt_groups[work_id],
                            "failure_categories": [], "repair_efforts": [],
                            "example_reuse_statuses": []}
    pack_ids = sorted({manifest.get("pack_version_id") for manifests in attempts.values()
                       for manifest in manifests if manifest.get("pack_version_id")})
    review_status = {}
    if pack_ids:
        for pack_id, item_id, status in conn.execute("""SELECT pack_version_id,example_id,status
            FROM example_reviews WHERE pack_version_id=ANY(%s) AND reviewed_at<=%s
            ORDER BY reviewed_at,review_id""", (pack_ids, as_of)):
            review_status[(pack_id, item_id)] = status
    for work_id, manifests in attempts.items():
        statuses = {review_status.get((manifest.get("pack_version_id"), item.get("item_id")), "unknown")
                    for manifest in manifests
                    for item in manifest.get("supplied", []) + manifest.get("excluded", [])
                    if item.get("kind") == "example" or
                    str(item.get("reason", "")).startswith("example_")}
        details[work_id]["example_reuse_statuses"] = sorted(statuses)
    for work_id, disposition_id, details_json in conn.execute("""SELECT c.work_item_id,
        d.disposition_id,d.details FROM dispositions d JOIN results r ON r.result_id=d.result_id
        JOIN calls c ON c.call_id=r.call_id
        WHERE c.work_item_id=ANY(%s) AND d.recorded_at<=%s
          AND NOT EXISTS (SELECT 1 FROM dispositions newer
              WHERE newer.supersedes_id=d.disposition_id AND newer.recorded_at<=%s)
          AND c.execution_mode='production'
          AND EXISTS (SELECT 1 FROM events e WHERE e.call_id=c.call_id
              AND e.event_type='call.dispatched' AND e.payload->>'destination'='skynet'
              AND e.occurred_at<=%s)""", (ids, as_of, as_of, as_of)):
        facts = details_json or {}
        if facts.get("failure_category"):
            details[work_id]["failure_categories"].append(facts["failure_category"])
        if facts.get("repair_effort"):
            details[work_id]["repair_efforts"].append(facts["repair_effort"])
    for record in details.values():
        record["failure_categories"] = sorted(set(record["failure_categories"]))
        record["repair_efforts"] = sorted(set(record["repair_efforts"]))
    return details


def matches_guidance_filters(facts: dict, query) -> bool:
    return (not query.guidance_group or facts["guidance_group"] == query.guidance_group) and (
        not query.failure_category or query.failure_category in facts["failure_categories"]) and (
        not query.repair_effort or query.repair_effort in facts["repair_efforts"]) and (
        not query.example_reuse_status or
        query.example_reuse_status in facts["example_reuse_statuses"])


def result_guidance_group(facts: dict, call_id: str, delivered_at) -> str:
    history = facts["attempt_groups"].get(call_id, [])
    prior = [group for dispatched_at, group in history if dispatched_at <= delivered_at]
    return prior[-1] if prior else "unknown"
