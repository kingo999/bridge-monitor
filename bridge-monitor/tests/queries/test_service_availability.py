from service.queries.service_availability import ollama_ready, service_report


def test_model_availability_requires_reachable_matching_profile(monkeypatch):
    from service.queries import service_availability as module
    monkeypatch.setattr(module, "get_json", lambda url: {"models": [{"name": "test:1", "digest": "abc"}]})
    assert ollama_ready("http://fixture", "test:1", "abc")
    assert not ollama_ready("http://fixture", "other", "abc")
    assert not ollama_ready("http://fixture", "test:1", "different-digest")
    monkeypatch.setattr(module, "get_json", lambda url: None)
    assert not ollama_ready("http://fixture", "test:1", "abc")


def test_status_keeps_idle_reachable_services_visible_and_caches_probes(monkeypatch):
    from types import SimpleNamespace
    from service.queries import service_availability as module
    module._cache.clear()
    policy = SimpleNamespace(codex_claude=SimpleNamespace(enabled=True),
        deployment=SimpleNamespace(qwen_endpoint="http://fixture", qwen_model_profile="test:1",
                                   qwen_model_digest="abc", qwen_agent_endpoint=""))
    monkeypatch.setattr(module, "availability_policy", lambda dsn: policy)
    monkeypatch.setattr(module, "_check_claude", lambda *args: True)
    monkeypatch.setattr(module, "load_config", lambda path: SimpleNamespace(
        classifier_shadow=SimpleNamespace(enabled=True, endpoint="http://gemma", model="gemma", model_digest="def")))
    probes = []
    def reachable(endpoint, model, digest):
        probes.append(endpoint)
        return endpoint == "http://fixture"
    monkeypatch.setattr(module, "ollama_ready", reachable)
    import time
    for _ in range(100):
        first = service_report("test")
        if not first["refreshing"]:
            break
        time.sleep(0.005)
    assert first["services"] == dict(claude=True, qwen=True, gemma=False)
    assert service_report("test")["services"] == first["services"]
    assert sorted(probes) == ["http://fixture", "http://gemma"]
    module._cache.clear()


def test_claude_checks_authentication_and_provider_health(monkeypatch):
    from service.queries import service_availability as module
    monkeypatch.setattr(module, "claude_runtime_authenticated", lambda: True)
    monkeypatch.setattr(module, "claude_model_evidence", lambda dsn, models: {m: True for m in models})
    components = [
        dict(name="Claude Code", status="operational"),
        dict(name="Claude API (api.anthropic.com)", status="operational")]
    monkeypatch.setattr(module, "get_json", lambda url: dict(components=components))
    assert module.claude_available("test")
    components[0]["status"] = "degraded_performance"
    assert module.claude_available("test")
    components[0]["status"] = "major_outage"
    assert not module.claude_available("test")
    components[0]["status"] = "operational"
    monkeypatch.setattr(module, "claude_runtime_authenticated", lambda: False)
    assert not module.claude_available("test")


def test_claude_hides_on_quota_or_recent_runtime_failure(monkeypatch):
    from service.queries import service_availability as module
    monkeypatch.setattr(module, "claude_runtime_authenticated", lambda: True)
    monkeypatch.setattr(module, "claude_model_evidence", lambda dsn, models: {m: False for m in models})
    monkeypatch.setattr(module, "get_json", lambda url: dict(components=[
        dict(name="Claude Code", status="operational"),
        dict(name="Claude API (api.anthropic.com)", status="operational")]))
    assert not module.claude_available("test")


def test_claude_runtime_auth_status_is_checked_without_exposing_identity(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace
    from service.queries import service_availability as module
    executable = tmp_path / "claude.exe"
    executable.touch()
    config = tmp_path / "runtime.toml"
    config.write_text("[claude]\nexecutable = '" + str(executable) + "'\n")
    monkeypatch.setenv("BRIDGE_MONITOR_RUNTIME_CONFIG", str(config))
    result = SimpleNamespace(returncode=0, stdout=json.dumps(dict(loggedIn=True, apiProvider="firstParty", email="private")))
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: result)
    assert module.claude_runtime_authenticated() is True
    result.stdout = json.dumps(dict(loggedIn=False, apiProvider="firstParty"))
    assert module.claude_runtime_authenticated() is False
    result.stdout = "invalid"
    assert module.claude_runtime_authenticated() is False
    executable.unlink()
    assert module.claude_runtime_authenticated() is False
