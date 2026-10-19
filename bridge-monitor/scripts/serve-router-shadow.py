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
    coordinator = build_shadow_coordinator(args.config)
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while not stopping:
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
            outcome = run_one_shadow(coordinator, verifier)
            if outcome and outcome.get("state") == "uncertain":
                # The uncertain owner incarnation is fenced; a new one may
                # process unrelated work while the held call awaits reconciliation.
                coordinator = build_shadow_coordinator(args.config)
        time.sleep(router.worker_poll_ms / 1000)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
