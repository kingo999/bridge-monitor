"""Run the reviewed, opt-in parent-router shadow queue without an MCP surface."""

import argparse
import signal
import sys
import time
from pathlib import Path

import psycopg

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from bridge.code_fix_verifier import DockerVerifier
from bridge.router.shadow_worker import run_one_shadow
from bridge.startup import build_shadow_coordinator


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    coordinator = None
    while not stopping:
        if coordinator is None:
            try:
                coordinator = build_shadow_coordinator(args.config)
            except psycopg.OperationalError:
                time.sleep(1)
                continue
        try:
            coordinator._refresh_policy()
        except psycopg.OperationalError:
            # No dispatch starts without a current policy. Keep the process
            # alive so a transient local database socket failure can clear.
            time.sleep(coordinator.policy.router.worker_poll_ms / 1000)
            continue
        router = coordinator.policy.router
        if router.mode == "shadow" and router.verifier is not None:
            verifier = DockerVerifier(router.verifier,
                coordinator.project_root / "var" / "router" / "verify")
            try:
                outcome = run_one_shadow(coordinator, verifier)
            except psycopg.OperationalError:
                # A failure after the one-way launch claim remains fenced in
                # the database. Keep the service alive for unrelated jobs.
                time.sleep(router.worker_poll_ms / 1000)
                continue
            if outcome and outcome.get("state") == "uncertain":
                # The uncertain owner incarnation is fenced; a new one may
                # process unrelated work while the held call awaits reconciliation.
                coordinator = None
        time.sleep(router.worker_poll_ms / 1000)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
