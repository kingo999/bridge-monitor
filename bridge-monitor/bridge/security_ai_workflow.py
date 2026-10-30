"""Fixed campaign operations for an explicitly granted security-ai test campaign.

The repository tool executes this runner inside its existing process containment,
lease and log boundary. Raw report reads, generic commands, approval and Git writes
are deliberately absent. Canonical campaign writers own revisions and hashes.
"""
import argparse
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


ACTIONS = frozenset({'state', 'scope', 'assess', 'base-plan', 'verify', 'group-status',
                     'handover-status', 'plan-bundle'})
FINDING_FIELDS = frozenset({'pt_id', 'classification', 'final_status', 'verification_pending',
                           'reason_codes', 'evidence_availability'})
ITEM_FIELDS = frozenset({'id', 'readiness', 'reason_codes'})


def validate_request(campaign, action, payload):
    if not isinstance(campaign, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,100}', campaign):
        raise ValueError('invalid campaign identity')
    if action not in ACTIONS or not isinstance(payload, dict):
        raise ValueError('framework action or payload not allowed')
    if len(json.dumps(payload).encode()) > 32768:
        raise ValueError('framework payload exceeds budget')
    expected = ({'expected_record_sha256', 'findings', 'items'} if action == 'assess' else
                {'expected_record_sha256', 'groups'} if action == 'base-plan' else set())
    if set(payload) != expected:
        raise ValueError('unexpected framework payload fields')
    if expected and not re.fullmatch('[a-f0-9]{64}', str(payload['expected_record_sha256'])):
        raise ValueError('expected record hash required')
    if action == 'assess':
        for key, allowed, identity in [('findings', FINDING_FIELDS, 'pt_id'), ('items', ITEM_FIELDS, 'id')]:
            if not isinstance(payload[key], list) or len(payload[key]) > 100:
                raise ValueError('invalid assessment rows')
            seen = set()
            for row in payload[key]:
                if not isinstance(row, dict) or set(row)-allowed or identity not in row:
                    raise ValueError('unexpected assessment fields')
                value = row[identity]
                if not isinstance(value, str) or value in seen:
                    raise ValueError('duplicate or invalid assessment identity')
                seen.add(value)
                if key == 'findings' and row.get('final_status') in {'fixed', 'complete-external-pending'}:
                    raise ValueError('assessment cannot credit a remediation checkpoint')
                if key == 'items' and row.get('readiness') not in {None, 'not-present', 'unresolved', 'awaiting-decision'}:
                    raise ValueError('assessment cannot prepare execution')
    if action == 'base-plan' and not isinstance(payload['groups'], list):
        raise ValueError('groups must be a list')


def campaign_path(root, campaign):
    root = root.resolve(strict=True)
    target = root / 'pt' / 'campaigns' / campaign
    current = root
    for part in ('pt', 'campaigns', campaign):
        current = current / part
        if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
            raise PermissionError('linked campaign path denied')
    if not target.resolve().is_relative_to(root):
        raise PermissionError('campaign escapes repository')
    return target


def prepare_request(policy, repository, campaign, action, payload):
    if repository not in policy.roots or campaign not in policy.security_ai_campaigns.get(repository, []):
        raise PermissionError('framework campaign not granted')
    payload = {} if payload is None else payload
    validate_request(campaign, action, payload)
    root = Path(policy.roots[repository]).resolve(strict=True)
    campaign_path(root, campaign)
    return root, {'campaign': campaign, 'action': action, 'payload': payload}


def record_summary(directory, record):
    return {'record_sha256': hashlib.sha256((directory/'campaign.json').read_bytes()).hexdigest(),
            'state': record['state'], 'authorization': record['authorization'],
            'findings': record['findings'], 'planning': {
                key: record['planning'][key] for key in ('revision', 'items', 'groups', 'approval')},
            'active_checkpoint': record['active_checkpoint']}


