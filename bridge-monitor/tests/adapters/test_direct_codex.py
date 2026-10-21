"""Direct task receipts must not invent attribution or turn missing counters into zero."""

import importlib
import hashlib
import json

import pytest


def _stream(*, usage=None, delegated=False):
    frames = [{"type": "thread.started", "thread_id": "session-1"},
              {"type": "turn.started"}]
    if delegated:
        frames.append({"type": "item.completed", "item": {"type": "mcp_tool_call",
            "server": "bridge_monitor", "tool": "route_work"}})
    frames.append({"type": "turn.completed", **({"usage": usage} if usage is not None else {})})
    return "\n".join(json.dumps(f) for f in frames)


def _parse(stream, **kwargs):
    return importlib.import_module("bridge.direct_codex").parse_codex_turn(stream, **kwargs)


def test_receipt_preserves_actual_zero_unknown_and_cache_semantics():
    receipt = _parse(_stream(usage={"input_tokens": 20, "output_tokens": 0,
        "cached_input_tokens": 12, "reasoning_output_tokens": 0}), turn_index=0)
    assert receipt.session_id == "session-1"
    assert receipt.input_tokens == 20
    assert receipt.output_tokens == 0
    assert receipt.cache_read_input_tokens == 12
    assert receipt.cache_creation_input_tokens is None
    assert receipt.reasoning_output_tokens == 0
    assert receipt.scope == "turn"
    assert receipt.provenance == "codex_cli_receipt"


def test_missing_usage_stays_unknown():
    receipt = _parse(_stream(), turn_index=0)
    assert receipt.input_tokens is None and receipt.output_tokens is None


def test_receipt_fingerprint_is_exact_file_bytes_including_bom_and_crlf():
    raw = b'\xef\xbb\xbf' + _stream(usage={"input_tokens": 2}).replace('\n', '\r\n').encode()
    receipt = _parse(raw, turn_index=0)
    assert receipt.source_sha256 == hashlib.sha256(raw).hexdigest()


def test_unknown_mcp_execution_is_not_silently_assumed_non_delegating():
    frames = _stream().splitlines()
    frames.insert(2, json.dumps({"type": "item.completed", "item": {
        "type": "mcp_tool_call", "server": "custom-worker", "tool": "run"}}))
    with pytest.raises(ValueError, match="delegat"):
        _parse('\n'.join(frames), turn_index=0)


@pytest.mark.parametrize("usage", [{"input_tokens": -1}, {"output_tokens": True},
                                  {"output_tokens": 1.5}, {"input_tokens": "10"}])
def test_invalid_usage_is_rejected(usage):
    with pytest.raises(ValueError):
        _parse(_stream(usage=usage), turn_index=0)


def test_bridge_turn_cannot_be_presented_as_standalone_direct_usage():
    with pytest.raises(ValueError, match="delegat"):
        _parse(_stream(usage={"input_tokens": 2}, delegated=True), turn_index=0)


def test_incomplete_ambiguous_and_malformed_receipts_are_rejected():
    for stream in ('{"type":"turn.started"}', _stream() + "\nnot json"):
        with pytest.raises(ValueError):
            _parse(stream, turn_index=0)
    stream = _stream() + '\n{"type":"turn.started"}\n{"type":"turn.completed"}'
    with pytest.raises(ValueError, match="turn"):
        _parse(stream)


def test_metadata_tool_rejects_model_asserted_token_counts():
    module = importlib.import_module("contracts.direct_execution")
    with pytest.raises(ValueError):
        module.DirectExecutionReport.model_validate({
            "record_id": "report", "execution_id": "exec", "session_id": "session",
            "project_root": "C:/project", "summary": "Example", "status": "completed",
            "bypass_reason": "user_instruction", "observed_at": "2026-10-21T10:00:00Z",
            "input_tokens": 1234})
