"""Launch the explicitly requested real Claude hop with v2-only MCP configuration."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

base = Path(__file__).resolve().parent.parent
evidence = base / 'evidence'
setup = json.loads((evidence / 'setup.json').read_text(encoding='utf-8'))
argv = [r'C:\Users\kian\.local\bin\claude.exe', '--print', '--verbose',
        '--output-format', 'stream-json', '--restricted', '--strict-mcp-config',
        '--mcp-config', str(evidence / 'mcp-v2-only.json'),
        '--tools', 'Read,Write', '--allowedTools', 'Read,Write,mcp__gpt-architect__local_worker',
        '--disallowedTools', 'mcp__gpt-architect__consult_architect,mcp__gpt-architect__review_implementation',
        '--permission-mode', 'dontAsk', '--permission-prompts', 'none',
        '--disable-slash-commands', '--no-chrome', '--session-id', setup['session_id'],
        '--name', 'LOCAL-00 GPT-Claude-Skynet synthetic test']
if setup['configured_claude_model']:
    argv.extend(['--model', setup['configured_claude_model']])
environment = dict(os.environ)
environment['CLAUDE_PROJECT_DIR'] = str(base / 'workspace')
started = time.time()
record = {'argv': argv, 'cwd': str(base / 'workspace'), 'session_id': setup['session_id'],
          'started_utc_epoch': started, 'v1_excluded_by_strict_mcp_config': True}
with (evidence / 'claude-stream.jsonl').open('wb') as stdout, (evidence / 'claude-stderr.txt').open('wb') as stderr:
    process = subprocess.Popen(argv, cwd=base / 'workspace', env=environment,
                               stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    record['pid'] = process.pid
    (evidence / 'launch.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    print(json.dumps({'claude_pid': process.pid, 'session_id': setup['session_id'],
                      'stream_log': str(evidence / 'claude-stream.jsonl')}), flush=True)
    try:
        process.communicate((evidence / 'gpt-to-claude-prompt.txt').read_bytes(), timeout=600)
    except subprocess.TimeoutExpired:
        record['timed_out'] = True
        process.terminate()
        process.communicate(timeout=15)
    record['exit_code'] = process.returncode
record['elapsed_s'] = time.time() - started
for name in ['claude-stream.jsonl', 'claude-stderr.txt']:
    record[name + '_sha256'] = hashlib.sha256((evidence / name).read_bytes()).hexdigest()
(evidence / 'launch.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'exit_code': process.returncode, 'elapsed_s': record['elapsed_s'],
                  'timed_out': record.get('timed_out', False)}), flush=True)
raise SystemExit(process.returncode or int(record.get('timed_out', False)))
