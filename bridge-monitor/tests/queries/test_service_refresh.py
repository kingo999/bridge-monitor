import threading
import time
from concurrent.futures import ThreadPoolExecutor

from service.queries import service_availability as module


def wait_report(dsn="test"):
    until = time.monotonic() + 2
    while time.monotonic() < until:
        result = module.service_report(dsn)
        if not result["refreshing"]:
            return result
        time.sleep(0.005)
    raise AssertionError("service refresh did not finish within its result deadline")


def test_one_probe_error_does_not_hide_other_services(monkeypatch):
    module._cache.clear()
    def fail(*args):
        raise RuntimeError("Claude metadata unavailable")
    monkeypatch.setattr(module, "_check_claude", fail)
    monkeypatch.setattr(module, "_check_qwen", lambda *args: True)
    monkeypatch.setattr(module, "_check_gemma", lambda *args: True)
    result = wait_report()
    assert result["services"] == dict(claude=False, qwen=True, gemma=True)
    module._cache.clear()


def test_slow_probe_never_blocks_requests_or_duplicates_refresh(monkeypatch):
    module._cache.clear()
    release = threading.Event()
    started = threading.Event()
    calls = []
    def slow(*args):
        calls.append("claude")
        started.set()
        release.wait(2)
        return True
    monkeypatch.setattr(module, "PROBE_SECONDS", 0.05)
    monkeypatch.setattr(module, "_check_claude", slow)
    monkeypatch.setattr(module, "_check_qwen", lambda *args: True)
    monkeypatch.setattr(module, "_check_gemma", lambda *args: True)
    try:
        begin = time.monotonic()
        first = module.service_report("slow")
        assert time.monotonic() - begin < 0.3
        assert first["refreshing"]
        assert started.wait(1)
        with ThreadPoolExecutor(max_workers=8) as readers:
            list(readers.map(lambda _: module.service_report("slow"), range(24)))
        assert calls == ["claude"]
        result = wait_report("slow")
        assert result["services"] == dict(claude=False, qwen=True, gemma=True)
        assert result["checks"]["claude"]["state"] == "timed_out"
    finally:
        release.set()
        module._cache.clear()


def test_invalid_urls_are_an_unavailable_result(monkeypatch):
    def invalid(*args, **kwargs):
        raise module.httpx.InvalidURL("invalid configured URL")
    monkeypatch.setattr(module.httpx.Client, "stream", invalid)
    assert module.get_json("http://fixture") is None


def test_agent_probe_uses_https_port_and_rejects_malformed_endpoint(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    deployment = SimpleNamespace(qwen_endpoint="http://inference", qwen_model_profile="model",
                                 qwen_model_digest="digest", qwen_agent_endpoint="https://agent")
    monkeypatch.setattr(module, "availability_policy", lambda dsn: SimpleNamespace(deployment=deployment))
    monkeypatch.setattr(module, "ollama_ready", lambda *args: True)
    connections = []
    def connect(address, timeout):
        connections.append(address)
        return nullcontext()
    monkeypatch.setattr(module.socket, "create_connection", connect)
    assert module._check_qwen("test", None)
    assert connections == [("agent", 443)]
    deployment.qwen_agent_endpoint = "agent-without-a-scheme"
    assert not module._check_qwen("test", None)
    assert len(connections) == 1
