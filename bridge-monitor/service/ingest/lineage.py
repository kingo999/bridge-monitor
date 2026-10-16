"""Explicit result-contribution links and observable survival boundaries."""


def would_create_cycle(conn, from_result_id: str, to_result_id: str) -> bool:
    if from_result_id == to_result_id:
        return True
    row = conn.execute("""WITH RECURSIVE descendants(id) AS (
        SELECT to_result_id FROM result_links WHERE from_result_id=%s
        UNION
        SELECT l.to_result_id FROM result_links l JOIN descendants d ON l.from_result_id=d.id
    ) SELECT 1 FROM descendants WHERE id=%s LIMIT 1""",
                       (to_result_id, from_result_id)).fetchone()
    return bool(row)


def final_survival(conn, local_result_id: str) -> str:
    """Unknown until a downstream final boundary and disposition are observed."""
    row = conn.execute("""WITH RECURSIVE descendants(id) AS (
        SELECT to_result_id FROM result_links WHERE from_result_id=%s
        UNION
        SELECT l.to_result_id FROM result_links l JOIN descendants d ON l.from_result_id=d.id
    ) SELECT 1 FROM descendants d
       JOIN work_resolutions w ON w.final_result_id=d.id
       JOIN dispositions x ON x.result_id=d.id AND x.stage IN ('gpt','human')
       WHERE x.judgment IN ('accepted','revised') LIMIT 1""",
                       (local_result_id,)).fetchone()
    return "confirmed" if row else "unknown"
