"""Explicit task-contract binding for the implementer bridge.

Supports only the CTX Markdown contract header subset documented in README.md: the first
`# ` title and `- Status:`, `- Approval:`, `- Base revision:` bullet lines before the first
`## ` heading, outside fenced blocks. Text matching is evidence that the caller's binding
agrees with the contract; it is not authentication of a human decision.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from pathlib import Path

import source_snapshot as source

MAX_CONTRACT_BYTES = 256 * 1024
MAX_APPROVAL_CHARS = 1000
KEYS = ('task_id', 'contract_path', 'contract_sha256', 'approval', 'base_revision',
        'source_paths', 'source_digest')

_TASK_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z')
_SHA256 = re.compile(r'^[0-9a-f]{64}\Z')
_REVISION = re.compile(r'^([0-9a-f]{40}|[0-9a-f]{64})\Z')
_HEADER = re.compile(r'^- (Status|Approval|Base revision):[ \t]*(.*?)[ \t]*$')
_FENCE_OPEN = re.compile(r'^ {0,3}(`{3,}|~{3,})(.*)$')
_FENCE_CLOSE = re.compile(r'^ {0,3}(`{3,}|~{3,})[ \t]*$')
_CONTROL = re.compile(r'[\x00-\x1f\x7f]')


class BindingError(ValueError):
    """The supplied contract binding was refused; the message names the field, not content."""


def parse_header(text: str) -> dict:
    """Return {'title', 'Status', 'Approval', 'Base revision'} found in the header region."""
    fields: dict[str, str] = {}
    title = None
    fence = None  # (marker character, opening length) while inside a fenced code block
    for raw in text.split('\n'):
        line = raw.rstrip('\r')
        if fence:
            # Only a line of the same character, at least as long as the opening and followed
            # by nothing but whitespace closes the block; anything else is still code.
            closing = _FENCE_CLOSE.match(line)
            if closing and closing.group(1)[0] == fence[0] and len(closing.group(1)) >= fence[1]:
                fence = None
            continue
        opening = _FENCE_OPEN.match(line)
        if opening and not (opening.group(1)[0] == '`' and '`' in opening.group(2)):
            fence = (opening.group(1)[0], len(opening.group(1)))
            continue
        if line.startswith('## '):
            break
        if title is None and line.startswith('# '):
            title = line[2:].strip()
            continue
        match = _HEADER.match(line)
        if match:
            key = match.group(1)
            if key in fields:
                raise BindingError('Contract header is ambiguous: duplicate ' + key + ' line.')
            fields[key] = match.group(2)
    return {'title': title, **fields}


def contract_bytes(root: Path, rel: str) -> bytes:
    """Read a contract below `root` with containment, link and size checks."""
    try:
        path = source.contained(root, rel)
    except source.Unsafe:
        raise BindingError('contract_path is not a safe repository-relative path.') from None
    try:
        with open(path, 'rb') as handle:
            data = handle.read(MAX_CONTRACT_BYTES + 1)
    except OSError:
        raise BindingError('The contract cannot be read.') from None
    if len(data) > MAX_CONTRACT_BYTES:
        raise BindingError('The contract exceeds the size limit.')
    return data


def contract_digest(root: Path, rel: str) -> str:
    return hashlib.sha256(contract_bytes(root, rel)).hexdigest()


def _single_line(value, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or _CONTROL.search(value):
        raise BindingError(f'{field} must be a single nonempty line within {limit} characters.')
    return value


def normalize(binding) -> dict:
    """Validate shape, types and bounds of a caller-supplied binding (no filesystem access)."""
    if not isinstance(binding, dict) or set(binding) != set(KEYS):
        raise BindingError('Contract binding must contain exactly: ' + ', '.join(KEYS) + '.')
    task_id = binding['task_id']
    if not isinstance(task_id, str) or not _TASK_ID.match(task_id):
        raise BindingError('task_id is malformed.')
    try:
        path = source.safe_relative(binding['contract_path'])
    except source.Unsafe:
        raise BindingError('contract_path is not a safe repository-relative path.') from None
    if not path.lower().endswith('.md'):
        raise BindingError('contract_path must be a Markdown file.')
    sha = binding['contract_sha256']
    if not isinstance(sha, str) or not _SHA256.match(sha):
        raise BindingError('contract_sha256 must be 64 lowercase hex characters.')
    approval = _single_line(binding['approval'], 'approval', MAX_APPROVAL_CHARS)
    revision = binding['base_revision']
    if not isinstance(revision, str) or not _REVISION.match(revision):
        raise BindingError('base_revision must be a full lowercase commit hash.')
    paths = binding['source_paths']
    if not isinstance(paths, list) or len(paths) > source.MAX_DECLARED_PATHS:
        raise BindingError('source_paths must be a bounded list.')
    clean = []
    for value in paths:
        try:
            clean.append(source.safe_relative(value))
        except source.Unsafe:
            raise BindingError('source_paths contains an unsafe path.') from None
    if len(set(clean)) != len(clean):
        raise BindingError('source_paths contains duplicates.')
    digest = binding['source_digest']
    if not isinstance(digest, str) or not _SHA256.match(digest):
        raise BindingError('source_digest must be 64 lowercase hex characters.')
    return {'task_id': task_id, 'contract_path': path, 'contract_sha256': sha, 'approval': approval,
            'base_revision': revision, 'source_paths': sorted(clean), 'source_digest': digest}


def validate(root: Path, head: str, binding) -> dict:
    """Return the normalized binding after checking contract bytes against it.

    Verifies: shape; exact SHA-256 of the contract file; UTF-8; `Status: Approved`;
    a non-placeholder `Approval:` line equal to the supplied approval; the contract's
    `Base revision:` equal to both the supplied base_revision and the checkout HEAD; and a
    title beginning with the task ID. The source digest is compared by the caller.
    """
    clean = normalize(binding)
    data = contract_bytes(root, clean['contract_path'])
    if not hmac.compare_digest(hashlib.sha256(data).hexdigest(), clean['contract_sha256']):
        raise BindingError('The contract digest does not match contract_sha256.')
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        raise BindingError('The contract is not valid UTF-8.') from None
    header = parse_header(text)
    if header.get('Status') != 'Approved':
        raise BindingError('The contract Status is not Approved; nothing was launched.')
    approval = header.get('Approval', '')
    if not approval or approval.lower() == 'pending' or approval.startswith('<'):
        raise BindingError('The contract has no recorded Approval; nothing was launched.')
    if approval != clean['approval']:
        raise BindingError('approval does not match the contract Approval line.')
    if header.get('Base revision') != clean['base_revision']:
        raise BindingError('base_revision does not match the contract Base revision line.')
    if head != clean['base_revision']:
        raise BindingError('The checkout HEAD is not the contract base revision.')
    title = header.get('title') or ''
    if not title.startswith(clean['task_id']) or (
            len(title) > len(clean['task_id']) and re.match(r'[A-Za-z0-9._-]', title[len(clean['task_id'])])):
        raise BindingError('task_id does not match the contract title.')
    return clean
