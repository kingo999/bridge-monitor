"""Durable, content-free job receipts for the implementer bridge.

One schema-versioned JSON file per job under a local, ignored store. Only allowlisted
fields are ever written: identities, timestamps, sanitized outcome codes, digests and
numeric counters. Prompts, results, stdout/stderr and denied tool input never reach this
module's records. The integrity value detects accidental corruption and partial edits; it
is not authentication. A receipt is recorded evidence, not approval, review or acceptance.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import stat
import time
import uuid
from pathlib import Path

import source_snapshot as source

SCHEMA = 1
MAX_RECEIPT_BYTES = 512 * 1024
MAX_REVIEWS = 20
MAX_CONCERNS = 50
MAX_VERIFICATIONS = 20
MAX_COUNT = 10 ** 12
SCAN_LIMIT = 200
STORE_NAME = '.receipts'

STATES = frozenset({'running', 'completed', 'needs_attention', 'failed', 'timed_out', 'interrupted'})
TERMINAL_STATES = STATES - {'running'}
OUTCOME_CODES = frozenset({'success', 'cli_error', 'no_valid_result', 'nonzero_exit', 'permission_denials',
                           'timeout', 'bridge_exception', 'cli_unavailable', 'interrupted',
                           'review_source_changed', 'review_evidence_unavailable'})
NOTES = frozenset({'interrupted_owner_not_running', 'source_evidence_unavailable'})
TOOL_CATEGORIES = ('Read', 'Edit', 'Write', 'Glob', 'Grep', 'Bash', 'other')
VERDICTS = frozenset({'no_blocking_concerns', 'changes_requested', 'inconclusive'})
DISPOSITIONS = frozenset({'open', 'fixed', 'accepted_risk', 'not_applicable', 'deferred'})
VERIFICATION_OUTCOMES = frozenset({'passed', 'failed', 'not_run', 'denied'})
USAGE_FIELDS = ('input_tokens', 'output_tokens', 'cache_creation_input_tokens',
                'cache_read_input_tokens', 'num_turns', 'duration_ms', 'duration_api_ms')

# `\Z`, not `$`: a trailing newline must never validate.
_JOB_ID = re.compile(r'^[a-z0-9][a-z0-9_-]{0,63}\Z')
_FILE_NAME = re.compile(r'^([a-z0-9][a-z0-9_-]{0,63})\.json\Z')
_LOCK_NAME = re.compile(r'^[a-z0-9][a-z0-9_-]{0,31}\Z')
_TOKEN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')
_CONCERN_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,31}\Z')
_REVIEWER = re.compile(r'^[A-Za-z0-9][A-Za-z0-9 ._:-]{0,63}\Z')
_MODEL = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,79}\Z')
_HEX64 = re.compile(r'^[0-9a-f]{64}\Z')
_REVISION = re.compile(r'^([0-9a-f]{40}|[0-9a-f]{64})\Z')
_CONTROL = re.compile(r'[\x00-\x1f\x7f]')
_CREDENTIAL_WORD = (r'(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|authorization|bearer'
                    r'|credential|private[_-]?key)')
_SENSITIVE = re.compile(
    r'(?i)(password|passwd|secret|token|api[_-]?key|authorization|bearer|credential|private[_-]?key)'
    r'\s*[:=]|sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|-----BEGIN|[A-Za-z0-9+_-]{48,}={0,2}'
    r'|://[^/\s:@]+:[^/\s@]+@|gh[pousr]_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}'
    r'|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}')
# Heuristics for caller-supplied command metadata. They reject known credential forms and
# structured or raw-output payloads; they cannot recognise every possible secret or disclosure.
_CREDENTIAL_NAME = re.compile(r'(?i)^[\w.:$-]*' + _CREDENTIAL_WORD + r'[\w.:$-]*$')
_AUTH_VALUE = re.compile(r'(?i)\b(?:bearer|authorization)\s+\S+')
_USER_PAIR = re.compile(r'(?:^|\s)(?:-u|--user)(?:\s+|=)\S+:\S+')
_QUOTED_KEY = re.compile(r'''["'][A-Za-z_][\w.-]*["']\s*:''')
_RAW_FIELD = re.compile(r'(?i)\b(?:raw_?)?(?:stdout|stderr)\b["\']?\s*[:=]|\btool_input\b|\braw_(?:output|result)\b')
_ENV_VERBS = frozenset({'export', 'set', 'setx', 'setenv', 'env', 'declare', 'typeset'})


class ReceiptError(Exception):
    """A receipt could not be validated, read or written; `code` is a sanitized reason."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def valid_session(value) -> bool:
    """A Claude session ID safe to pass as a CLI argument (never starts with '-')."""
    return isinstance(value, str) and bool(_TOKEN.match(value))


