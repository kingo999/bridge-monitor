"""Append-only authenticated human decisions about example reuse."""

import uuid
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from bridge.runtime.admission import ARBITRATION_LOCK
from bridge.runtime.reconciliation import _authenticate


@dataclass(frozen=True)
class ExampleReviewReceipt:
    review_id: str
    status: str
    permitted_use: str
    exposure: str


def _linked_results(conn, source_work_item_id: str | None) -> set[str]:
    if not source_work_item_id:
        return set()
    rows = conn.execute("WITH RECURSIVE family(result_id) AS ("
        "SELECT r.result_id FROM results r JOIN calls c ON c.call_id=r.call_id "
        "WHERE c.work_item_id=%s UNION "
        "SELECT CASE WHEN l.from_result_id=f.result_id THEN l.to_result_id "
        "ELSE l.from_result_id END FROM result_links l JOIN family f "
        "ON l.from_result_id=f.result_id OR l.to_result_id=f.result_id) "
        "SELECT result_id FROM family", (source_work_item_id,)).fetchall()
    return {row[0] for row in rows}


def _recorded_exposure(conn, pack_version_id: str, example_id: str,
                       source_work_item_id: str | None) -> bool:
    linked = _linked_results(conn, source_work_item_id)
    manifests = conn.execute("""SELECT d.guidance_manifest FROM delegation_decisions d
        WHERE d.route='local' AND d.guidance_manifest->>'state'='supplied'
        AND EXISTS (SELECT 1 FROM events e WHERE e.call_id=d.child_call_id
            AND e.event_type='call.dispatched' AND e.payload->>'destination'='skynet'
            AND e.occurred_at>=d.decided_at)""").fetchall()
    for (manifest,) in manifests:
        for item in manifest.get("supplied", []):
            if (manifest.get("pack_version_id") == pack_version_id and
                    item.get("item_id") == example_id):
                return True
            if item.get("source_result_id") in linked or (
                    source_work_item_id and item.get("source_work_item_id") == source_work_item_id):
                return True
    return False


def record_example_review(dsn, pack_version_id, example_id, *, reviewer_id, auth_token,
                          status, permitted_use, exposure, scope, evidence, supersedes_id=None):
    _authenticate("operator", reviewer_id, auth_token)
    if status not in ("candidate", "approved", "rejected") or permitted_use not in (
            "prompt_example", "training_candidate", "evaluation_only") or exposure not in (
            "unseen", "exposed", "unknown"):
        raise ValueError("recognized review status, use and exposure required")
    if not isinstance(scope, list) or not isinstance(evidence, dict):
        raise ValueError("review scope and evidence must be structured")
    validation = evidence.get("validation")
    if status == "approved" and (not scope or not isinstance(validation, dict) or
                                  not validation.get("check") or
                                  validation.get("outcome") != "pass"):
        raise ValueError("approval needs scope and independent validation evidence")
    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (ARBITRATION_LOCK,))
        pack = conn.execute("SELECT items FROM guidance_pack_versions WHERE pack_version_id=%s",
                            (pack_version_id,)).fetchone()
        item = next((entry for entry in pack[0] if entry["item_id"] == example_id), None) if pack else None
        if not item or item["kind"] != "example":
            raise ValueError("registered example item required")
        prior = conn.execute("SELECT review_id FROM example_reviews WHERE pack_version_id=%s "
                             "AND example_id=%s ORDER BY reviewed_at DESC,review_id DESC LIMIT 1",
                             (pack_version_id, example_id)).fetchone()
        if (prior and supersedes_id != prior[0]) or (not prior and supersedes_id):
            raise ValueError("new review must supersede the effective review")
        if status == "approved" and (not item.get("source_work_item_id") or
                                     not item.get("source_result_id")):
            raise ValueError("approved reuse needs source lineage")
        if item.get("source_result_id"):
            owner = conn.execute("SELECT c.work_item_id FROM results r "
                "JOIN calls c ON c.call_id=r.call_id WHERE r.result_id=%s",
                (item["source_result_id"],)).fetchone()
            if not owner or owner[0] != item.get("source_work_item_id"):
                raise ValueError("source result must belong to declared work item")
        recorded_exposure = _recorded_exposure(conn, pack_version_id, example_id,
                                               item.get("source_work_item_id"))
        if permitted_use == "evaluation_only" and exposure == "unseen" and recorded_exposure:
            raise ValueError("exposed recorded guidance prevents unseen evaluation")
        review_id = str(uuid.uuid4())
        conn.execute("INSERT INTO example_reviews(review_id,pack_version_id,example_id,"
                     "content_id,source_work_item_id,source_result_id,reviewer_id,status,"
                     "permitted_use,exposure,scope,evidence,supersedes_id) "
                     "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                     (review_id, pack_version_id, example_id, item["content_id"],
                      item.get("source_work_item_id"), item.get("source_result_id"), reviewer_id,
                      status, permitted_use, exposure, Jsonb(scope), Jsonb(evidence), supersedes_id))
    return ExampleReviewReceipt(review_id, status, permitted_use, exposure)
