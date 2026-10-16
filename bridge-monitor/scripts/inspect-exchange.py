import getpass
import hmac
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service.queries.history import inspect_exchange


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python scripts/inspect-exchange.py WORK_ITEM_ID")
    dsn = os.environ.get("BRIDGE_MONITOR_TEST_DSN") or os.environ.get("BRIDGE_MONITOR_DSN")
    expected = os.environ.get("BRIDGE_MONITOR_OPERATOR_TOKEN")
    if not dsn or not expected:
        raise SystemExit("database and operator token environment references are required")
    supplied = (getpass.getpass("Operator token: ") if sys.stdin.isatty()
                else sys.stdin.readline().rstrip("\r\n"))
    if not hmac.compare_digest(supplied, expected):
        raise SystemExit("operator authentication failed")
    result = inspect_exchange(dsn, sys.argv[1], role="operator")
    if result is None:
        raise SystemExit("work item not found")
    print(json.dumps(result, indent=2, default=str))
