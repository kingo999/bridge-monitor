import os

import pytest


def pytest_addoption(parser):
    parser.addoption("--live-models", action="store_true", default=False)


@pytest.fixture(scope="session")
def test_dsn():
    dsn = os.environ.get("BRIDGE_MONITOR_TEST_DSN")
    if not dsn:
        pytest.skip("dedicated BRIDGE_MONITOR_TEST_DSN is required")
    operational = os.environ.get("BRIDGE_MONITOR_DSN")
    if operational and operational == dsn:
        pytest.fail("test DSN must differ from operational DSN")
    if "bridge_monitor_test" not in dsn:
        pytest.fail("test DSN must name dedicated bridge_monitor_test database")
    return dsn


@pytest.fixture
def runtime_dsn(test_dsn):
    """Isolate new operational-state tests in the dedicated test database."""
    import psycopg
    from database.migrate import migrate
    migrate(test_dsn)
    with psycopg.connect(test_dsn) as conn:
        conn.execute("TRUNCATE runtime_completion_diagnostics, runtime_disposition_obligations, "
                     "runtime_recovery_links, runtime_fallback_transitions, "
                     "runtime_source_locks, runtime_reconciliations, runtime_jobs, "
                     "runtime_task_budgets, runtime_pool_cursor, runtime_incarnations, "
                     "runtime_policy_activations, runtime_active_policy, "
                     "example_reviews, guidance_pack_versions")
    return test_dsn
