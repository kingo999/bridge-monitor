"""Candidate code receives inputs, never the host-held expected outputs."""

import json
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired

import pytest

from bridge.code_fix_verifier import DockerVerifier, VerifierSettings


IMAGE = "python@sha256:" + "a" * 64
ENDPOINT = "npipe:////./pipe/dockerDesktopLinuxEngine"
TESTS = [{"function": "answer", "args": [], "expected": 42}]


def settings(**updates):
    return VerifierSettings.model_validate({"image": IMAGE,
        "daemon_endpoint": ENDPOINT, **updates})


def fake_docker(monkeypatch, *, candidate_result=42, run_exit=0, cleanup_exit=0):
    seen = []
    def run(command, **kwargs):
        seen.append((command, kwargs))
        if command[1] == "context":
            return CompletedProcess(command, 0, ENDPOINT + "\n", "")
        if command[1] == "run":
            if run_exit == 0:
                output_mount = [command[i + 1] for i, value in enumerate(command)
                                if value == "--mount"][1]
                destination = Path(output_mount.split("src=", 1)[1].split(",", 1)[0])
                destination.write_text(json.dumps({"results": [candidate_result]}),
                                       encoding="utf-8")
            return CompletedProcess(command, run_exit, "", "")
        if command[1] == "rm":
            return CompletedProcess(command, cleanup_exit, "", "")
        raise AssertionError(command)
    monkeypatch.setattr("subprocess.run", run)
    return seen


def test_container_is_isolated_and_expected_values_are_not_mounted(tmp_path, monkeypatch):
    seen = fake_docker(monkeypatch)
    import subprocess
    fake_run = subprocess.run
    mounted_files = []
    runner_text = []
    def inspecting(command, **kwargs):
        if command[1] == "run":
            mounted = Path(command[command.index("--mount") + 1].split("src=", 1)[1].split(",", 1)[0])
            mounted_files.extend(path.name for path in mounted.iterdir())
            runner_text.append((mounted / "runner.py").read_text(encoding="utf-8"))
        return fake_run(command, **kwargs)
    monkeypatch.setattr("subprocess.run", inspecting)
    result = DockerVerifier(settings(), tmp_path).verify("def answer(): return 42\n", TESTS)
    assert result.state == "pass"
    command, kwargs = next((c, k) for c, k in seen if c[1] == "run")
    assert command[:4] == ["docker", "run", "-i", "--name"]
    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command and ["--cap-drop", "ALL"] == command[command.index("--cap-drop"):command.index("--cap-drop") + 2]
    assert command[command.index("--user") + 1] == "65534:65534"
    assert command[command.index("--pull") + 1] == "never"
    assert "--memory-swap" in command and IMAGE in command
    assert "readonly" in command[command.index("--mount") + 1]
    assert kwargs["timeout"] == settings().timeout_seconds
    assert "expected" not in kwargs["input"]
    assert sorted(mounted_files) == ["candidate.py", "result.json", "runner.py"]
    assert "42" not in runner_text[0]


def test_wrong_result_fails_and_oversized_source_stops_before_dispatch(tmp_path, monkeypatch):
    fake_docker(monkeypatch, candidate_result=41)
    assert DockerVerifier(settings(), tmp_path).verify("pass", TESTS).state == "fail"
    seen = fake_docker(monkeypatch)
    result = DockerVerifier(settings(max_source_bytes=8), tmp_path).verify("too much source", TESTS)
    assert result.reason == "source_too_large"
    assert not seen


def test_unpaired_surrogate_is_rejected_before_docker(tmp_path, monkeypatch):
    seen = fake_docker(monkeypatch)
    result = DockerVerifier(settings(), tmp_path).verify("\ud800", TESTS)
    assert result.state == "fail" and result.reason == "invalid_utf8_source"
    assert not seen


def test_timeout_removes_container_and_cleanup_failure_is_reported(tmp_path, monkeypatch):
    calls = []
    def run(command, **_kwargs):
        calls.append(command)
        if command[1] == "context":
            return CompletedProcess(command, 0, ENDPOINT, "")
        if command[1] == "run":
            raise TimeoutExpired(command, 1)
        return CompletedProcess(command, 0, "", "")
    monkeypatch.setattr("subprocess.run", run)
    result = DockerVerifier(settings(), tmp_path).verify("pass", TESTS)
    assert result.reason == "container_timeout"
    name = calls[1][calls[1].index("--name") + 1]
    assert calls[-1] == ["docker", "rm", "-f", name]
    seen = fake_docker(monkeypatch, cleanup_exit=1)
    result = DockerVerifier(settings(), tmp_path).verify("pass", TESTS)
    assert result.state == "uncertain" and result.reason == "container_cleanup_failed"
    assert seen[-1][0][1] == "rm"


def test_remote_daemon_and_bad_reference_reject_before_candidate_dispatch(tmp_path, monkeypatch):
    seen = fake_docker(monkeypatch)
    verifier = DockerVerifier(settings(daemon_endpoint="npipe:////./pipe/other"), tmp_path)
    assert verifier.verify("pass", TESTS).reason == "daemon_endpoint_mismatch"
    assert not any(command[1] == "run" for command, _ in seen)
    seen = fake_docker(monkeypatch, candidate_result=41)
    assert DockerVerifier(settings(), tmp_path).validate_case("bad", "good", TESTS).reason == "reference_not_passing"


def test_reference_passes_and_baseline_fails(tmp_path, monkeypatch):
    def run(command, **_kwargs):
        if command[1] == "context":
            return CompletedProcess(command, 0, ENDPOINT, "")
        if command[1] == "run":
            mounts = [command[i + 1] for i, part in enumerate(command) if part == "--mount"]
            source = Path(mounts[0].split("src=", 1)[1].split(",", 1)[0])
            output = Path(mounts[1].split("src=", 1)[1].split(",", 1)[0])
            value = 42 if "good" in (source / "candidate.py").read_text(encoding="utf-8") else 41
            output.write_text(json.dumps({"results": [value]}), encoding="utf-8")
            return CompletedProcess(command, 0, "", "")
        return CompletedProcess(command, 0, "", "")
    commands = []
    def recorded(command, **kwargs):
        commands.append(command)
        return run(command, **kwargs)
    monkeypatch.setattr("subprocess.run", recorded)
    checked = DockerVerifier(settings(), tmp_path).validate_case("bad", "good", TESTS)
    assert checked.state == "ready"
    assert checked.reference.state == "pass" and checked.baseline.state == "fail"


def test_unpinned_image_is_rejected():
    with pytest.raises(ValueError):
        VerifierSettings(image="python:latest", daemon_endpoint=ENDPOINT)
