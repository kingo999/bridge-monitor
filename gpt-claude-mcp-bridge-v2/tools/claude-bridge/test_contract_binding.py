import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import contract_binding as cb

HEAD = 'a' * 40
APPROVAL = 'Kian, 2026-10-14, FLOW-01 implementation delegated to Claude Code; given in Desktop chat'
DIGEST = 'b' * 64


def make_dir_link(link, target) -> bool:
    """Create a directory symlink, or a junction on Windows; False when neither is permitted."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        pass
    if os.name == 'nt':
        done = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                              capture_output=True, stdin=subprocess.DEVNULL)
        return done.returncode == 0 and os.path.isdir(link)
    return False


def remove_link(link) -> None:
    try:
        os.rmdir(link)
    except OSError:
        try:
            os.unlink(link)
        except OSError:
            pass


def contract_text(status='Approved', approval=APPROVAL, base=HEAD, title='FLOW-01 Implementation Plan',
                  extra_header=''):
    return (f'# {title}\n\n- Status: {status}\n- Approval: {approval}\n- Base revision: {base}\n'
            f'{extra_header}\n## Objective\nText.\n')


class BindingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write(self, text, name='plan.md', data=None):
        path = self.root / name
        path.write_bytes(text.encode('utf-8') if data is None else data)
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def binding(self, sha, **changes):
        value = {'task_id': 'FLOW-01', 'contract_path': 'plan.md', 'contract_sha256': sha,
                 'approval': APPROVAL, 'base_revision': HEAD, 'source_paths': ['tools/x'],
                 'source_digest': DIGEST}
        value.update(changes)
        return value

    def refused(self, binding, head=HEAD):
        with self.assertRaises(cb.BindingError):
            cb.validate(self.root, head, binding)

    def test_approved_contract_with_matching_evidence_is_accepted(self):
        sha = self.write(contract_text())
        clean = cb.validate(self.root, HEAD, self.binding(sha))
        self.assertEqual(clean['source_paths'], ['tools/x'])
        self.assertEqual(clean['approval'], APPROVAL)

    def test_proposed_and_pending_are_refused(self):
        self.refused(self.binding(self.write(contract_text(status='Proposed'))))
        self.refused(self.binding(self.write(contract_text(approval='pending')), approval='pending'))
        self.refused(self.binding(self.write(contract_text(approval='<named human>')), approval='<named human>'))
        for status in ('Implemented', 'Reviewed', 'Rejected', 'approved'):
            self.refused(self.binding(self.write(contract_text(status=status))))

    def test_digest_approval_base_head_and_task_mismatches_are_refused(self):
        sha = self.write(contract_text())
        self.refused(self.binding('c' * 64))
        self.refused(self.binding(sha, approval='someone else, 2026-10-14'))
        self.refused(self.binding(sha, base_revision='d' * 40))
        self.refused(self.binding(sha), head='e' * 40)
        self.refused(self.binding(sha, task_id='FLOW-0'))
        self.refused(self.binding(sha, task_id='FLOW-02'))

    def test_task_id_must_end_at_a_boundary(self):
        sha = self.write(contract_text(title='FLOW-010 Something'))
        self.refused(self.binding(sha, task_id='FLOW-01'))
        sha = self.write(contract_text(title='FLOW-01: Something'))
        cb.validate(self.root, HEAD, self.binding(sha))

    def test_header_subset_is_strict(self):
        fenced = ('# FLOW-01 Plan\n\n```markdown\n- Status: Approved\n- Approval: ' + APPROVAL
                  + '\n- Base revision: ' + HEAD + '\n```\n')
        self.refused(self.binding(self.write(fenced)))
        late = '# FLOW-01 Plan\n\n## Notes\n- Status: Approved\n- Approval: ' + APPROVAL + '\n- Base revision: ' + HEAD + '\n'
        self.refused(self.binding(self.write(late)))
        duplicate = contract_text(extra_header='- Status: Approved\n')
        self.refused(self.binding(self.write(duplicate)))
        prose = '# FLOW-01 Plan\n\nThis was approved by Kian on 2026-10-14.\n'
        self.refused(self.binding(self.write(prose)))

    def test_only_a_valid_matching_closing_fence_ends_a_code_block(self):
        header = '- Status: Approved\n- Approval: ' + APPROVAL + '\n- Base revision: ' + HEAD + '\n'
        for opening, not_closing in (('```python', '```still-code'), ('````', '```'), ('```', '~~~'),
                                     ('~~~~', '~~~'), ('```', '``` trailing text')):
            text = f'# FLOW-01 Plan\n\n{opening}\ncode\n{not_closing}\n{header}\n'
            self.refused(self.binding(self.write(text)))
            self.assertEqual(cb.parse_header(text), {'title': 'FLOW-01 Plan'}, opening + ' / ' + not_closing)

    def test_valid_closing_fences_end_the_block_and_the_header_is_read(self):
        header = '- Status: Approved\n- Approval: ' + APPROVAL + '\n- Base revision: ' + HEAD + '\n'
        for opening, closing in (('```python', '```'), ('```', '````'), ('~~~', '~~~~~'),
                                 ('  ```', '```   '), ('```', '```\t')):
            text = f'# FLOW-01 Plan\n\n{opening}\ncode\n{closing}\n{header}\n## Objective\n'
            self.assertEqual(cb.parse_header(text)['Status'], 'Approved', opening + ' / ' + closing)
            cb.validate(self.root, HEAD, self.binding(self.write(text)))

    def test_backtick_info_string_with_backtick_is_not_a_fence(self):
        text = ('# FLOW-01 Plan\n\n```not`a fence\n- Status: Approved\n- Approval: ' + APPROVAL
                + '\n- Base revision: ' + HEAD + '\n')
        self.assertEqual(cb.parse_header(text)['Status'], 'Approved')

    def test_unclosed_fence_hides_the_rest_of_the_header(self):
        text = '# FLOW-01 Plan\n\n```\n- Status: Approved\n- Approval: x\n- Base revision: ' + HEAD + '\n'
        self.assertNotIn('Status', cb.parse_header(text))
        self.refused(self.binding(self.write(text)))

    def test_contract_below_a_linked_ancestor_is_refused_without_reading(self):
        (self.root / 'docs').mkdir()
        sha = self.write(contract_text(), name='docs/plan.md')
        with patch.object(cb.source, 'is_link', lambda path: os.path.normcase(str(path)) == os.path.normcase(
                str(self.root / 'docs'))):
            with patch.object(cb, 'open', create=True, side_effect=open) as opened:
                self.refused(self.binding(sha, contract_path='docs/plan.md'))
        opened.assert_not_called()
        cb.validate(self.root, HEAD, self.binding(sha, contract_path='docs/plan.md'))

    def test_contract_through_a_real_linked_directory_is_refused(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        target = Path(outside.name)
        (target / 'plan.md').write_bytes(contract_text().encode('utf-8'))
        link = self.root / 'docs'
        if not make_dir_link(link, target):
            self.skipTest('neither symbolic links nor junctions are permitted here')
        self.addCleanup(remove_link, link)
        sha = hashlib.sha256((target / 'plan.md').read_bytes()).hexdigest()
        self.refused(self.binding(sha, contract_path='docs/plan.md'))

    def test_crlf_contract_is_supported(self):
        text = contract_text().replace('\n', '\r\n')
        sha = self.write('', data=text.encode('utf-8'))
        cb.validate(self.root, HEAD, self.binding(sha))

    def test_invalid_utf8_and_oversized_contracts_are_refused(self):
        sha = self.write('', data=b'# FLOW-01\n- Status: Approved\n\xff\xfe\n')
        self.refused(self.binding(sha))
        sha = self.write('', data=b'x' * (cb.MAX_CONTRACT_BYTES + 1))
        self.refused(self.binding(sha))

    def test_paths_are_contained(self):
        sha = self.write(contract_text())
        for path in ('../plan.md', '/etc/plan.md', 'C:/plan.md', 'plan.txt', '.git/plan.md', 'a:plan.md'):
            self.refused(self.binding(sha, contract_path=path))
        self.refused(self.binding(sha, contract_path='missing.md'))
        self.refused(self.binding(sha, source_paths=['../outside']))

    def test_linked_contract_is_refused(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        target = Path(outside.name) / 'real.md'
        target.write_bytes(contract_text().encode('utf-8'))
        try:
            os.symlink(target, self.root / 'linked.md')
        except (OSError, NotImplementedError):
            self.skipTest('symbolic links are not permitted here')
        sha = hashlib.sha256(target.read_bytes()).hexdigest()
        self.refused(self.binding(sha, contract_path='linked.md'))

    def test_shape_types_and_bounds_are_enforced(self):
        sha = self.write(contract_text())
        good = self.binding(sha)
        self.refused(dict(good, extra='x'))
        self.refused({key: value for key, value in good.items() if key != 'approval'})
        self.refused('not a dict')
        for key, value in (('task_id', 5), ('task_id', '-bad'), ('contract_sha256', sha.upper()),
                           ('approval', 'two\nlines'), ('approval', ''), ('approval', 'x' * 1001),
                           ('base_revision', 'HEAD'), ('source_paths', 'tools'),
                           ('source_paths', ['a', 'a']), ('source_paths', [5]),
                           ('source_digest', 'short')):
            self.refused(dict(good, **{key: value}))
        self.refused(dict(good, source_paths=[f'p{i}' for i in range(101)]))

    def test_digest_helper_matches_file_bytes(self):
        sha = self.write(contract_text())
        self.assertEqual(cb.contract_digest(self.root, 'plan.md'), sha)
        with self.assertRaises(cb.BindingError):
            cb.contract_digest(self.root, '../plan.md')


if __name__ == '__main__':
    unittest.main()
