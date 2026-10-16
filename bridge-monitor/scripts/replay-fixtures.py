from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.replay_fixtures import replay_fixture
import os


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python scripts/replay-fixtures.py path-to-fixture.json")
    dsn = os.environ.get("BRIDGE_MONITOR_TEST_DSN")
    if not dsn:
        raise SystemExit("BRIDGE_MONITOR_TEST_DSN is required")
    receipts = replay_fixture(dsn, Path(sys.argv[1]))
    print({state: receipts.count(state) for state in sorted(set(receipts))})
