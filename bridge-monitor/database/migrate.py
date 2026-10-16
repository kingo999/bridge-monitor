from pathlib import Path

import psycopg


MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def migrate(dsn: str) -> list[str]:
    applied = []
    with psycopg.connect(dsn) as conn:
        with conn.transaction():
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
            for source in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")):
                if conn.execute("SELECT 1 FROM schema_migrations WHERE version=%s", (source.stem,)).fetchone():
                    continue
                conn.execute(source.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations(version) VALUES(%s)", (source.stem,))
                applied.append(source.stem)
    return applied


if __name__ == "__main__":
    import os
    import sys
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m database.migrate TEST_OR_PROJECT_DSN_ENV_NAME")
    dsn = os.environ.get(sys.argv[1])
    if not dsn:
        raise SystemExit("named DSN environment variable is missing")
    print("\n".join(migrate(dsn)))
