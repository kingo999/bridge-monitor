"""Isolated synthetic E2E server; never points at the operational pilot database."""

import os
import sys
from pathlib import Path

import psycopg
import uvicorn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from database.migrate import migrate  # noqa: E402
from scripts.replay_fixtures import replay_fixture  # noqa: E402
from service.http.app import create_app  # noqa: E402


def main() -> None:
    dsn = os.environ.get("BRIDGE_MONITOR_TEST_DSN", "")
    if "bridge_monitor_test" not in dsn:
        raise SystemExit("E2E server requires dedicated bridge_monitor_test DSN")
    migrate(dsn)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE publication_stream, events, content_objects, producer_sequences, tasks RESTART IDENTITY CASCADE")
        conn.execute("UPDATE publication_counter SET next_position=1 WHERE singleton=true")
        conn.execute("UPDATE dashboard_status SET active=0,queued=0,held_uncertain=0,blocked=0,capture_state='unknown',known_gaps=0,spool_pressure=NULL WHERE singleton=true")
    replay_fixture(dsn, ROOT / "tests" / "fixtures" / "demonstration.json")
    app = create_app(dsn, producer_tokens={"seed": "producer-secret"},
                     operator_token="operator-secret", payload_limit_bytes=1048576,
                     dashboard_dir=ROOT / "dashboard" / "out")
    uvicorn.run(app, host="127.0.0.1", port=8874, log_level="warning")


if __name__ == "__main__":
    main()