def valid_job_id(value) -> bool:
    return (isinstance(value, str) and bool(_JOB_ID.match(value))
            and not source.is_reserved_name(value))


def _structured(value: str) -> bool:
    """True for JSON-like objects/arrays and raw stdout/stderr or tool-input fields."""
    return bool(value.strip()[:1] in ('{', '[') or _QUOTED_KEY.search(value) or _RAW_FIELD.search(value))


def _credential_command(value: str) -> bool:
    """Credential names given with separate values (`export X_TOKEN value`, `--password value`)."""
    tokens = value.split()
    for index, raw in enumerate(tokens):
        name, equals, attached = raw.partition('=')
        name = re.sub(r'(?i)^\$env:', '', name.strip('"\'`;,()'))
        if not name or not _CREDENTIAL_NAME.match(name):
            continue
        if equals:
            if attached.strip('"\'`;,()'):
                return True
            continue
        previous = tokens[index - 1].lower() if index else ''
        named = name.startswith('-') or name.isupper() or previous in _ENV_VERBS
        if named and index + 1 < len(tokens):
            return True
    return False


def looks_sensitive(value: str, *, command: bool = False) -> bool:
    """True when `value` looks like a credential or a structured/raw-output payload.

    `command=True` adds the command-line forms (whitespace-separated credential names and
    values, bearer/authorization values, `-u user:pass`). Heuristic, fail-closed; it cannot
    prove a string is free of sensitive content.
    """
    if _SENSITIVE.search(value) or _structured(value):
        return True
    return command and bool(_AUTH_VALUE.search(value) or _USER_PAIR.search(value)
                            or _credential_command(value))


# ------------------------------------------------------------------- validation


def _exact(value, keys) -> dict:
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ReceiptError('malformed')
    return value


def _plain(value, limit: int, *, optional: bool = False):
    """Bounded single-line identifier-like text (paths, branches)."""
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value or len(value) > limit or _CONTROL.search(value):
        raise ReceiptError('malformed')
    return value


def _free(value, limit: int, *, command: bool = False):
    """Caller-supplied prose or commands: also refuse credential-looking or raw payloads."""
    _plain(value, limit)
    if looks_sensitive(value, command=command):
        raise ReceiptError('sensitive_metadata')
    return value