def run(root, request):
    campaign, action, payload = request['campaign'], request['action'], request['payload']
    validate_request(campaign, action, payload)
    directory = campaign_path(root, campaign)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root/'security-ai/tools'))
    import campaign_content as content
    import campaign_execution as execution
    import campaign_base_plan as base
    import campaign_checkpoint as checkpoint
    from campaign_minimize import canonical, write_new

    directory, record = execution.load(directory, root)
    if action == 'state':
        return record_summary(directory, record)
    if action == 'scope':
        return base.scope(record)
    if action in {'assess', 'base-plan'}:
        if hashlib.sha256((directory/'campaign.json').read_bytes()).hexdigest() != payload['expected_record_sha256']:
            raise ValueError('campaign changed since review')
        if record['active_checkpoint'] is not None:
            raise ValueError('active checkpoint must be resolved')
        if base.git(root, 'status', '--porcelain=v1', '--untracked-files=all'):
            raise ValueError('committed source required for campaign publication')
    if action == 'assess':
        with checkpoint.writer_lock(directory):
            directory, record = execution.load(directory, root)
            if hashlib.sha256((directory/'campaign.json').read_bytes()).hexdigest() != payload['expected_record_sha256']:
                raise ValueError('campaign changed since review')
            findings = {row['pt_id']: row for row in record['findings']}
            items = {row['id']: row for row in record['planning']['items']}
            for update in payload['findings']:
                row = findings[update['pt_id']]
                row.update({k: v for k, v in update.items() if k != 'pt_id'})
                row['checked_at_head'] = execution.head(root)
            for update in payload['items']:
                row = items[update['id']]
                row.update({k: v for k, v in update.items() if k != 'id'})
            for row in items.values():
                row['execution_eligible'] = False
            record['progress_summary'] = {
                'head': execution.head(root),
                'counts': dict(Counter(row['final_status'] or 'pending' for row in findings.values()))}
            execution.reset(record)
            content.check_record(record, root)
            execution.refresh_snapshot(directory, record, root)
            execution.reset(record)
            execution.publish(directory, record, root)
        return record_summary(directory, record)
    if action == 'base-plan':
        prospective = copy.deepcopy(record)
        prospective['planning']['groups'] = payload['groups']
        content.check_record(prospective, root)
        marker = base.begin(directory, root=root)
        revision = marker['revision']
        base.snapshot(directory, revision, [], root=root)
        group_file = directory/'plans'/f'bridge-groups-input-revision-{revision}.json'
        data = canonical({'content_profile': content.PROFILE, 'groups': payload['groups']})
        if group_file.exists():
            if group_file.read_bytes() != data:
                raise ValueError('existing grouping input differs')
        else:
            write_new(group_file, data)
        return base.finalize(directory, group_file, revision, root=root)
    if action == 'verify':
        return execution.verify(directory, root=root)
    if action == 'group-status':
        import campaign_group_status as groups
        return groups.group_status_report(directory, source='active')
    if action in {'handover-status', 'plan-bundle'}:
        script = 'campaign_handover.py' if action == 'handover-status' else 'campaign_plan_bundle.py'
        verb = 'status' if action == 'handover-status' else 'create'
        result = subprocess.run([sys.executable, '-B', str(root/'security-ai/tools'/script), verb, str(directory)],
                                cwd=root, stdin=subprocess.DEVNULL, capture_output=True, text=True)
        return {'exit_code': result.returncode, 'output': result.stdout+result.stderr}
    raise ValueError('unsupported workflow action')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--request', required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve(strict=True)
    request = json.loads(Path(args.request).read_text(encoding='utf-8'))
    if set(request) != {'campaign', 'action', 'payload'}:
        raise ValueError('invalid workflow request')
    result = run(root, request)
    print(json.dumps(result, sort_keys=True))
    return result.get('exit_code', 0) if isinstance(result, dict) else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, PermissionError, OSError) as error:
        print('Framework operation failed: '+type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
