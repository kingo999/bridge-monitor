"""Opt-in normal flow through the same isolated Codex client as the live pilot."""

import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

from bridge.runtime.policy_startup import active_policy


PROJECT = Path(__file__).resolve().parents[2]


def test_codex_sonnet_qwen_normal_flow(request):
    if not request.config.getoption("--live-models"):
        pytest.skip("live-models opt-in required")
    dsn = os.environ.get("BRIDGE_MONITOR_DSN")
    runtime = os.environ.get("BRIDGE_MONITOR_RUNTIME_CONFIG")
    if not dsn or not runtime:
        pytest.fail("BRIDGE_MONITOR_DSN and BRIDGE_MONITOR_RUNTIME_CONFIG are required")
    if os.environ.get("BRIDGE_MONITOR_TEST_DSN") == dsn:
        pytest.fail("live pilot must not use the dedicated test database")
    with psycopg.connect(dsn) as conn:
        held = conn.execute("SELECT count(*) FROM runtime_jobs WHERE pool='qwen' "
                            "AND slot_held=true").fetchone()[0]
    if held:
        pytest.fail("local capacity is held uncertain; reconcile with evidence first")
    policy = active_policy(dsn)
    if not policy or policy.limits.qwen_execution_seconds < 120:
        pytest.fail("normal reviewed policy must be active for this test")

    completed = subprocess.run([sys.executable,
        str(PROJECT / "scripts" / "run-codex-pilot.py"), "--case", "normal"],
        cwd=PROJECT, env=os.environ.copy(), capture_output=True, text=True,
        timeout=660, encoding="utf-8", errors="replace")
    assert completed.returncode == 0, completed.stderr + completed.stdout
    receipt = json.loads(completed.stdout.strip().splitlines()[-1])
    assert receipt["routes"] == ["local"]
    assert receipt["resolution"] == "local_retained"
    assert receipt["bridge_result"]["accounting_complete"] is True