def _match(value, pattern, *, optional: bool = False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not pattern.match(value):
        raise ReceiptError('malformed')
    return value


def _identifier(value, pattern, *, optional: bool = False):
    """A pattern-matched identifier that must also not look like a credential."""
    _match(value, pattern, optional=optional)
    if value is not None and looks_sensitive(value):
        raise ReceiptError('sensitive_metadata')
    return value


def _one_of(value, allowed) -> str:
    """Membership for untrusted values: unhashable or non-string input is malformed, not a crash."""
    if not isinstance(value, str) or value not in allowed:
        raise ReceiptError('malformed')
    return value


def _count(value, *, optional: bool = False):
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_COUNT:
        raise ReceiptError('malformed')
    return value


def _time(value, *, optional: bool = False):
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReceiptError('malformed')
    if isinstance(value, float) and not math.isfinite(value):
        raise ReceiptError('malformed')
    if not 0 <= value <= MAX_COUNT:  # also keeps float() of a huge int from overflowing
        raise ReceiptError('malformed')
    return float(value)


def _paths(value) -> list:
    if not isinstance(value, list) or len(value) > source.MAX_DECLARED_PATHS:
        raise ReceiptError('malformed')
    for item in value:
        try:
            if source.safe_relative(item) != item:
                raise ReceiptError('malformed')
        except source.Unsafe:
            raise ReceiptError('malformed') from None
    return list(value)


def _snapshot(value, *, optional: bool = True, captured_only: bool = False):
    if value is None and optional:
        return None
    if isinstance(value, dict) and value.get('state') == 'unavailable' and not captured_only:
        _exact(value, ('state', 'reason'))
        _one_of(value['reason'], source.REASONS)
        return dict(value)
    keys = ('state', 'root', 'branch', 'head', 'digest', 'file_count', 'declared_paths',
            'declared_file_count', 'absent_declared')
    _exact(value, keys)
    if value['state'] != 'captured':
        raise ReceiptError('malformed')
    _plain(value['root'], source.MAX_PATH_CHARS)
    _plain(value['branch'], 200)
    _match(value['head'], _REVISION)
    _match(value['digest'], _HEX64)
    _count(value['file_count'])
    _count(value['declared_file_count'])
    _paths(value['declared_paths'])
    _paths(value['absent_declared'])
    return copy.deepcopy(value)


def _contract(value):
    if value is None:
        return None
    _exact(value, ('path', 'sha256', 'approval', 'base_revision'))
    try:
        if source.safe_relative(value['path']) != value['path']:
            raise ReceiptError('malformed')
    except source.Unsafe:
        raise ReceiptError('malformed') from None
    _match(value['sha256'], _HEX64)
    _free(value['approval'], 1000)
    _match(value['base_revision'], _REVISION)
    return dict(value)


def _outcome(value):
    if value is None:
        return None
    _exact(value, ('code', 'returncode', 'denial_count', 'denied_tools'))
    _one_of(value['code'], OUTCOME_CODES)
    code = value['returncode']
    if code is not None and (isinstance(code, bool) or not isinstance(code, int) or not -2 ** 31 <= code < 2 ** 31):
        raise ReceiptError('malformed')
    _count(value['denial_count'])
    tools = value['denied_tools']
    if not isinstance(tools, list) or len(tools) > len(TOOL_CATEGORIES):
        raise ReceiptError('malformed')
    for tool in tools:
        _one_of(tool, TOOL_CATEGORIES)
    if len(set(tools)) != len(tools):
        raise ReceiptError('malformed')
    return copy.deepcopy(value)


def _usage(value):
    _exact(value, ('reported',) + USAGE_FIELDS)
    if not isinstance(value['reported'], bool):
        raise ReceiptError('malformed')
    for name in USAGE_FIELDS:
        _count(value[name], optional=True)
    return dict(value)


def _model(value):
    _exact(value, ('reported', 'selection_verified'))
    names = value['reported']
    if not isinstance(names, list) or len(names) > 5:
        raise ReceiptError('malformed')
    for name in names:
        _identifier(name, _MODEL)
    if len(set(names)) != len(names):
        raise ReceiptError('malformed')
    if value['selection_verified'] is not False:
        raise ReceiptError('malformed')
    return copy.deepcopy(value)


def _review(value, position: int) -> dict:
    _exact(value, ('review_id', 'recorded_at', 'verdict', 'reviewer', 'receipt_state', 'source',
                   'contract_sha256', 'concerns', 'verification'))
    if value['review_id'] != f'r{position}':
        raise ReceiptError('malformed')
    _time(value['recorded_at'])
    _one_of(value['verdict'], VERDICTS)
    _one_of(value['receipt_state'], STATES)
    _identifier(value['reviewer'], _REVIEWER, optional=True)
    _snapshot(value['source'], optional=False, captured_only=True)
    _match(value['contract_sha256'], _HEX64, optional=True)
    concerns = value['concerns']
    if not isinstance(concerns, list) or len(concerns) > MAX_CONCERNS:
        raise ReceiptError('malformed')
    for item in concerns:
        _exact(item, ('id', 'disposition'))
        _identifier(item['id'], _CONCERN_ID)
        _one_of(item['disposition'], DISPOSITIONS)
    checks = value['verification']
    if not isinstance(checks, list) or len(checks) > MAX_VERIFICATIONS:
        raise ReceiptError('malformed')
    for item in checks:
        _exact(item, ('command', 'outcome', 'exit_code'))
        _free(item['command'], 300, command=True)
        _one_of(item['outcome'], VERIFICATION_OUTCOMES)
        code = item['exit_code']
        if code is not None and (isinstance(code, bool) or not isinstance(code, int) or not -255 <= code <= 255):
            raise ReceiptError('malformed')
    return copy.deepcopy(value)


RECORD_KEYS = ('schema', 'job_id', 'state', 'binding', 'instance_id', 'created_at', 'updated_at',
               'started_at', 'finished_at', 'task_id', 'contract', 'repository', 'source_paths',
               'source_before', 'source_after', 'source_at_recovery', 'session_id', 'parent_job_id',
               'outcome', 'usage', 'model', 'reviews', 'human_acceptance', 'note')


def validate_record(record) -> dict:
    """Return a validated deep copy; unknown or malformed fields are rejected, never copied.

    Only ReceiptError escapes: any malformed nested shape (unhashable enum values, wrong
    container types, oversized numbers) is reported as 'malformed', never as a crash.
    """
    try:
        return _validate_record(record)
    except ReceiptError:
        raise
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError, RecursionError, IndexError):
        raise ReceiptError('malformed') from None


