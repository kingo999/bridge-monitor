import json
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace

from bridge import experiment_transports as transports


def test_parent_and_paid_worker_run_outside_project_directory(monkeypatch):
    observed = []
    def fake_run(command, **kwargs):
        cwd = Path(kwargs["cwd"])
        observed.append((command, cwd, sorted(path.name for path in cwd.iterdir())))
        assert cwd != transports.PROJECT and cwd.exists()
        if command[0] == "codex":
            assert "--skip-git-repo-check" in command
            assert command[command.index("-C") + 1] == str(cwd)
            output = "\n".join(json.dumps(item) for item in [
                {"type": "item.completed", "item": {"type": "agent_message", "text": "okay"}},
                {"type": "turn.completed", "usage": {"input_tokens": 10,
                    "output_tokens": 2}},
            ])
        else:
            output = json.dumps({"result": '{"answer":"42"}', "usage": {
                "input_tokens": 1, "output_tokens": 2}, "modelUsage": {
                "claude-sonnet-test": {}}})
        return CompletedProcess(command, 0, output, "")
    monkeypatch.setattr("subprocess.run", fake_run)
    parent = transports.codex_parent_call("trusted metadata only", model="gpt-test",
        timeout_seconds=20)
    paid = transports.sonnet_call("trusted task", model="sonnet", settings=
        SimpleNamespace(paid_max_budget_usd=0.1, paid_timeout_seconds=20))
    assert parent["usage"]["input_tokens"] == 10 and paid.usage["output_tokens"] == 2
    assert len(observed) == 2
    assert all(files == [] for _, _, files in observed)
