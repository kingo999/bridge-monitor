"""Immutable registered guidance snapshots and exact content hashes."""

import hashlib
import json
import uuid
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from bridge.runtime.reconciliation import _authenticate


@dataclass(frozen=True)
class RegisteredPack:
    pack_version_id: str
    sha256: str


def register_pack(dsn, *, pack_id, version_label, items, actor_id, auth_token):
    _authenticate("operator", actor_id, auth_token)
    if not pack_id or not version_label or not items:
        raise ValueError("pack identity and items are required")
    material = []
    bodies = []
    seen = set()
    for item in items:
        item_id = item.get("item_id")
        kind = item.get("kind")
        body = item.get("body")
        if not item_id or item_id in seen or kind not in ("rule", "example") or not isinstance(body, (bytes, bytearray)) or not body:
            raise ValueError("unique item identity, type and nonempty bytes required")
        seen.add(item_id)
        copied = bytes(body)
        try:
            copied.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("guidance text must be UTF-8") from exc
        digest = hashlib.sha256(copied).hexdigest()
        material.append({"item_id": item_id, "kind": kind, "required": bool(item.get("required", False)),
                         "sha256": digest, "byte_length": len(copied),
                         "source_work_item_id": item.get("source_work_item_id"),
                         "source_result_id": item.get("source_result_id")})
        bodies.append(copied)
    canonical = json.dumps({"pack_id": pack_id, "version_label": version_label,
                            "items": material}, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    version_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"bridge-monitor:guidance:{pack_id}:{version_label}:{digest}"))
    stored_items = []
    for item in material:
        content_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
            f"bridge-monitor:guidance-content:{version_id}:{item['item_id']}"))
        stored_items.append({**item, "content_id": content_id})
    with psycopg.connect(dsn) as conn, conn.transaction():
        for item in stored_items:
            source_work = item.get("source_work_item_id")
            source_result = item.get("source_result_id")
            if bool(source_work) != bool(source_result):
                raise ValueError("example source work item and result must be paired")
            if source_work:
                owner = conn.execute("SELECT c.work_item_id FROM results r "
                    "JOIN calls c ON c.call_id=r.call_id WHERE r.result_id=%s",
                    (source_result,)).fetchone()
                if not owner or owner[0] != source_work:
                    raise ValueError("source result must belong to declared work item")
        prior = conn.execute("SELECT pack_version_id,sha256 FROM guidance_pack_versions "
                             "WHERE pack_id=%s AND version_label=%s",
                             (pack_id, version_label)).fetchone()
        if prior:
            if prior != (version_id, digest):
                raise ValueError("registered pack version is immutable")
            return RegisteredPack(*prior)
        for item, body in zip(stored_items, bodies):
            conn.execute("INSERT INTO content_objects(content_id,raw_bytes,byte_length,sha256,"
                         "role,direction,content_type,encoding,origin,capture_layer,completeness,"
                         "message_id,block_order) VALUES(%s,%s,%s,%s,'guidance','in','text/plain',"
                         "'utf-8','imported','registry','complete',NULL,0) "
                         "ON CONFLICT(content_id) DO NOTHING",
                         (item["content_id"], body, len(body), item["sha256"]))
            saved = conn.execute("SELECT raw_bytes,sha256 FROM content_objects WHERE content_id=%s",
                                 (item["content_id"],)).fetchone()
            if not saved or bytes(saved[0]) != body or saved[1] != item["sha256"]:
                raise ValueError("registered content identity conflict")
        conn.execute("INSERT INTO guidance_pack_versions(pack_version_id,pack_id,version_label,"
                     "sha256,items,registered_by) VALUES(%s,%s,%s,%s,%s,%s)",
                     (version_id, pack_id, version_label, digest, Jsonb(stored_items), actor_id))
    return RegisteredPack(version_id, digest)