def _validate_record(record) -> dict:
    value = _exact(record, RECORD_KEYS + (('execution_mode',) if 'execution_mode' in record else ()))
    if 'execution_mode' in value:
        _one_of(value['execution_mode'], ('implementation', 'review'))
    if (isinstance(value['schema'], bool) or value['schema'] != SCHEMA
            or not valid_job_id(value['job_id'])):
        raise ReceiptError('malformed')
    _one_of(value['state'], STATES)
    _one_of(value['binding'], ('bound', 'unbound'))
    _identifier(value['instance_id'], _TOKEN)
    for name in ('created_at', 'updated_at', 'started_at'):
        _time(value[name])
    _time(value['finished_at'], optional=True)
    _identifier(value['task_id'], _TOKEN, optional=True)
    contract = _contract(value['contract'])
    if (value['binding'] == 'bound') != (contract is not None and value['task_id'] is not None):
        raise ReceiptError('malformed')
    repository = _exact(value['repository'], ('root', 'branch', 'head'))
    _plain(repository['root'], source.MAX_PATH_CHARS)
    _plain(repository['branch'], 200, optional=True)
    _match(repository['head'], _REVISION, optional=True)
    _paths(value['source_paths'])
    _snapshot(value['source_before'])
    _snapshot(value['source_after'])
    _snapshot(value['source_at_recovery'])
    _identifier(value['session_id'], _TOKEN, optional=True)
    if value['parent_job_id'] is not None and not valid_job_id(value['parent_job_id']):
        raise ReceiptError('malformed')
    _outcome(value['outcome'])
    _usage(value['usage'])
    _model(value['model'])
    reviews = value['reviews']
    if not isinstance(reviews, list) or len(reviews) > MAX_REVIEWS:
        raise ReceiptError('malformed')
    for position, review in enumerate(reviews, 1):
        _review(review, position)
    if value['human_acceptance'] != {'state': 'not_recorded'}:
        raise ReceiptError('malformed')
    if value['note'] is not None:
        _one_of(value['note'], NOTES)
    if value['state'] in TERMINAL_STATES and value['finished_at'] is None:
        raise ReceiptError('malformed')
    return copy.deepcopy(value)


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def encode(record: dict) -> bytes:
    record = validate_record(record)
    body = canonical({'schema': SCHEMA, 'integrity': hashlib.sha256(canonical(record)).hexdigest(),
                      'record': record})
    if len(body) > MAX_RECEIPT_BYTES:
        raise ReceiptError('too_large')
    return body


def decode(data: bytes) -> dict:
    """Validate stored bytes; raises ReceiptError('malformed'|'unknown_schema'|'integrity_mismatch')."""
    if not isinstance(data, (bytes, bytearray)):
        raise ReceiptError('malformed')
    try:
        envelope = json.loads(bytes(data).decode('utf-8'))
    except (ValueError, RecursionError):
        raise ReceiptError('malformed') from None
    if not isinstance(envelope, dict) or set(envelope) != {'schema', 'integrity', 'record'}:
        raise ReceiptError('malformed')
    if isinstance(envelope['schema'], bool) or envelope['schema'] != SCHEMA:
        raise ReceiptError('unknown_schema')
    try:
        expected = hashlib.sha256(canonical(envelope['record'])).hexdigest()
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise ReceiptError('malformed') from None
    if not isinstance(envelope['integrity'], str) or envelope['integrity'] != expected:
        raise ReceiptError('integrity_mismatch')
    return validate_record(envelope['record'])


