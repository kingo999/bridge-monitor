import psycopg


def publish(conn: psycopg.Connection, event_id: str) -> int:
    # The counter row lock is held until the enclosing transaction commits.
    position = conn.execute(
        "UPDATE publication_counter SET next_position=next_position+1 WHERE singleton=true RETURNING next_position-1"
    ).fetchone()[0]
    conn.execute("INSERT INTO publication_stream(position, event_id) VALUES(%s,%s)",
                 (position, event_id))
    return position
