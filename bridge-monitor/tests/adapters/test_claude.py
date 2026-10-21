import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from contracts.events import EventType
from contracts.work import DispatchLease, TaskPacket
from contracts.outcomes import DispositionRecord
from bridge.adapters.claude_cli import ClaudeCLIAdapter, SubprocessRunner, build_claude_command


class Sink:
    def __init__(self): self.events = []
    def emit(self, kind, **kwargs):
        self.events.append((kind, kwargs))
        return True


class Runner:
    def __init__(self, frames, returncode=0, fail=False):
        self.frames = frames
        self.returncode = returncode
        self.fail = fail
        self.commands = []

    def stream(self, command, prompt):
        self.commands.append((command, prompt))
        for frame in self.frames:
            yield json.dumps(frame)
        if self.fail:
            raise ConnectionError("CLI stream dropped")


def _lease():
    return DispatchLease(lease_id="lease-claude", call_id="call-claude",
                         owner_incarnation="boot-1", fencing_token=1, policy_version="v1")


def _task():
    return TaskPacket(task_id="task-1", work_item_id="work-1", category="analysis",
                      scope=["source-read"], token_budget=1000, call_budget=2)


def test_command_restricts_worker_to_named_child_capability(tmp_path):
    command = build_claude_command("claude", "opus", tmp_path / "mcp.json")
    assert "--restricted" in command
    assert "--strict-mcp-config" in command
    assert "--include-partial-messages" in command
    assert "--verbose" in command
    assert "mcp__bridge_monitor__delegate_local_analysis" in command
    assert command[command.index("--tools") + 1] == ""
    assert command[command.index("--allowedTools") + 1] == (
        "mcp__bridge_monitor__delegate_local_analysis")
    assert "Bash" in command[command.index("--disallowedTools") + 1:]


def test_subprocess_runner_keeps_cli_stderr_inside_project(tmp_path):
    runner = SubprocessRunner(cwd=tmp_path, timeout_seconds=5,
                              client_tool_timeout_seconds=5)
    command = [sys.executable, "-c",
               "import sys; print('stream-frame'); sys.stderr.write('diagnostic\\n')"]
    assert list(runner.stream(command, "probe")) == ["stream-frame\n"]
    assert runner.returncode == 0
    assert runner.last_stderr_path.is_relative_to(tmp_path)
    assert runner.last_stderr_path.read_text(encoding="utf-8") == "diagnostic\n"


def test_subprocess_runner_stops_active_process_on_cancellation(tmp_path):
    runner = SubprocessRunner(cwd=tmp_path, timeout_seconds=10,
                              client_tool_timeout_seconds=5)
    command = [sys.executable, "-c", "import time; time.sleep(10)"]
    assert list(runner.stream(command, "probe", cancel_requested=lambda: True)) == []
    assert runner.cancelled is True
    assert runner.terminated is True
    assert runner.returncode != 0


def test_subprocess_runner_cancellation_terminates_descendant(tmp_path):
    marker = tmp_path / "descendant-survived.txt"
    started = tmp_path / "descendant-started.txt"
    descendant = ("import pathlib,sys,time; time.sleep(0.6); "
                  "pathlib.Path(sys.argv[1]).write_text('survived'); time.sleep(1)")
    parent = ("import pathlib,subprocess,sys,time; "
              "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]], "
              "stdout=sys.stdout); "
              "pathlib.Path(sys.argv[3]).write_text('started'); time.sleep(3)")
    command = [sys.executable, "-c", parent, descendant, str(marker), str(started)]
    cancel = threading.Event()
    runner = SubprocessRunner(cwd=tmp_path, timeout_seconds=5,
        client_tool_timeout_seconds=5, cancel_poll_seconds=0.02)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(lambda: list(runner.stream(command, "probe",
            cancel_requested=cancel.is_set)))
        deadline = time.monotonic() + 2
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert started.exists()
        cancel.set()
        assert future.result(timeout=4) == []
    time.sleep(0.8)
    assert runner.cancelled and runner.terminated
    assert not marker.exists()


