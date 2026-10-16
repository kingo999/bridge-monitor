"""Deterministic registered-context selection and current-use rechecks."""

import hashlib
from dataclasses import dataclass

import psycopg


@dataclass(frozen=True)
class GuidanceResolution:
    state: str
    reason: str | None
    manifest: dict
    effective_prompt: str


def _review(conn, pack_version_id: str, example_id: str):
    return conn.execute("SELECT review_id,status,permitted_use,scope FROM example_reviews "
                        "WHERE pack_version_id=%s AND example_id=%s "
                        "ORDER BY reviewed_at DESC,review_id DESC LIMIT 1",
                        (pack_version_id, example_id)).fetchone()


def resolve_guidance(dsn, policy, task, prompt, *, selected_pack_version_id=None,
                     selection_source="configuration"):
    if selection_source not in ("configuration", "claude"):
        raise ValueError("known guidance selection source required")
    manifest = {"version": 1, "state": "no_guidance", "selection_source": selection_source,
                "pack_version_id": None, "pack_sha256": None, "supplied": [], "excluded": []}
    required = task.category in policy.guidance.required_categories
    refs = ([ref for ref in policy.guidance.packs
             if ref.pack_version_id == selected_pack_version_id] if selected_pack_version_id else
            list(policy.guidance.packs))
    chosen = None
    for ref in refs:
        if not ref.enabled:
            why = "guidance_withdrawn"
        elif task.category not in ref.categories:
            why = "category_mismatch"
        elif not set(task.scope).issubset(ref.scopes):
            why = "scope_mismatch"
        else:
            chosen = ref
            break
        manifest["excluded"].append({"pack_version_id": ref.pack_version_id, "reason": why})
    if not chosen:
        return GuidanceResolution("blocked" if required else "ready",
            "required_guidance_missing" if required else None, manifest, prompt)
    with psycopg.connect(dsn) as conn:
        registered = conn.execute("SELECT pack_id,sha256,items FROM guidance_pack_versions "
                                  "WHERE pack_version_id=%s", (chosen.pack_version_id,)).fetchone()
        if not registered or registered[1] != chosen.sha256:
            manifest["excluded"].append({"pack_version_id": chosen.pack_version_id,
                                         "reason": "pack_unregistered_or_hash_mismatch"})
            return GuidanceResolution("blocked", "guidance_snapshot_unavailable", manifest, prompt)
        manifest.update(state="supplied", pack_version_id=chosen.pack_version_id,
                        pack_sha256=chosen.sha256, pack_id=registered[0])
        supplied_bodies = []
        total = 0
        blocked_reason = None
        for item in registered[2]:
            why = None
            review_id = None
            if item["kind"] == "example":
                review = _review(conn, chosen.pack_version_id, item["item_id"])
                if not review:
                    why = "example_unreviewed"
                elif review[1:3] != ("approved", "prompt_example"):
                    why = "example_not_approved_for_prompt"
                elif not set(task.scope).issubset(review[3]):
                    why = "example_scope_mismatch"
                else:
                    review_id = review[0]
            body_row = conn.execute("SELECT raw_bytes,sha256 FROM content_objects WHERE content_id=%s",
                                    (item["content_id"],)).fetchone()
            if not body_row or not body_row[0] or body_row[1] != item["sha256"] or \
                    hashlib.sha256(bytes(body_row[0])).hexdigest() != item["sha256"]:
                why = "guidance_content_hash_mismatch"
            if not why and total + item["byte_length"] > policy.guidance.max_bytes:
                why = "guidance_budget_exceeded"
            if why:
                manifest["excluded"].append({"item_id": item["item_id"], "reason": why})
                if item["required"]:
                    blocked_reason = why
                continue
            body = bytes(body_row[0]).decode("utf-8")
            total += item["byte_length"]
            supplied_bodies.append((item["item_id"], body))
            manifest["supplied"].append({"item_id": item["item_id"], "kind": item["kind"],
                "content_id": item["content_id"], "sha256": item["sha256"],
                "review_id": review_id,
                "source_work_item_id": item.get("source_work_item_id"),
                "source_result_id": item.get("source_result_id")})
    manifest["byte_length"] = total
    if required and not supplied_bodies:
        blocked_reason = blocked_reason or "required_guidance_missing"
    if blocked_reason:
        return GuidanceResolution("blocked", blocked_reason, manifest, prompt)
    suffix = "\n\n[Registered guidance]\n" + "\n".join(
        f"[{item_id}] {body}" for item_id, body in supplied_bodies) if supplied_bodies else ""
    return GuidanceResolution("ready", None, manifest, prompt + suffix)


def check_current_guidance(conn, policy, manifest: dict, task=None) -> tuple[bool, str | None, list[str]]:
    """Recheck only restrictions that may tighten after admission."""
    if manifest.get("state") != "supplied":
        return True, None, []
    pack_id = manifest.get("pack_version_id")
    digest = manifest.get("pack_sha256")
    ref = next((ref for ref in policy.guidance.packs
                if ref.pack_version_id == pack_id and ref.sha256 == digest and ref.enabled), None)
    if ref is None:
        return False, "guidance_withdrawn", []
    if task is not None:
        category = task.category if hasattr(task, "category") else task.get("category")
        scope = task.scope if hasattr(task, "scope") else task.get("scope", [])
        if category not in ref.categories or not set(scope).issubset(ref.scopes):
            return False, "guidance_scope_withdrawn", []
    registered = conn.execute("SELECT sha256 FROM guidance_pack_versions WHERE pack_version_id=%s",
                              (pack_id,)).fetchone()
    if not registered or registered[0] != digest:
        return False, "guidance_snapshot_unavailable", []
    review_ids = []
    for item in manifest.get("supplied", []):
        if item.get("kind") != "example":
            continue
        review = _review(conn, pack_id, item["item_id"])
        if not review or review[1:3] != ("approved", "prompt_example"):
            return False, "example_use_withdrawn", review_ids
        if task is not None:
            scope = task.scope if hasattr(task, "scope") else task.get("scope", [])
            if not set(scope).issubset(review[3]):
                return False, "example_scope_withdrawn", review_ids
        review_ids.append(review[0])
    return True, None, review_ids
