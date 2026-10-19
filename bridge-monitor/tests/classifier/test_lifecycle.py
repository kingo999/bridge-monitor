"""Launcher ownership: only a process matching its receipt exactly may be stopped."""

import json
import os
import shutil
import socket
import subprocess
import sys
import time

import pytest

from bridge.classifier import winproc
from bridge.classifier.config import RuntimeConfig
from bridge.classifier.lifecycle import EXIT, RuntimeController

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows process identity")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def controller(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    # A stand-in "ollama.exe" that merely sleeps, so no GPU or model is involved.
    base = getattr(sys, "_base_executable", sys.executable)
    shutil.copy(base, runtime / "ollama.exe")
    for dll in ("python3.dll", "python313.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        source = os.path.join(os.path.dirname(base), dll)
        if os.path.exists(source):
            shutil.copy(source, runtime / dll)
    cfg = RuntimeConfig(runtime_dir=str(runtime), state_dir=str(tmp_path / "state"),
                        log_dir=str(tmp_path / "logs"), models_dir=str(tmp_path / "models"),
                        port=free_port(), stop_timeout_seconds=10)
    return RuntimeController(cfg)


def spawn_sleeper(exe):
    return subprocess.Popen([str(exe), "-c", "import time; time.sleep(60)"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def write_receipt(controller, process, **overrides):
    identity = winproc.identify(process.pid)
    receipt = {"launch_id": "test-launch", "pid": identity.pid, "executable": identity.image_path,
               "created_ticks": identity.created_ticks, "created_utc": identity.created_utc,
               **overrides}
    controller.state_dir.mkdir(parents=True, exist_ok=True)
    controller.current.write_text(json.dumps(receipt))
    return receipt


def test_environment_is_process_scoped_loopback_single_flight_and_cloud_disabled(controller, tmp_path,
                                                                                  monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0:9")
    monkeypatch.setenv("OLLAMA_ORIGINS", "*")
    env = controller.environment(tmp_path / "profile")
    assert env["OLLAMA_HOST"] == f"127.0.0.1:{controller.cfg.port}"
    assert env["OLLAMA_NO_CLOUD"] == "1" and env["OLLAMA_NUM_PARALLEL"] == "1"
    assert env["OLLAMA_MAX_LOADED_MODELS"] == "1" and env["OLLAMA_CONTEXT_LENGTH"] == "8192"
    assert "OLLAMA_ORIGINS" not in env                       # inherited settings do not leak in
    assert os.environ["OLLAMA_HOST"] == "0.0.0.0:9"          # and the parent environment is untouched
    with pytest.raises(ValueError):
        RuntimeConfig(host="0.0.0.0")


def test_creation_time_keeps_100ns_precision():
    me = winproc.identify(os.getpid())
    stamp = me.created_utc
    assert stamp.endswith("Z") and len(stamp.split(".")[1]) == 8   # 7 digits + Z
    assert me.created_ticks > 0


def test_stop_refuses_when_pid_is_held_by_a_different_process(controller):
    unrelated = spawn_sleeper(sys.executable)
    try:
        time.sleep(0.3)
        # Receipt claims the right image but the wrong creation time: a reused PID.
        write_receipt(controller, unrelated, executable=str(controller.exe),
                      created_ticks=winproc.identify(unrelated.pid).created_ticks + 1)
        result = controller.stop()
        assert result["result"] == "refused" and result["exit"] == EXIT["refused"]
        assert unrelated.poll() is None and controller.current.exists()
    finally:
        unrelated.kill()


def test_stop_refuses_when_executable_is_not_the_launchers_runtime(controller):
    unrelated = spawn_sleeper(sys.executable)
    try:
        time.sleep(0.3)
        write_receipt(controller, unrelated)         # exact PID and time, but not our runtime
        assert controller.verify_owner(controller.read_receipt())[0] == "identity_mismatch"
        assert controller.stop()["result"] == "refused"
        assert unrelated.poll() is None
    finally:
        unrelated.kill()


def test_start_refuses_an_occupied_port_and_touches_nothing(controller):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", controller.cfg.port))
        listener.listen()
        result = controller.start()
        assert result["reason"] == "port_occupied_by_unowned_process"
        assert result["exit"] == EXIT["unowned_listener"]
        assert controller.stop()["result"] == "refused"      # no receipt: not ours to stop
        assert not controller.current.exists()


def test_stop_terminates_an_owned_process_and_archives_its_receipt(controller):
    process = spawn_sleeper(controller.exe)
    try:
        time.sleep(0.3)
        write_receipt(controller, process)
        assert controller.status()["process"]["state"] == "owned"
        result = controller.stop()
        assert result["result"] == "stopped" and process.wait(timeout=10) is not None
        assert not controller.current.exists()
        archived = json.loads(open(result["receipt"], encoding="utf-8").read())
        assert archived["pid"] == process.pid and "stopped_at" in archived
        assert controller.stop()["result"] == "nothing_to_stop"
    finally:
        if process.poll() is None:
            process.kill()


def test_dead_owner_receipt_is_archived_as_stale_not_overwritten(controller):
    process = spawn_sleeper(controller.exe)
    time.sleep(0.3)
    write_receipt(controller, process)
    process.kill()
    process.wait()
    result = controller.stop()
    assert result["result"] == "already_stopped"
    assert os.path.exists(result["receipt"]) and "stale-test-launch" in result["receipt"]


def test_preflight_rejects_wrong_runtime_or_model_digest(controller):
    problems = controller.preflight()
    assert "runtime_digest_mismatch" in problems and "model_manifest_missing" in problems
    assert controller.start()["reason"] == "preflight_failed"
    assert not controller.current.exists()                   # nothing was launched
