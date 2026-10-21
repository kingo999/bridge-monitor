"""Append-only capture coverage supplement; never modifies the frozen evaluator."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
import uuid

P=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(P))
import psycopg
from psycopg.rows import dict_row
from service.queries.classifier import ClassifierCohortFilter
from service.queries.classifier_exclusions import query_capture_exclusions


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--window',required=True,type=Path)
    args=parser.parse_args()
    window=args.window.resolve()
    if not window.is_relative_to(P/'var/shadow-checks'): raise ValueError('window_outside_project_checks')
    raw=(window/'manifest.json').read_bytes();m=json.loads(raw)
    if hashlib.sha256(raw).hexdigest()!=(window/'manifest.sha256').read_text().strip(): raise ValueError('manifest_drift')
    if hashlib.sha256((window/'monitor.py').read_bytes()).hexdigest()!=m['monitor_script_sha256']: raise ValueError('monitor_drift')
    filters=ClassifierCohortFilter(origin_kind='natural',profile_id=m['profile_id'],created_from=m['started_at'])
    dsn=json.loads((P/'var/secrets/desktop-mcp.json').read_text())['dsn']
    with psycopg.connect(dsn,row_factory=dict_row,options='-c default_transaction_read_only=on -c statement_timeout=5000',connect_timeout=5) as c:
        summary=query_capture_exclusions(c,filters,omit_admin=True)
        budget=c.execute("SELECT consumed_rows,consumed_bytes FROM classifier_hook_capture_budget WHERE scope='route_work-v1'").fetchone()
    reports=sorted((window/'runs').glob('*/summary.json'))
    latest=reports[-1] if reports else None
    result={'at':dt.datetime.now(dt.timezone.utc).isoformat(),'window_id':m['window_id'],
        'manifest_sha256':hashlib.sha256(raw).hexdigest(),'capture_exclusions':summary,
        'shared_lifetime_budget':budget,'quality_report':str(latest) if latest else None,
        'quality_report_sha256':hashlib.sha256(latest.read_bytes()).hexdigest() if latest else None,
        'interpretation':'Add these distinct explicit exclusions to captured-only coverage reporting, not to paired-quality samples. Never treat absence of a row as a successful capture. Historical counter-only drops are not retroactively fabricated.'}
    folder=window/'coverage';folder.mkdir(exist_ok=True)
    output=folder/(dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8]+'.json')
    with output.open('x',encoding='utf-8') as f:json.dump(result,f,indent=2)
    print(json.dumps({'status':'ok','evidence':str(output),'capture_exclusions':summary}))


if __name__=='__main__':main()