def test_stream_captures_intermediate_text_tool_events_and_final_model(tmp_path):
    frames = [
        {"type": "system", "subtype": "init", "model": "claude-opus-4"},
        {"type": "stream_event", "event": {"type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "Thinking "}}},
        {"type": "stream_event", "event": {"type": "content_block_start",
            "content_block": {"type": "tool_use", "id": "tool-1",
                              "name": "mcp__bridge_monitor__delegate_local_analysis"}}},
        {"type": "stream_event", "event": {"type": "content_block_stop", "index": 1}},
        {"type": "result", "result": "Final answer", "is_error": False,
         "model": "claude-opus-4"},
    ]
    sink, runner = Sink(), Runner(frames)
    result = ClaudeCLIAdapter(runner, executable="claude", model="opus",
                              mcp_config_path=tmp_path / "mcp.json").run(
                                  _lease(), _task(), "Exact user prompt", sink)
    assert result.state == "completed" and result.answer == "Final answer"
    assert result.local_tool_attempts == 1
    assert result.reported_model == "claude-opus-4"
    kinds = [kind for kind, _ in sink.events]
    assert EventType.RESPONSE_DELTA in kinds and EventType.TOOL_STARTED in kinds
    assert kinds.index(EventType.MODEL_REPORTED) < kinds.index(EventType.TOOL_STARTED)
    assert kinds.count(EventType.MODEL_REPORTED) == 1
    assert EventType.MESSAGE_SENT in kinds and EventType.RESPONSE_COMPLETED in kinds
    assert all("Bash" not in part for part in runner.commands[0][0][:1])


def test_missing_reported_model_and_lost_stream_are_uncertain(tmp_path):
    sink, runner = Sink(), Runner([{"type": "stream_event", "event": {"type": "content_block_delta",
        "delta": {"type": "text_delta", "text": "partial"}}}], fail=True)
    result = ClaudeCLIAdapter(runner, executable="claude", model="opus",
                              mcp_config_path=tmp_path / "mcp.json").run(
                                  _lease(), _task(), "prompt", sink)
    assert result.state == "uncertain" and result.reported_model is None
    assert EventType.ATTEMPT_UNCERTAIN in [kind for kind, _ in sink.events]


def test_child_mcp_config_is_bound_to_the_dispatched_lease(tmp_path):
    runner = Runner([{"type": "result", "result": "answer", "is_error": False}])
    seen = []
    def config_for(lease):
        seen.append((lease.call_id, lease.lease_id))
        return tmp_path / lease.lease_id / "child.json"
    adapter = ClaudeCLIAdapter(runner, executable="claude", model="opus",
                               mcp_config_factory=config_for)
    assert adapter.run(_lease(), _task(), "prompt", Sink()).state == "completed"
    assert seen == [("call-claude", "lease-claude")]
    command = runner.commands[0][0]
    assert command[command.index("--mcp-config") + 1] == str(
        tmp_path / "lease-claude" / "child.json")


def test_structured_claude_final_preserves_raw_body_and_reports_disposition(tmp_path):
    raw = json.dumps({"answer": "I kept Qwen's finding.", "local_results": [{
        "result_id": "local-result-1", "input_sha256": "a" * 64,
        "judgment": "accepted", "reason": "verified against code"}],
        "work_resolution": "resolved"})
    sink = Sink()
    result = ClaudeCLIAdapter(Runner([
        {"type": "result", "result": raw, "is_error": False,
         "model": "claude-opus-4"}]), executable="claude", model="opus",
        mcp_config_path=tmp_path / "mcp.json").run(_lease(), _task(), "Check code", sink)
    assert result.answer == "I kept Qwen's finding."
    assert result.raw_answer == raw
    assert result.raw_content_id
    assert result.completion_claim["local_results"][0]["judgment"] == "accepted"
    assert any(kind == EventType.RESPONSE_COMPLETED and kw["body"] == raw.encode()
               and kw["content_id"] == result.raw_content_id
               for kind, kw in sink.events)


def test_trailing_completion_object_after_prose_keeps_raw_reply(tmp_path):
    claim = {"answer": "37 × 14 = 518", "local_results": [{
        "result_id": "local-result-1", "input_sha256": "a" * 64,
        "judgment": "accepted", "reason": "checked arithmetic"}],
        "fallback_child_call_id": None, "work_resolution": "resolved"}
    raw = "Qwen's calculation checks out.\n\n" + json.dumps(claim)
    sink = Sink()
    result = ClaudeCLIAdapter(Runner([{"type": "result", "result": raw,
        "is_error": False}]), executable="claude", model="sonnet",
        mcp_config_path=tmp_path / "mcp.json").run(_lease(), _task(), "Check", sink)
    assert result.answer == claim["answer"]
    assert result.completion_claim == claim
    assert result.raw_answer == raw
    assert any(kind == EventType.RESPONSE_COMPLETED and kw["body"] == raw.encode()
               for kind, kw in sink.events)


def test_multiple_completion_objects_are_not_silently_selected(tmp_path):
    claim = {"answer": "first", "local_results": [],
             "fallback_child_call_id": None, "work_resolution": "resolved"}
    raw = json.dumps(claim) + "\n\n" + json.dumps({**claim, "answer": "second"})
    result = ClaudeCLIAdapter(Runner([{"type": "result", "result": raw,
        "is_error": False}]), executable="claude", model="sonnet",
        mcp_config_path=tmp_path / "mcp.json").run(_lease(), _task(), "Check", Sink())
    assert result.answer == raw
    assert result.completion_claim is None


def test_completion_object_followed_by_more_prose_is_unaccounted(tmp_path):
    raw = (json.dumps({"answer": "first", "local_results": [],
                       "fallback_child_call_id": None, "work_resolution": "resolved"})
           + "\nI changed my mind.")
    result = ClaudeCLIAdapter(Runner([{"type": "result", "result": raw,
        "is_error": False}]), executable="claude", model="sonnet",
        mcp_config_path=tmp_path / "mcp.json").run(_lease(), _task(), "Check", Sink())
    assert result.answer == raw
    assert result.completion_claim is None


def test_claude_usage_keeps_cache_input_counters(tmp_path):
    sink = Sink()
    result = ClaudeCLIAdapter(Runner([{"type": "result", "result": "answer",
        "is_error": False, "usage": {"input_tokens": 4,
        "cache_read_input_tokens": 2440, "cache_creation_input_tokens": 12,
        "output_tokens": 80}}]), executable="claude", model="sonnet",
        mcp_config_path=tmp_path / "mcp.json").run(_lease(), _task(), "prompt", sink)

    expected = {"input_tokens": 4, "cache_read_input_tokens": 2440,
                "cache_creation_input_tokens": 12, "output_tokens": 80}
    assert result.usage == expected
    usage_events = [fields["payload"] for kind, fields in sink.events
                    if kind == EventType.USAGE_REPORTED]
    assert len(usage_events) == 1
    assert all(usage_events[0][key] == value for key, value in expected.items())


def test_worker_system_prompt_requires_explicit_local_result_accounting(tmp_path):
    runner, sink = Runner([{"type": "result", "result": "plain answer",
                            "is_error": False}]), Sink()
    ClaudeCLIAdapter(runner, executable="claude", model="opus",
        mcp_config_path=tmp_path / "mcp.json").run(_lease(), _task(),
                                                     "Exact GPT prompt", sink)
    command, original = runner.commands[0]
    assert original == "Exact GPT prompt"
    system_prompt = command[command.index("--system-prompt") + 1]
    assert "local_results" in system_prompt
    assert "route_choice" in system_prompt
    assert "paid_direct" in system_prompt and "local_requested" in system_prompt
    assert "already_delivered" in system_prompt
    assert "accepted" in system_prompt and "revised" in system_prompt
    assert "formatting only" in system_prompt
    assert "substantive explanation" in system_prompt
    assert "paid_continuation_required" in system_prompt
    assert "source-read" in system_prompt
    assert any(kind == EventType.MESSAGE_SENT and kw["role"] == "system"
               and kw["body"] == system_prompt.encode() for kind, kw in sink.events)


def test_worker_system_prompt_publishes_the_repair_effort_contract(tmp_path):
    runner, sink = Runner([{"type": "result", "result": "answer", "is_error": False}]), Sink()
    ClaudeCLIAdapter(runner, executable="claude", model="sonnet",
        mcp_config_path=tmp_path / "mcp.json").run(_lease(), _task(), "Check", sink)
    command, _ = runner.commands[0]
    prompt = command[command.index("--system-prompt") + 1]
    choices = re.search(r"repair_effort values: (\[[^\]]+\])", prompt)
    assert choices, "The actual worker instructions must supply the allowed repair_effort literals"
    schema = DispositionRecord.model_json_schema()['properties']['repair_effort']
    allowed = next(choice['enum'] for choice in schema['anyOf'] if 'enum' in choice)
    assert json.loads(choices.group(1)) == allowed
    assert "Omit repair_effort when unknown" in prompt
    assert any(kind == EventType.MESSAGE_SENT and fields.get('role') == 'system'
               and fields['body'] == prompt.encode() for kind, fields in sink.events)
