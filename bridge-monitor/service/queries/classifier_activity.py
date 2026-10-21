"""Current classifier state only; never queries or controls production routing."""
import psycopg
from psycopg.rows import dict_row


def query_classifier_activity(dsn: str) -> dict:
    with psycopg.connect(dsn, connect_timeout=2, row_factory=dict_row) as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout='1000ms'")
        conn.execute("SET LOCAL lock_timeout='200ms'")
        checked_at = conn.execute('SELECT clock_timestamp() AS now').fetchone()['now']
        if not conn.execute("SELECT to_regclass('classifier_observations') IS NOT NULL AS ok").fetchone()['ok']:
            return dict(available=False, checked_at=checked_at, queued=None,
                        classifying=None, uncertain=None, latest=None)
        counts = {r['state']: r['total'] for r in conn.execute(
            "SELECT state,count(*) AS total FROM classifier_observations "
            "WHERE state IN ('captured','dispatching','uncertain') GROUP BY state")}
        latest = conn.execute("""SELECT observation_id,work_item_id,state,requested_model,
            captured_at,dispatched_at,completed_at,rec_route,origin_kind,
            CASE WHEN facts_provenance->>'capture_method' IN ('route_work','bridge_ingress')
                 THEN facts_provenance->>'capture_method' ELSE 'manual' END AS capture_method
            FROM classifier_observations ORDER BY captured_at DESC,observation_id DESC LIMIT 1""").fetchone()
    return dict(available=True, checked_at=checked_at, queued=counts.get('captured', 0),
                classifying=counts.get('dispatching', 0), uncertain=counts.get('uncertain', 0),
                latest=dict(latest) if latest else None)
