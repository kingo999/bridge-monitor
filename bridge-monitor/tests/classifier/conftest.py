import psycopg
import pytest

from bridge.classifier.config import ClassifierShadowConfig
from database.migrate import migrate


@pytest.fixture
def cdsn(test_dsn):
    """Dedicated test database with the classifier schema and empty classifier tables."""
    migrate(test_dsn)
    with psycopg.connect(test_dsn) as conn:
        conn.execute("TRUNCATE classifier_evaluations, classifier_observations")
        conn.execute("DELETE FROM content_objects WHERE content_id LIKE 'classifier:%'")
    return test_dsn


@pytest.fixture
def cfg():
    return ClassifierShadowConfig(enabled=True, max_queue=3, request_timeout_seconds=5)


GENERAL_FACTS = {"general_available": True, "code_available": True, "paid_allowed": True}


def table_counts(dsn: str) -> dict:
    """Row counts of every production-side table the classifier must not touch."""
    names = ["events", "calls", "runtime_jobs", "delegation_decisions", "results", "work_items",
             "runtime_task_budgets", "usage_observations", "model_executions", "attempts",
             "runtime_active_policy", "runtime_policy_activations"]
    with psycopg.connect(dsn) as conn:
        return {name: conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0] for name in names}
