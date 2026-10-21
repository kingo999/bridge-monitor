"""Separate capture-loss coverage; excluded arrivals are never model outcomes."""
from collections import Counter


def query_capture_exclusions(conn, filters, *, omit_admin=False):
    if not conn.execute("SELECT to_regclass('classifier_capture_exclusions') IS NOT NULL AS ok").fetchone()['ok']:
        return {'available':False,'total':0,'reasons':{},'size_kinds':{},'metadata_bytes':0}
    rows = conn.execute('''SELECT task_id,work_item_id,reason,size_kind,metadata_bytes
        FROM classifier_capture_exclusions
        WHERE (%s='all' OR origin_kind=%s)
          AND (%s='all' OR capture_method=%s)
          AND (%s::text IS NULL OR requested_model=%s)
          AND (%s::text IS NULL OR profile_id=%s)
          AND (%s::text IS NULL OR rubric_version=%s)
          AND (%s::timestamptz IS NULL OR captured_at>=%s)
          AND (%s::timestamptz IS NULL OR captured_at<%s)''',
        (filters.origin_kind,filters.origin_kind,filters.capture_method,filters.capture_method,
         filters.model,filters.model,filters.profile_id,filters.profile_id,
         filters.rubric_version,filters.rubric_version,filters.created_from,filters.created_from,
         filters.created_until,filters.created_until)).fetchall()
    all_count = len(rows)
    if omit_admin:
        import re
        admin = re.compile(r'^(?:gemma-(?:real-shadow-monitor|real-value-monitor|v4-postrestart|shadow-fixes|scenario|baseline|evaluation|suitability)|classifier-(?:probe|smoke|evaluation))',re.I)
        rows = [r for r in rows if not admin.search(r['task_id']) and not admin.search(r['work_item_id'])]
    return {'available':True,'total':len(rows),'admin_excluded':all_count-len(rows),
            'reasons':dict(Counter(r['reason'] for r in rows)),
            'size_kinds':dict(Counter(r['size_kind'] for r in rows)),
            'metadata_bytes':sum(r['metadata_bytes'] for r in rows),
            'note':'Metadata-only exclusions, not inference failures or quality scores. Character sizes are lower bounds; serialized UTF-8 sizes are exact. Shared lifetime quota/rate/queue limits can still drop arrivals before durable recording.'}
