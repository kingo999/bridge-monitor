from service.queries.distributions import classify, model_family


def test_retained_local_and_paid_revision_are_distinct():
    local = classify("bridge", "local_retained", {"claude", "qwen"},
                     {"claude": {"sonnet"}, "qwen": {"qwen"}})
    revised = classify("bridge", "paid_revision_with_contribution", {"claude", "qwen"},
                       {"claude": {"sonnet"}, "qwen": {"qwen"}})
    assert local == {"node": "skynet", "model": "qwen", "handling": "qwen_full", "tier": "local"}
    assert revised == {"node": "mixed", "model": "mixed", "handling": "qwen_partial", "tier": "mixed"}


def test_unresolved_and_missing_worker_evidence_are_explicit():
    assert set(classify("bridge", None, {"qwen"}, {}).values()) == {"in_progress"}
    assert set(classify("bridge", "unknown", {"qwen"}, {}).values()) == {"unknown"}
    assert set(classify("bridge", "local_retained", set(), {}).values()) == {"unknown"}


def test_direct_codex_and_requested_model_family():
    assert classify("direct_codex", "completed", set(), {"codex": {"sol"}}) == {
        "node": "codex", "model": "sol", "handling": "codex_only", "tier": "paid"}
    assert model_family("gpt-6.1-sol") == "sol"
    assert model_family("claude-sonnet-5-5") == "sonnet"
    assert model_family("Qwen3:12b") == "qwen"
