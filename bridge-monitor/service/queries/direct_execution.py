"""Execution labels and scoped direct usage, shared by live/history/detail queries."""

from psycopg.rows import dict_row


def execution_metadata(conn, work_ids, as_of):
    if not work_ids:
        return {}
    with conn.cursor(row_factory=dict_row) as cursor:
        direct = cursor.execute("SELECT DISTINCT ON (work_item_id) work_item_id,payload,observation_layer "
            "FROM events WHERE event_type='direct_task.recorded' AND work_item_id=ANY(%s) "
            "AND occurred_at<=%s ORDER BY work_item_id,occurred_at DESC,event_id DESC",
            (list(work_ids), as_of)).fetchall()
        bridge = {r["work_item_id"] for r in cursor.execute("SELECT DISTINCT work_item_id "
            "FROM calls WHERE work_item_id=ANY(%s) AND created_at<=%s UNION "
            "SELECT DISTINCT work_item_id FROM delegation_decisions WHERE work_item_id=ANY(%s) "
            "AND decided_at<=%s", (list(work_ids), as_of, list(work_ids), as_of))}
    result = {identifier: {"execution_path": "bridge" if identifier in bridge else "unknown"}
              for identifier in work_ids}
    for row in direct:
        report = row["payload"]["report"]
        result[row["work_item_id"]] = {"execution_path": "direct_codex",
            "bypass_reason": report["bypass_reason"], "bypass_reason_text": report["reason"],
            "requested_model": report.get("requested_model"), "reported_model": None,
            "direct_execution": {**report, "metadata_provenance":
                "desktop_hook" if row['observation_layer'] == 'codex_desktop_hook' else "caller_reported"}}
    return result


def direct_usage(conn, execution_id):
    with conn.cursor(row_factory=dict_row) as cursor:
        return [dict(row) for row in cursor.execute("SELECT observation_id,scope,is_cumulative,"
            "input_tokens,output_tokens,cache_read_input_tokens,cache_creation_input_tokens,"
            "provenance,price_basis,source_turn_id,evidence FROM usage_observations "
            "WHERE direct_execution_id=%s ORDER BY source_turn_id,observation_id", (execution_id,))]