# ----------------------------------------------------------------------- locks


class FileLock:
    """Small cross-process exclusive lock on one byte of a file; released by the OS on exit.

    The lock file and its directory are checked for links before every open, so a linked
    lock leaf is refused rather than opened or written. `guard` (a store's revalidation) runs
    first on every attempt. These are point-in-time checks, not OS-level race immunity.
    """

    def __init__(self, path: Path, guard=None):
        self.path = str(path)
        self._guard = guard
        self._fd: int | None = None

    def try_acquire(self, attempts: int = 1, delay: float = 0.05) -> bool:
        for attempt in range(max(1, attempts)):
            if self._try():
                return True
            if attempt + 1 < attempts:
                time.sleep(delay)
        return False

    def acquire(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while not self._try():
            if time.monotonic() >= deadline:
                raise ReceiptError('lock_timeout')
            time.sleep(0.025)

    def _try(self) -> bool:
        if self._fd is not None:
            return False
        if self._guard is not None:
            self._guard()
        parent = os.path.dirname(self.path)
        if source.is_link(self.path) or (parent and source.is_link(parent)):
            raise ReceiptError('lock_unavailable')
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_BINARY', 0)
                         | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        except OSError:
            raise ReceiptError('lock_unavailable') from None
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                os.close(fd)
                raise ReceiptError('lock_unavailable')
            if os.fstat(fd).st_size == 0:
                os.write(fd, b'\0')
            os.lseek(fd, 0, os.SEEK_SET)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            if os.name == 'nt':
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            os.close(fd)

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


# ------------------------------------------------------------------------ store


class ReadResult:
    """status is 'ok', 'missing' or 'unavailable' (with a sanitized reason)."""

    def __init__(self, status: str, record: dict | None = None, reason: str | None = None):
        self.status, self.record, self.reason = status, record, reason


class ReceiptStore:
    """Atomic, validated receipt files below `base/.receipts` (never created on import)."""

    def __init__(self, base: str | os.PathLike, name: str = STORE_NAME):
        self.base = Path(base)
        try:
            self._name = source.safe_relative(name)
            self._pin = source.pin(self.base)
            self.directory = source.contained(self.base, self._name, pinned=self._pin)
        except source.Unsafe:
            raise ReceiptError('unsafe_store') from None

    def _vet(self) -> None:
        """Re-check the base and every component down to the store before an access.

        Run at each read, list, probe, lock and write boundary so a store or ancestor replaced
        by a link after construction is refused instead of followed. A point-in-time check:
        a swap between this check and the following system call is not prevented.
        """
        try:
            source.contained(self.base, self._name, pinned=self._pin)
        except source.Unsafe:
            raise ReceiptError('unsafe_store') from None

    def path_for(self, job_id: str) -> Path:
        if not valid_job_id(job_id):
            raise ReceiptError('invalid_id')
        return self.directory / (job_id + '.json')

    def exists(self) -> bool:
        try:
            self._vet()
        except ReceiptError:
            return False
        return os.path.isdir(self.directory)

    def _ensure(self) -> None:
        self._vet()
        try:
            os.makedirs(self.directory, exist_ok=True)
        except OSError:
            raise ReceiptError('write_failed') from None
        self._vet()

    def lock(self, name: str) -> FileLock:
        """`store` serializes read-modify-write; `active-job` is held while a job runs."""
        if not isinstance(name, str) or not _LOCK_NAME.match(name) or source.is_reserved_name(name):
            raise ReceiptError('invalid_lock')
        self._ensure()
        return FileLock(self.directory / (name + '.lock'), guard=self._vet)

    # -- reading

    def read(self, job_id: str) -> ReadResult:
        path = self.path_for(job_id)
        try:
            self._vet()
        except ReceiptError as exc:
            return ReadResult('unavailable', reason=exc.code)
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return ReadResult('missing')
        except OSError:
            return ReadResult('unavailable', reason='unreadable')
        if source.is_link(path) or not stat.S_ISREG(info.st_mode):
            return ReadResult('unavailable', reason='not_regular_file')
        if info.st_size > MAX_RECEIPT_BYTES:
            return ReadResult('unavailable', reason='oversized')
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(descriptor, 'rb') as handle:
                data = handle.read(MAX_RECEIPT_BYTES + 1)
        except OSError:
            return ReadResult('unavailable', reason='unreadable')
        if len(data) > MAX_RECEIPT_BYTES:
            return ReadResult('unavailable', reason='oversized')
        try:
            record = decode(data)
        except ReceiptError as exc:
            return ReadResult('unavailable', reason=exc.code)
        if record['job_id'] != job_id:
            return ReadResult('unavailable', reason='identity_mismatch')
        return ReadResult('ok', record)

    def list(self, limit: int = 20) -> dict:
        """Newest-first summaries (bounded) including unavailable receipts."""
        limit = max(1, min(int(limit), 50))
        try:
            self._vet()
        except ReceiptError as exc:
            return {'receipts': [{'state': 'unavailable', 'reason': exc.code}], 'truncated': False}
        if not os.path.isdir(self.directory):
            return {'receipts': [], 'truncated': False}
        found = []
        try:
            names = os.listdir(self.directory)
        except OSError:
            return {'receipts': [{'state': 'unavailable', 'reason': 'unreadable'}], 'truncated': False}
        for name in names:
            match = _FILE_NAME.match(name)
            if not match or source.is_reserved_name(match.group(1)):
                continue
            try:
                found.append((os.lstat(self.directory / name).st_mtime, match.group(1)))
            except OSError:
                continue
        found.sort(reverse=True)
        truncated = len(found) > SCAN_LIMIT
        items = []
        for _, job_id in found[:SCAN_LIMIT]:
            result = self.read(job_id)
            if result.status == 'ok':
                items.append(result.record)
            elif result.status == 'unavailable':
                items.append({'job_id': job_id, 'state': 'unavailable', 'reason': result.reason})
        items.sort(key=lambda item: item.get('created_at', 0), reverse=True)
        return {'receipts': items[:limit], 'truncated': truncated or len(items) > limit}

    def running_ids(self) -> list[str]:
        listing = self.list(50)
        return [item['job_id'] for item in listing['receipts'] if item.get('state') == 'running']

    # -- writing

    def create(self, record: dict) -> dict:
        """Persist a new record durably; fails if it exists or cannot be written."""
        body = encode(record)
        job_id = record['job_id']
        with self.lock('store'):
            if os.path.lexists(self.path_for(job_id)):
                raise ReceiptError('exists')
            self._write(job_id, body)
        return validate_record(record)

    def update(self, job_id: str, mutate) -> dict:
        """Read-validate-mutate-write under the store lock; `mutate` may return None for no change.

        The previous record stays authoritative if validation or the write fails. Identity
        fields and earlier reviews can never change, and a finished receipt only gains reviews.
        """
        with self.lock('store'):
            current = self.read(job_id)
            if current.status != 'ok':
                raise ReceiptError(current.reason or 'missing')
            changed = mutate(copy.deepcopy(current.record))
            if changed is None:
                return current.record
            if not isinstance(changed, dict):
                raise ReceiptError('malformed')
            changed['updated_at'] = time.time()
            changed = validate_record(changed)
            _check_transition(current.record, changed)
            self._write(job_id, encode(changed))
            return changed

    def _write(self, job_id: str, body: bytes) -> None:
        self._ensure()
        target = self.path_for(job_id)
        temporary = self.directory / f'.tmp-{job_id}-{uuid.uuid4().hex}'
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0)
                                 | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            self._vet()
            if source.is_link(target):  # never replace a link; the store never creates one
                raise ReceiptError('unsafe_store')
            for attempt in range(8):
                try:
                    os.replace(temporary, target)
                    break
                except PermissionError:
                    if attempt == 7:
                        raise
                    time.sleep(0.02)
        except (OSError, ReceiptError) as exc:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            if isinstance(exc, ReceiptError):
                raise
            raise ReceiptError('write_failed') from None
        if os.name != 'nt':
            try:
                directory = os.open(self.directory, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except OSError:
                pass


_IMMUTABLE = ('schema', 'job_id', 'binding', 'instance_id', 'created_at', 'started_at', 'task_id',
              'contract', 'repository', 'source_paths', 'source_before', 'parent_job_id',
              'human_acceptance')


def _check_transition(old: dict, new: dict) -> None:
    if old.get('execution_mode', 'implementation') != new.get('execution_mode', 'implementation'):
        raise ReceiptError('immutable_field')
    for key in _IMMUTABLE:
        if old[key] != new[key]:
            raise ReceiptError('immutable_field')
    if new['reviews'][:len(old['reviews'])] != old['reviews']:
        raise ReceiptError('immutable_review')
    if old['state'] != 'running':
        skip = {'reviews', 'updated_at'}
        if any(old[key] != new[key] for key in RECORD_KEYS if key not in skip):
            raise ReceiptError('immutable_terminal')


# ----------------------------------------------------------------- record builders


def empty_usage() -> dict:
    return {'reported': False, **{name: None for name in USAGE_FIELDS}}


def empty_model() -> dict:
    return {'reported': [], 'selection_verified': False}


def new_record(job_id: str, instance_id: str, repository: dict, *, started_at: float | None = None,
               contract: dict | None = None, task_id: str | None = None, source_paths=(),
               source_before: dict | None = None, parent_job_id: str | None = None,
               execution_mode: str = 'implementation') -> dict:
    """A running receipt; legacy callers pass no contract/task_id and are labelled unbound."""
    now = time.time()
    started = now if started_at is None else started_at
    return {'schema': SCHEMA, 'job_id': job_id, 'state': 'running', 'execution_mode': execution_mode,
            'binding': 'bound' if contract is not None else 'unbound', 'instance_id': instance_id,
            'created_at': now, 'updated_at': now, 'started_at': started, 'finished_at': None,
            'task_id': task_id, 'contract': contract,
            'repository': {'root': repository.get('root'), 'branch': repository.get('branch'),
                           'head': repository.get('head')},
            'source_paths': list(source_paths), 'source_before': source_before, 'source_after': None,
            'source_at_recovery': None, 'session_id': None, 'parent_job_id': parent_job_id,
            'outcome': None, 'usage': empty_usage(), 'model': empty_model(), 'reviews': [],
            'human_acceptance': {'state': 'not_recorded'}, 'note': None}


def _counter(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and math.isfinite(value) and value == int(value):
        value = int(value)
    if isinstance(value, int) and 0 <= value <= MAX_COUNT:
        return value
    return None


def sanitize_result(result, returncode) -> dict:
    """Extract allowlisted scalars from untrusted CLI JSON; everything else is discarded."""
    data = result if isinstance(result, dict) else {}
    session = data.get('session_id')
    # identifiers that look like credentials are dropped, never persisted
    session = (session if isinstance(session, str) and _TOKEN.match(session)
               and not looks_sensitive(session) else None)
    denials = data.get('permission_denials')
    denials = denials if isinstance(denials, list) else []
    tools = set()
    for item in denials:
        name = item.get('tool_name') if isinstance(item, dict) else None
        tools.add(name if name in TOOL_CATEGORIES[:-1] else 'other')
    usage = empty_usage()
    raw_usage = data.get('usage') if isinstance(data.get('usage'), dict) else {}
    for name in USAGE_FIELDS:
        value = _counter(raw_usage.get(name) if name in raw_usage else data.get(name))
        usage[name] = value
    usage['reported'] = any(usage[name] is not None for name in USAGE_FIELDS)
    models = []
    raw_models = data.get('modelUsage')
    for name in (raw_models if isinstance(raw_models, dict) else {}):
        if (isinstance(name, str) and _MODEL.match(name) and not looks_sensitive(name)
                and name not in models and len(models) < 5):
            models.append(name)
    code = returncode if isinstance(returncode, int) and not isinstance(returncode, bool) else None
    return {'session_id': session, 'usage': usage, 'model': {'reported': models, 'selection_verified': False},
            'denial_count': min(len(denials), MAX_COUNT), 'denied_tools': sorted(tools, key=TOOL_CATEGORIES.index),
            'returncode': code if code is None or -2 ** 31 <= code < 2 ** 31 else None}


def finish(record: dict, *, state: str, code: str, finished_at: float, source_after: dict | None,
           extracted: dict | None = None, note: str | None = None) -> dict:
    """Return `record` completed with a terminal state; call only after the post-run snapshot."""
    extracted = extracted or {}
    record['state'] = state
    record['finished_at'] = finished_at
    record['source_after'] = source_after
    record['session_id'] = extracted.get('session_id')
    record['usage'] = extracted.get('usage') or empty_usage()
    record['model'] = extracted.get('model') or empty_model()
    record['outcome'] = {'code': code, 'returncode': extracted.get('returncode'),
                         'denial_count': extracted.get('denial_count', 0),
                         'denied_tools': extracted.get('denied_tools', [])}
    record['note'] = note
    return record


def check_text(value, field: str, limit: int) -> str:
    """Single-line bounded metadata text; secret-like or raw payloads are rejected."""
    if not isinstance(value, str) or not value.strip() or len(value) > limit or _CONTROL.search(value):
        raise ValueError(f'{field} must be a single nonempty line within {limit} characters.')
    if looks_sensitive(value, command=True):
        raise ValueError(f'{field} looks like a credential or raw payload; record only a short command.')
    return value


def build_review(position: int, *, verdict, reviewer, concerns, verification, receipt_state: str,
                 reviewed_source: dict, contract_sha256: str | None, recorded_at: float) -> dict:
    """Validate caller-supplied review metadata and return an immutable review entry."""
    if not isinstance(verdict, str) or verdict not in VERDICTS:
        raise ValueError('verdict must be one of: ' + ', '.join(sorted(VERDICTS)) + '.')
    if reviewer in (None, ''):
        reviewer = None
    elif not isinstance(reviewer, str) or not _REVIEWER.match(reviewer) or looks_sensitive(reviewer):
        raise ValueError('reviewer must be a short plain label.')
    concerns = [] if concerns is None else concerns
    verification = [] if verification is None else verification
    if not isinstance(concerns, list) or len(concerns) > MAX_CONCERNS:
        raise ValueError('concerns must be a bounded list.')
    if not isinstance(verification, list) or len(verification) > MAX_VERIFICATIONS:
        raise ValueError('verification must be a bounded list.')
    clean_concerns = []
    for item in concerns:
        if not isinstance(item, dict) or set(item) != {'id', 'disposition'}:
            raise ValueError('Each concern must be exactly {id, disposition}.')
        disposition = item['disposition']
        if (not isinstance(item['id'], str) or not _CONCERN_ID.match(item['id'])
                or not isinstance(disposition, str) or disposition not in DISPOSITIONS):
            raise ValueError('A concern id or disposition is invalid.')
        clean_concerns.append({'id': item['id'], 'disposition': item['disposition']})
    clean_checks = []
    for item in verification:
        if not isinstance(item, dict) or not {'command', 'outcome'} <= set(item) <= {'command', 'outcome', 'exit_code'}:
            raise ValueError('Each verification must be {command, outcome[, exit_code]}.')
        if not isinstance(item['outcome'], str) or item['outcome'] not in VERIFICATION_OUTCOMES:
            raise ValueError('A verification outcome is invalid.')
        exit_code = item.get('exit_code')
        if exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int)
                                      or not -255 <= exit_code <= 255):
            raise ValueError('A verification exit_code is invalid.')
        clean_checks.append({'command': check_text(item['command'], 'verification command', 300),
                             'outcome': item['outcome'], 'exit_code': exit_code})
    return {'review_id': f'r{position}', 'recorded_at': recorded_at, 'verdict': verdict,
            'reviewer': reviewer, 'receipt_state': receipt_state, 'source': reviewed_source,
            'contract_sha256': contract_sha256, 'concerns': clean_concerns, 'verification': clean_checks}
