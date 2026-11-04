"""Drain the local capture journal into PostgreSQL. SQLite remains the durable input buffer."""
import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import psycopg


# Names are fixed schema identifiers, never user input. Keys precede value columns.
TABLES = {
    "policy": (("version",), ("document",)),
    "active": (("singleton",), ("version",)),
    "roots": (("host", "pid", "birth"), ("owner",)),
    "process_state": (("host", "pid", "birth"), ("shared", "ended_ns")),
    "observations": (("id",), ("evidence", "version", "decision", "matched")),
}
INTEGER_FIELDS = {"singleton", "pid", "birth", "shared", "ended_ns", "id"}


def file_identity(path):
    stat = path.stat()
    if not stat.st_ino:
        raise ValueError("journal does not expose a stable file identity")
    return f"{stat.st_dev}:{stat.st_ino}:{getattr(stat, 'st_birthtime_ns', 0)}"


def stage_table(source, target, name, columns, after):
    fields = ", ".join(columns)
    target.execute(f"CREATE TEMP TABLE staged_{name} ON COMMIT DROP AS "
                   f"SELECT {fields} FROM outbound_audit.{name} WITH NO DATA")
    query = f"SELECT {fields} FROM {name}"
    params = ()
    if after is not None:
        query += " WHERE id >= ?"
        params = (after,)
    with target.cursor() as cur, cur.copy(f"COPY staged_{name} ({fields}) FROM STDIN") as stream:
        for row in source.execute(query, params):
            for column, value in zip(columns, row):
                expected = int if column in INTEGER_FIELDS else str
                if value is None and column in TABLES[name][0]:
                    raise ValueError(f"{name}.{column}: null source key; import rolled back")
                if value is not None and not isinstance(value, expected):
                    raise ValueError(f"{name}.{column}: unexpected SQLite storage type; import rolled back")
            stream.write_row(row)
    return target.execute(f"SELECT count(*) FROM staged_{name}").fetchone()[0]


def merge_table(target, name, keys, values, after):
    fields = ", ".join((*keys, *values))
    conflict = "DO NOTHING"
    if name in {"active", "process_state"}:
        assignments = ", ".join(f"{c}=EXCLUDED.{c}" for c in values)
        old = ", ".join(f"t.{c}" for c in values)
        new = ", ".join(f"EXCLUDED.{c}" for c in values)
        conflict = f"DO UPDATE SET {assignments} WHERE ROW({old}) IS DISTINCT FROM ROW({new})"
    target.execute(f"INSERT INTO outbound_audit.{name} AS t ({fields}) "
                   f"SELECT {fields} FROM staged_{name} ON CONFLICT ({', '.join(keys)}) {conflict}")
    remote = f"SELECT {fields} FROM outbound_audit.{name}"
    args = ()
    if after is not None:
        remote += " WHERE id >= %s"
        args = (after,)
    local = f"SELECT {fields} FROM staged_{name}"
    different = target.execute(f"SELECT EXISTS(({local} EXCEPT {remote}) "
                               f"UNION ALL ({remote} EXCEPT {local}))", args + args).fetchone()[0]
    if different:
        raise ValueError(f"{name}: source and retained rows differ; import rolled back")


def sync(journal, dsn, *, verify_all=False):
    path = Path(journal).resolve(strict=True)
    identity = file_identity(path)
    with psycopg.connect(dsn, connect_timeout=5) as target:
        target.execute("SET LOCAL lock_timeout = '10s'")
        target.execute("SELECT pg_advisory_xact_lock(673244013, 14)")
        target.execute("INSERT INTO outbound_audit.source (journal_path, file_identity) VALUES (%s,%s) "
                       "ON CONFLICT (singleton) DO NOTHING", (str(path), identity))
        saved_path, saved_identity, last = target.execute(
            "SELECT journal_path, file_identity, last_id FROM outbound_audit.source WHERE singleton FOR UPDATE"
        ).fetchone()
        if (Path(saved_path), saved_identity) != (path, identity):
            raise ValueError("registered SQLite journal identity differs; reconcile before continuing")
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as source:
            source.execute("BEGIN")
            known = {row[0] for row in source.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT GLOB 'sqlite_*'")}
            if known != set(TABLES):
                raise ValueError("unexpected journal tables; review schema before import")
            high = source.execute("SELECT coalesce(max(id),0) FROM observations").fetchone()[0]
            if high < last:
                raise ValueError("journal high-water mark regressed")
            staged = {}
            for name, (keys, values) in TABLES.items():
                columns = (*keys, *values)
                if tuple(row[1] for row in source.execute(f"PRAGMA table_info({name})")) != columns:
                    raise ValueError(f"{name}: unexpected journal columns")
                after = last if name == "observations" and last and not verify_all else None
                staged[name] = stage_table(source, target, name, columns, after)
                merge_table(target, name, keys, values, after)
            if file_identity(path) != identity:
                raise ValueError("journal was replaced during import")
            target.execute("UPDATE outbound_audit.source SET last_id=%s, synced_at=clock_timestamp() "
                           "WHERE singleton", (high,))
    return {"verified_rows": staged, "previous_high_id": last, "high_id": high}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, required=True, help="protected JSON file with journal and dsn")
    parser.add_argument("--verify-all", action="store_true", help="compare all retained rows and fields")
    args = parser.parse_args()
    settings = json.loads(args.settings.read_text(encoding="utf-8"))
    try:
        result = sync(settings["journal"], settings["dsn"], verify_all=args.verify_all)
    except psycopg.Error as exc:
        # A COPY error can contain the input row. Keep metadata out of task logs.
        raise SystemExit(f"PostgreSQL import failed ({exc.sqlstate or type(exc).__name__})") from None
    print(json.dumps(result))


if __name__ == "__main__":
    main()
