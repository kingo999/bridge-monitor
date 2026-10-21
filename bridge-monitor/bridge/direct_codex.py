"""Record direct tasks through the existing transactional event ingestion path."""

import hashlib
import json
import uuid

import psycopg

from contracts.direct_execution import CodexTurnReceipt, DirectExecutionReport
from contracts.events import EventEnvelope, EventType
from service.ingest.store import ingest_batch


def parse_codex_turn(stream: str | bytes, *, turn_index: int | None = None) -> CodexTurnReceipt:
    """Read an explicitly selected CLI JSONL receipt, never a transcript directory."""
    raw_bytes = stream if isinstance(stream, bytes) else stream.encode("utf-8")
    if len(raw_bytes) > 16 * 1024 * 1024:
        raise ValueError("receipt exceeds 16 MiB")
    stream = raw_bytes.decode("utf-8-sig")
    frames = [json.loads(line) for line in stream.splitlines() if line.strip()]
    session_id = None
    turns, current = [], None
    for frame in frames:
        if not isinstance(frame, dict):
            raise ValueError("receipt frames must be objects")
        kind = frame.get("type")
        if kind == "thread.started":
            thread = frame.get("thread_id")
            if not isinstance(thread, str) or not thread or (session_id and thread != session_id):
                raise ValueError("receipt must identify one session")
            session_id = thread
        elif kind == "turn.started":
            if current is not None:
                raise ValueError("overlapping turns in receipt")
            current = {"delegated": False}
        elif kind in ("item.started", "item.completed"):
            item = frame.get("item") or {}
            if not isinstance(item, dict):
                raise ValueError("invalid item")
            if current is not None and item.get("type") == "mcp_tool_call":
                # Recording/status tools are management; execution tools mean mixed work.
                server, tool = item.get("server"), item.get("tool")
                management = server == "bridge_monitor" and tool in (
                    "record_direct_task", "get_execution_status", "cancel_work",
                    "acknowledge_claude_result", "list_mcp_resources", "list_mcp_resource_templates")
                known_direct = server in ("node_repl", "cua_repl", "codex_app")
                if (not (management or known_direct) or tool in ("route_work", "delegate_claude", "implement_changes",
                        "review_changes", "consult_architect", "review_implementation", "local_worker")
                        or server in ("gpt-architect", "gpt-architect-v1",
                                                 "claude-implementer-internal")):
                    current["delegated"] = True
            if current is not None and ("collab" in str(item.get("type")) or
                    item.get("type") == "agent_tool_call"):
                current["delegated"] = True
        elif kind in ("turn.completed", "turn.failed"):
            if current is None:
                raise ValueError("turn completion without start")
            current.update(frame=frame)
            turns.append(current)
            current = None
    if current is not None or not session_id or not turns:
        raise ValueError("incomplete receipt")
    if turn_index is None:
        if len(turns) != 1:
            raise ValueError("select a turn_index for a multi-turn receipt")
        turn_index = 0
    if type(turn_index) is not int or not 0 <= turn_index < len(turns):
        raise ValueError("turn_index out of bounds")
    selected = turns[turn_index]
    if selected["delegated"]:
        raise ValueError("delegated/mixed turn cannot be attributed to standalone direct execution")
    raw = selected["frame"].get("usage")
    if raw is not None and not isinstance(raw, dict):
        raise ValueError("invalid usage object")
    raw = raw or {}
    fields = {"input_tokens": "input_tokens", "output_tokens": "output_tokens",
              "cache_read_input_tokens": "cached_input_tokens",
              "cache_creation_input_tokens": "cache_write_input_tokens",
              "reasoning_output_tokens": "reasoning_output_tokens"}
    return CodexTurnReceipt(session_id=session_id,
        source_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        **{target: raw.get(source) for target, source in fields.items()})


def record_direct_task(dsn: str, *, producer_id: str, report: dict,
                       usage: CodexTurnReceipt | None = None,
                       source_turn_id: str | None = None,
                       observation_layer: str = "codex_direct_recorder") -> dict:
    report = DirectExecutionReport.model_validate(report)
    if not producer_id:
        raise PermissionError("authenticated recorder required")
    if usage is not None and (not source_turn_id or usage.session_id != report.session_id):
        raise ValueError("usage requires a matching session identity and stable source_turn_id")
    if usage is None and source_turn_id is not None:
        raise ValueError("source_turn_id requires a usage receipt")
    work_id = "direct:" + str(uuid.uuid5(uuid.NAMESPACE_URL, report.execution_id))
    event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"direct-report:{producer_id}:{report.record_id}"))
    usage_data = usage.model_dump() if usage else None
    if usage_data is not None:
        # Preserve the wire identity of receipts made before desktop support existed.
        for field in ("runtime_model", "runtime_provider"):
            if usage_data[field] is None:
                usage_data.pop(field)
    payload = {"report": report.model_dump(mode="json"),
               "usage": usage_data, "source_turn_id": source_turn_id}
    event = EventEnvelope(event_id=event_id, event_type=EventType.DIRECT_TASK_RECORDED,
        schema_version=1, producer_id=producer_id, producer_incarnation=event_id,
        producer_sequence=1, occurred_at=report.observed_at, origin="observed",
        observation_layer=observation_layer, capture_completeness="partial",
        task_id=work_id, work_item_id=work_id, session_id=report.session_id, payload=payload)
    with psycopg.connect(dsn) as conn, conn.transaction():
        # This lock protects recorder identity/replay, never scheduler capacity.
        conn.execute("SELECT pg_advisory_xact_lock(%s)",
                     (int.from_bytes(hashlib.sha256(report.execution_id.encode()).digest()[:8],
                                     "big", signed=True),))
        ingest_batch(dsn, producer_id, [event], [], connection=conn)
        unresolved = conn.execute("SELECT unresolved_references FROM events WHERE event_id=%s",
                                  (event_id,)).fetchone()[0]
        if unresolved:
            raise ValueError("direct execution identity or receipt conflict: " + ",".join(unresolved))
    return {"execution_id": report.execution_id, "work_item_id": work_id,
            "record_id": report.record_id, "event_id": event_id, "state": "recorded"}
