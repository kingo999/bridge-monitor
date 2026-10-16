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
