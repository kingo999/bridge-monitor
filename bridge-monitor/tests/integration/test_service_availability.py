import psycopg
from database.migrate import migrate
from service.queries.service_availability import claude_recently_blocked


def test_only_current_exhausted_allowance_hides_claude(test_dsn):
    migrate(test_dsn)
    with psycopg.connect(test_dsn) as db:
        db.execute("INSERT INTO cc_subscription_observations "
                   "(observation_id,provider,window_id,consumed_percent,window_start,resets_at,"
                   "observed_at,valid_until,producer_id,evidence_sha256) "
                   "VALUES ('availability-test','claude','five_hour',100,"
                   "now()-interval '1 hour',now()+interval '1 hour',now()-interval '1 minute',"
                   "now()+interval '1 minute','test','test')")
    try:
        assert claude_recently_blocked(test_dsn)
        with psycopg.connect(test_dsn) as db:
            db.execute("UPDATE cc_subscription_observations SET consumed_percent=99 WHERE observation_id='availability-test'")
        assert not claude_recently_blocked(test_dsn)
        with psycopg.connect(test_dsn) as db:
            db.execute("UPDATE cc_subscription_observations SET consumed_percent=100,valid_until=now()-interval '1 second' "
                       "WHERE observation_id='availability-test'")
        assert not claude_recently_blocked(test_dsn)
    finally:
        with psycopg.connect(test_dsn) as db:
            db.execute("DELETE FROM cc_subscription_observations WHERE observation_id='availability-test'")


def test_recent_provider_failure_hides_claude_until_success(test_dsn):
    import uuid
    from tests.integration.test_codex_claude_efficiency_db import _event
    migrate(test_dsn)
    identifier = "claude-health-" + uuid.uuid4().hex
    with psycopg.connect(test_dsn) as db:
        db.execute("INSERT INTO tasks(task_id,origin,created_at) VALUES(%s,'observed',now())", (identifier,))
        db.execute("INSERT INTO work_items(work_item_id,task_id,origin,created_at) VALUES(%s,%s,'observed',now())",
                   (identifier,identifier))
        db.execute("INSERT INTO calls(call_id,work_item_id,requested_model,created_at) VALUES(%s,%s,'claude-sonnet-test',now())",
                   (identifier,identifier))
        _event(db, identifier, identifier, identifier, "call.failed", 119000,
               payload=dict(provider_error_code="authentication_failed", provider_http_status=401))
        db.execute("UPDATE events SET origin='observed' WHERE call_id=%s", (identifier,))
    assert claude_recently_blocked(test_dsn)
    with psycopg.connect(test_dsn) as db:
        _event(db, identifier, identifier, identifier, "call.completed", 119900)
        db.execute("UPDATE events SET origin='observed' WHERE call_id=%s", (identifier,))
    assert not claude_recently_blocked(test_dsn)


def test_model_failure_needs_relevant_success_and_supports_suffixed_aliases(test_dsn):
    import uuid
    from tests.integration.test_codex_claude_efficiency_db import _event
    migrate(test_dsn)
    calls = {}
    for model in ("opus[1m]", "claude-sonnet-5-5"):
        identifier = "model-readiness-" + uuid.uuid4().hex
        calls[model] = identifier
        with psycopg.connect(test_dsn) as db:
            db.execute("INSERT INTO tasks(task_id,origin,created_at) VALUES(%s,'observed',now())", (identifier,))
            db.execute("INSERT INTO work_items(work_item_id,task_id,origin,created_at) VALUES(%s,%s,'observed',now())", (identifier,identifier))
            db.execute("INSERT INTO calls(call_id,work_item_id,requested_model,created_at) VALUES(%s,%s,%s,now())", (identifier,identifier,model))
    def emit(model, kind, offset, payload=None):
        identifier = calls[model]
        with psycopg.connect(test_dsn) as db:
            _event(db, identifier, identifier, identifier, kind, offset, payload=payload)
            db.execute("UPDATE events SET origin='observed' WHERE call_id=%s", (identifier,))
    emit("opus[1m]", "call.failed", 1000, dict(provider_error_code="rate_limit"))
    assert claude_recently_blocked(test_dsn, "claude-opus-5-5")
    assert not claude_recently_blocked(test_dsn, "claude-sonnet-5-5")
    emit("claude-sonnet-5-5", "call.completed", 116000)
    emit("opus[1m]", "call.failed", 117000, dict(reason="no_successful_result"))
    assert claude_recently_blocked(test_dsn, "claude-opus-5-5")
    emit("opus[1m]", "call.completed", 118000)
    assert not claude_recently_blocked(test_dsn, "claude-opus-5-5")


