import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import source_snapshot as snap


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


def only_linked(*paths):
    """An `is_link` stand-in that reports exactly `paths` as linked (deterministic, no privilege)."""
    wanted = {os.path.normcase(str(path)) for path in paths}
    return lambda path: os.path.normcase(str(path)) in wanted


def git(root, *args):
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                    '-c', 'commit.gpgsign=false', *args],
                   check=True, capture_output=True, stdin=subprocess.DEVNULL)


def make_repo(parent) -> Path:
    root = Path(parent).resolve() / 'repo'
    root.mkdir()
    git(root, 'init', '-q')
    (root / '.gitignore').write_bytes(b'ignored/\n')
    (root / 'tracked.txt').write_bytes(b'one\n')
    git(root, 'add', '-A')
    git(root, 'commit', '-q', '-m', 'base')
    return root


class SafePathTests(unittest.TestCase):
    def test_accepts_portable_relative_paths(self):
        self.assertEqual(snap.safe_relative('a/b.txt'), 'a/b.txt')
        self.assertEqual(snap.safe_relative('dir\\file.txt'), 'dir/file.txt')
        self.assertEqual(snap.safe_relative('./a//b'), 'a/b')
        self.assertEqual(snap.safe_relative('with space/\u00fcn\u00ef.txt'), 'with space/\u00fcn\u00ef.txt')

    def test_rejects_traversal_rooted_drive_and_unsafe_components(self):
        for value in ('', '.', '..', 'a/../b', '../a', '/abs', '\\\\server\\share\\x', 'C:/x', 'C:rel',
                      'a:b', 'a/C:/b', 'con', 'dir/aux.txt', 'name.', 'name ', '.git/config', 'a\0b',
                      'x' * 401, 'a<b', 'a?b', None, 5):
            with self.assertRaises(snap.Unsafe, msg=repr(value)):
                snap.safe_relative(value)

    def test_contained_rejects_linked_components(self):
        with tempfile.TemporaryDirectory() as parent:
            base = Path(parent) / 'base'
            outside = Path(parent) / 'outside'
            base.mkdir()
            outside.mkdir()
            (outside / 'secret.txt').write_bytes(b'outside')
            try:
                os.symlink(outside, base / 'link', target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest('symbolic links are not permitted here')
            with self.assertRaises(snap.Unsafe):
                snap.contained(base, 'link/secret.txt')
            self.assertEqual(snap.contained(base, 'plain/file.txt'), base / 'plain' / 'file.txt')

    def test_contained_rejects_linked_component_deterministically(self):
        with tempfile.TemporaryDirectory() as parent:
            base = Path(parent) / 'base'
            (base / 'dir').mkdir(parents=True)
            with patch.object(snap, 'is_link', only_linked(base / 'dir')):
                with self.assertRaises(snap.Unsafe):
                    snap.contained(base, 'dir/file.txt')
            with patch.object(snap, 'is_link', only_linked(base)):
                with self.assertRaises(snap.Unsafe):  # a linked base itself is refused
                    snap.contained(base, 'dir/file.txt')

    def test_every_absolute_ancestor_of_the_base_is_vetted_before_resolution(self):
        with tempfile.TemporaryDirectory() as parent:
            grand = Path(parent) / 'grand'
            base = grand / 'parent' / 'base'
            base.mkdir(parents=True)
            for linked in (grand, grand / 'parent', base):
                with patch.object(snap, 'is_link', only_linked(linked)):
                    with self.assertRaises(snap.Unsafe, msg=str(linked)):
                        snap.pin(base)
                    with self.assertRaises(snap.Unsafe, msg=str(linked)):
                        snap.contained(base, 'a/b')
            with patch.object(snap, 'is_link', only_linked(Path(parent).parent)):
                with self.assertRaises(snap.Unsafe):  # ancestors above the temp directory too
                    snap.pin(base)
            self.assertEqual(snap.contained(base, 'a/b'), base / 'a' / 'b')

    def test_ancestors_are_vetted_root_to_leaf_before_descendants_are_probed(self):
        with tempfile.TemporaryDirectory() as parent:
            alias = Path(parent) / 'alias'
            base = alias / 'repo' / 'base'  # nothing here needs to exist
            seen = []

            def spy(path):
                seen.append(os.path.normcase(str(path)))
                return os.path.normcase(str(path)) == os.path.normcase(str(alias))

            with patch.object(snap, 'is_link', spy):
                with self.assertRaises(snap.Unsafe):
                    snap.pin(base)
            self.assertEqual(seen[-1], os.path.normcase(str(alias)))
            below = os.path.normcase(str(alias)) + os.sep
            self.assertEqual([path for path in seen if path.startswith(below)], [])

    def test_capture_refuses_a_root_below_a_linked_ancestor(self):
        with tempfile.TemporaryDirectory() as parent:
            root = make_repo(parent)
            with patch.object(snap, 'is_link', only_linked(Path(parent).resolve())):
                with self.assertRaises(snap.SnapshotError) as caught:
                    snap.capture(root)
            self.assertEqual(caught.exception.code, 'unsafe_path')
            self.assertEqual(snap.capture(root)['state'], 'captured')

    def test_contained_rejects_a_base_that_changed_since_it_was_pinned(self):
        with tempfile.TemporaryDirectory() as parent:
            first = Path(parent) / 'first'
            second = Path(parent) / 'second'
            first.mkdir()
            second.mkdir()
            pinned = snap.pin(first)
            snap.contained(first, 'a/b', pinned=pinned)
            with self.assertRaises(snap.Unsafe):
                snap.contained(second, 'a/b', pinned=pinned)

    def test_real_junction_or_symlink_is_a_link(self):
        with tempfile.TemporaryDirectory() as parent:
            target = Path(parent) / 'target'
            link = Path(parent) / 'link'
            target.mkdir()
            if not make_dir_link(link, target):
                self.skipTest('neither symbolic links nor junctions are permitted here')
            try:
                self.assertTrue(snap.is_link(link))
                self.assertFalse(snap.is_link(target))
                self.assertFalse(snap.is_link(Path(parent) / 'missing'))
                with self.assertRaises(snap.Unsafe):
                    snap.contained(parent, 'link/file.txt')
            finally:
                remove_link(link)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = make_repo(temporary.name)

    def digest(self, declared=(), exclude=()):
        return snap.capture(self.root, declared, exclude)['digest']

    def test_deterministic_and_content_free(self):
        (self.root / 'marker.txt').write_bytes(b'CONTENT-MARKER-12345')
        first = snap.capture(self.root)
        self.assertEqual(first, snap.capture(self.root))
        self.assertEqual(first['state'], 'captured')
        self.assertNotIn('CONTENT-MARKER', json.dumps(first))
        self.assertEqual(len(first['digest']), 64)

    def test_tracked_untracked_deleted_and_renamed_changes_alter_digest(self):
        base = self.digest()
        (self.root / 'tracked.txt').write_bytes(b'two\n')
        modified = self.digest()
        self.assertNotEqual(base, modified)
        (self.root / 'new file \u00e9.txt').write_bytes(b'x')
        untracked = self.digest()
        self.assertNotEqual(modified, untracked)
        os.rename(self.root / 'new file \u00e9.txt', self.root / 'renamed.txt')
        renamed = self.digest()
        self.assertNotEqual(untracked, renamed)
        os.unlink(self.root / 'tracked.txt')
        self.assertNotEqual(renamed, self.digest())

    def test_head_change_alters_digest_even_without_file_change(self):
        before = self.digest()
        git(self.root, 'commit', '-q', '--allow-empty', '-m', 'empty')
        self.assertNotEqual(before, self.digest())

    def test_ignored_paths_count_only_when_declared(self):
        (self.root / 'ignored').mkdir()
        (self.root / 'ignored' / 'notes.txt').write_bytes(b'a')
        undeclared = self.digest()
        declared = self.digest(['ignored/notes.txt'])
        self.assertNotEqual(undeclared, declared)
        (self.root / 'ignored' / 'notes.txt').write_bytes(b'b')
        self.assertEqual(undeclared, self.digest())
        self.assertNotEqual(declared, self.digest(['ignored/notes.txt']))
        self.assertNotEqual(declared, self.digest(['ignored']))

    def test_declared_deletion_and_absence_are_reported(self):
        (self.root / 'ignored').mkdir()
        (self.root / 'ignored' / 'a.txt').write_bytes(b'a')
        present = snap.capture(self.root, ['ignored/a.txt'])
        os.unlink(self.root / 'ignored' / 'a.txt')
        gone = snap.capture(self.root, ['ignored/a.txt'])
        self.assertEqual(present['absent_declared'], [])
        self.assertEqual(gone['absent_declared'], ['ignored/a.txt'])
        self.assertNotEqual(present['digest'], gone['digest'])

    def test_excluded_store_cannot_invalidate_its_own_snapshot(self):
        store = self.root / 'local-store'
        store.mkdir()
        before = self.digest(exclude=[store])
        (store / 'job.json').write_bytes(b'{}')
        (store / '.tmp-job-abc').write_bytes(b'partial')
        self.assertEqual(before, self.digest(exclude=[store]))
        self.assertNotEqual(before, self.digest())

    def test_nested_store_exclusion_does_not_hide_similarly_named_siblings(self):
        store = self.root / 'local' / 'store'
        store.mkdir(parents=True)
        (self.root / 'local' / 'store-other').mkdir()
        before = self.digest(exclude=[store])
        (store / 'deep').mkdir()
        (store / 'deep' / 'job.json').write_bytes(b'{}')
        self.assertEqual(before, self.digest(exclude=[store]))
        (self.root / 'local' / 'store-other' / 'x.txt').write_bytes(b'counts')
        self.assertNotEqual(before, self.digest(exclude=[store]))

    @unittest.skipUnless(os.name == 'nt', 'case-insensitive path comparison is Windows-specific')
    def test_excluded_store_matches_regardless_of_case_and_separator(self):
        store = self.root / 'Local-Store'
        store.mkdir()
        before = self.digest(exclude=[str(store).upper().replace('\\', '/')])
        (store / 'job.json').write_bytes(b'{}')
        self.assertEqual(before, self.digest(exclude=[str(store).lower()]))

    def test_tracked_file_below_a_linked_ancestor_is_refused_before_it_is_read(self):
        (self.root / 'sub').mkdir()
        (self.root / 'sub' / 'inner.txt').write_bytes(b'tracked')
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-q', '-m', 'sub')
        with patch.object(snap, 'is_link', only_linked(self.root / 'sub')):
            with patch.object(snap, 'open', create=True, side_effect=open) as opened:
                with self.assertRaises(snap.SnapshotError) as caught:
                    snap.capture(self.root)
        self.assertEqual(caught.exception.code, 'unsafe_path')
        for call in opened.call_args_list:
            self.assertNotIn('inner.txt', str(call))
        self.assertEqual(snap.capture_or_unavailable(self.root)['state'], 'captured')

    def test_tracked_file_replaced_by_a_real_link_ancestor_is_refused(self):
        (self.root / 'sub').mkdir()
        (self.root / 'sub' / 'inner.txt').write_bytes(b'tracked')
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-q', '-m', 'sub')
        outside = self.root.parent / 'outside'
        outside.mkdir()
        (outside / 'inner.txt').write_bytes(b'outside')
        (self.root / 'sub' / 'inner.txt').unlink()
        os.rmdir(self.root / 'sub')
        if not make_dir_link(self.root / 'sub', outside):
            self.skipTest('neither symbolic links nor junctions are permitted here')
        self.addCleanup(remove_link, self.root / 'sub')
        with self.assertRaises(snap.SnapshotError) as caught:
            snap.capture(self.root)
        self.assertEqual(caught.exception.code, 'unsafe_path')

    def test_missing_ancestor_is_absent_not_an_error(self):
        (self.root / 'sub').mkdir()
        (self.root / 'sub' / 'inner.txt').write_bytes(b'tracked')
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-q', '-m', 'sub')
        (self.root / 'sub' / 'inner.txt').unlink()
        os.rmdir(self.root / 'sub')
        self.assertEqual(snap.capture(self.root)['state'], 'captured')

    def test_unsafe_and_excess_declarations_fail_closed(self):
        for declared in (['../outside'], ['/abs'], ['.git/config'], ['a:b']):
            with self.assertRaises(snap.SnapshotError, msg=declared) as caught:
                snap.capture(self.root, declared)
            self.assertEqual(caught.exception.code, 'unsafe_path')
        with self.assertRaises(snap.SnapshotError):
            snap.capture(self.root, [f'f{i}' for i in range(snap.MAX_DECLARED_PATHS + 1)])

    def test_declared_link_is_refused(self):
        (self.root / 'ignored').mkdir()
        target = self.root / 'tracked.txt'
        try:
            os.symlink(target, self.root / 'ignored' / 'link.txt')
        except (OSError, NotImplementedError):
            self.skipTest('symbolic links are not permitted here')
        with self.assertRaises(snap.SnapshotError):
            snap.capture(self.root, ['ignored/link.txt'])

    def test_non_repository_is_unavailable_not_success(self):
        with tempfile.TemporaryDirectory() as plain:
            self.assertEqual(snap.capture_or_unavailable(plain),
                             {'state': 'unavailable', 'reason': 'git_failed'})

    def test_file_budget_fails_closed(self):
        original = snap.MAX_FILES
        snap.MAX_FILES = 1
        try:
            (self.root / 'second.txt').write_bytes(b'2')
            with self.assertRaises(snap.SnapshotError) as caught:
                snap.capture(self.root)
            self.assertEqual(caught.exception.code, 'too_many_files')
        finally:
            snap.MAX_FILES = original


if __name__ == '__main__':
    unittest.main()