def test_model_scoped_quota_keeps_other_models_available(test_dsn):
    migrate(test_dsn)
    with psycopg.connect(test_dsn) as db:
        db.execute("INSERT INTO cc_subscription_observations "
                   "(observation_id,provider,window_id,consumed_percent,window_start,resets_at,"
                   "observed_at,valid_until,producer_id,evidence_sha256) "
                   "VALUES ('opus-quota-test','claude','seven_day_opus',100,"
                   "now()-interval '1 hour',now()+interval '1 hour',now()-interval '1 minute',"
                   "now()+interval '1 minute','test','test')")
    try:
        assert claude_recently_blocked(test_dsn, "claude-opus-5-5")
        assert not claude_recently_blocked(test_dsn, "claude-sonnet-5-5")
        assert not claude_recently_blocked(test_dsn)
    finally:
        with psycopg.connect(test_dsn) as db:
            db.execute("DELETE FROM cc_subscription_observations WHERE observation_id='opus-quota-test'")


def test_account_recovery_preserves_model_overload_and_ignores_other_versions(test_dsn):
    import uuid
    from tests.integration.test_codex_claude_efficiency_db import _event
    from service.queries.service_availability import claude_model_evidence
    migrate(test_dsn)
    def emit(model, kind, offset, payload=None):
        identifier = "readiness-edges-" + uuid.uuid4().hex
        with psycopg.connect(test_dsn) as db:
            db.execute("INSERT INTO tasks(task_id,origin,created_at) VALUES(%s,'observed',now())", (identifier,))
            db.execute("INSERT INTO work_items(work_item_id,task_id,origin,created_at) VALUES(%s,%s,'observed',now())", (identifier,identifier))
            db.execute("INSERT INTO calls(call_id,work_item_id,requested_model,created_at) VALUES(%s,%s,%s,now())", (identifier,identifier,model))
            _event(db, identifier, identifier, identifier, kind, 120000, payload=payload)
            db.execute("UPDATE events SET origin='observed' WHERE call_id=%s", (identifier,))
    models = ["claude-sonnet-5-5", "claude-opus-5-5"]
    emit("opus[1m]", "call.failed", 1000, dict(provider_http_status=529))
    emit("sonnet", "call.failed", 2000, dict(provider_http_status=401))
    assert claude_model_evidence(test_dsn, models) == dict.fromkeys(models, False)
    emit("sonnet", "call.completed", 3000)
    emit("claude-sonnet-4-5", "call.failed", 4000, dict(provider_error_code="rate_limit"))
    emit("claude-opus-4-5", "call.completed", 5000)
    assert claude_model_evidence(test_dsn, models) == {models[0]: True, models[1]: False}
    emit("opus[1m]", "call.completed", 6000)
    assert claude_model_evidence(test_dsn, models) == dict.fromkeys(models, True)


def test_unknown_quota_window_does_not_block_account(test_dsn):
    migrate(test_dsn)
    with psycopg.connect(test_dsn) as db:
        db.execute("INSERT INTO cc_subscription_observations "
                   "(observation_id,provider,window_id,consumed_percent,window_start,resets_at,"
                   "observed_at,valid_until,producer_id,evidence_sha256) "
                   "VALUES ('unknown-quota-test','claude','seven_day_oauth_apps',100,"
                   "now()-interval '1 hour',now()+interval '1 hour',now()-interval '1 minute',"
                   "now()+interval '1 minute','test','test')")
    try:
        assert not claude_recently_blocked(test_dsn)
    finally:
        with psycopg.connect(test_dsn) as db:
            db.execute("DELETE FROM cc_subscription_observations WHERE observation_id='unknown-quota-test'")
