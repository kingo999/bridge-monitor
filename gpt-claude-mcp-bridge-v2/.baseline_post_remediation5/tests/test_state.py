"""Registry, locks, canonical repository identity, isolation and corruption."""

from __future__ import annotations

import json
import multiprocessing as mp
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from tests.helpers import ROOT, WorldCase

from bridge import errors, state

UUID_A = "aaaaaaaa-1111-2222-3333-444444444444"
UUID_B = "bbbbbbbb-1111-2222-3333-444444444444"


def _ident(repo_key="r" * 32, model="gpt-6.1-sol", session="default", repo_path="C:\\repo"):
    return {"repo_key": repo_key, "repo_path": repo_path, "model": model, "session": session}


def _bind_worker(args):
    state_dir, n = args
    reg = state.Registry(Path(state_dir))
    ident = _ident(repo_key=f"{n:032d}")
    reg.bind_thread(state.session_key(ident["repo_key"], ident["model"], ident["session"]), ident,
                    f"{n:08d}-1111-2222-3333-444444444444", "0.162.1")
    return n


def _hold_lock(args):
    path, hold_s, ready = args
    lock = state.FileLock(Path(path))
    lock.acquire(5)
    Path(ready).write_text("1")
    time.sleep(hold_s)
    lock.release()


class CanonicalRepo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bridge_v2_repo_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def make(self, name):
        p = self.tmp / name
        (p / ".git").mkdir(parents=True)
        return p

    def test_same_repo_many_spellings_same_key(self):
        repo = self.make("Repo One")
        _, k1 = state.canonical_repo(str(repo))
        spellings = [str(repo) + os.sep, str(repo).replace("\\", "/"), str(repo / "." / "..") + "/Repo One"]
        if os.name == "nt":
            spellings += [str(repo).upper(), str(repo).lower(), "\\\\?\\" + str(repo)]
        for s in spellings:
            self.assertEqual(state.canonical_repo(s)[1], k1, s)

    @unittest.skipUnless(os.name == "nt", "directory junctions are a Windows feature")
    def test_directory_junction_resolves_to_same_repository(self):
        repo = self.make("real repo")
        link = self.tmp / "junction_to_repo"
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(repo)], capture_output=True)
        if made.returncode != 0:
            self.skipTest("cannot create junction here")
        self.assertEqual(state.canonical_repo(str(link))[1], state.canonical_repo(str(repo))[1])
        subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True)  # removes the link only

    def test_distinct_repos_distinct_keys(self):
        self.assertNotEqual(state.canonical_repo(str(self.make("a")))[1], state.canonical_repo(str(self.make("b")))[1])

    def test_non_git_directory_is_rejected(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        with self.assertRaises(errors.BridgeError) as cm:
            state.canonical_repo(str(plain))
        self.assertEqual(cm.exception.code, "not_a_repo")
        with self.assertRaises(errors.BridgeError):
            state.canonical_repo(str(self.tmp / "missing"))

    def test_git_file_worktree_is_accepted(self):
        wt = self.tmp / "wt"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: elsewhere")
        self.assertTrue(state.canonical_repo(str(wt))[1])

    def test_state_dir_must_be_outside_repo_and_v1(self):
        repo = self.make("r")
        home = self.tmp / "home"
        with self.assertRaises(errors.BridgeError) as cm:
            state.validate_state_dir(repo / ".bridge", repo, home)
        self.assertEqual(cm.exception.code, "state_in_repo")
        with self.assertRaises(errors.BridgeError) as cm:
            state.validate_state_dir(home / ".gpt-architect-bridge", repo, home)
        self.assertEqual(cm.exception.code, "state_overlaps_v1")
        with self.assertRaises(errors.BridgeError):
            state.validate_state_dir(home / ".gpt-architect-bridge" / "sub", repo, home)
        state.validate_state_dir(home / ".gpt-architect-bridge-v2", repo, home)  # ok, sibling name


class RegistryBehaviour(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bridge_v2_reg_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.reg = state.Registry(self.tmp)
        self.reg.ensure_dirs()

    def key(self, ident):
        return state.session_key(ident["repo_key"], ident["model"], ident["session"])

    def test_restart_continuity(self):
        ident = _ident()
        self.reg.bind_thread(self.key(ident), ident, UUID_A, "0.162.1")
        again = state.Registry(self.tmp)  # "restart": brand-new object, same directory
        entry = again.get(self.key(ident), ident)
        self.assertEqual((entry["thread_id"], entry["turns"]), (UUID_A, 1))
        again.bind_thread(self.key(ident), ident, UUID_A, "0.162.1")
        self.assertEqual(again.get(self.key(ident), ident)["turns"], 2)
        self.assertEqual(json.loads(self.reg.path.read_text())["schema_version"], state.SCHEMA_VERSION)

    def test_model_session_and_repo_isolation(self):
        a, b, c, d = _ident(), _ident(model="gpt-6-astra"), _ident(session="s2"), _ident(repo_key="x" * 32)
        for i, ident in enumerate((a, b, c, d)):
            self.reg.bind_thread(self.key(ident), ident, f"{i:08d}-1111-2222-3333-444444444444", None)
        got = {self.reg.get(self.key(i), i)["thread_id"] for i in (a, b, c, d)}
        self.assertEqual(len(got), 4)

    def test_rebind_to_different_thread_is_refused(self):
        ident = _ident()
        self.reg.bind_thread(self.key(ident), ident, UUID_A, None)
        with self.assertRaises(errors.BridgeError) as cm:
            self.reg.bind_thread(self.key(ident), ident, UUID_B, None)
        self.assertEqual(cm.exception.category, errors.SESSION_MISMATCH)
        self.assertEqual(self.reg.get(self.key(ident), ident)["thread_id"], UUID_A)

    def test_corrupt_registry_is_never_replaced(self):
        for payload in (b"{not json", b"", b"[]", json.dumps({"schema_version": 99, "sessions": {}}).encode()):
            self.reg.path.write_bytes(payload)
            ident = _ident()
            with self.assertRaises(errors.BridgeError) as cm:
                self.reg.get(self.key(ident), ident)
            self.assertEqual(cm.exception.category, errors.SESSION_CORRUPT)
            with self.assertRaises(errors.BridgeError):
                self.reg.bind_thread(self.key(ident), ident, UUID_A, None)
            self.assertEqual(self.reg.path.read_bytes(), payload, "corrupt file must be left byte-for-byte")

    def test_quarantine_renames_but_never_deletes(self):
        self.reg.path.write_bytes(b"{broken")
        target = self.reg.quarantine()
        self.assertTrue(target.exists())
        self.assertEqual(target.read_bytes(), b"{broken")
        self.assertFalse(self.reg.path.exists())

    def test_hand_edited_entry_mismatch_detected(self):
        ident = _ident()
        self.reg.bind_thread(self.key(ident), ident, UUID_A, None)
        data = json.loads(self.reg.path.read_text())
        data["sessions"][self.key(ident)]["model"] = "gpt-6-astra"
        self.reg.path.write_text(json.dumps(data))
        with self.assertRaises(errors.BridgeError) as cm:
            self.reg.get(self.key(ident), ident)
        self.assertEqual((cm.exception.category, cm.exception.code), (errors.SESSION_MISMATCH, "entry_mismatch"))
        data["sessions"][self.key(ident)]["model"] = ident["model"]
        data["sessions"][self.key(ident)]["thread_id"] = "--bad flag"
        self.reg.path.write_text(json.dumps(data))
        with self.assertRaises(errors.BridgeError):
            self.reg.get(self.key(ident), ident)

    def test_atomic_write_failure_keeps_previous_registry(self):
        ident = _ident()
        self.reg.bind_thread(self.key(ident), ident, UUID_A, None)
        before = self.reg.path.read_bytes()
        real_replace = os.replace

        def boom(*a, **k):
            raise PermissionError("simulated")

        state.os.replace = boom
        try:
            with self.assertRaises(errors.BridgeError) as cm:
                self.reg.bind_thread(self.key(ident), ident, UUID_A, None)
        finally:
            state.os.replace = real_replace
        self.assertEqual(cm.exception.category, errors.STATE_ERROR)
        self.assertEqual(self.reg.path.read_bytes(), before)
        self.assertEqual([p.name for p in self.tmp.glob("*.tmp")], [], "temp files must be cleaned up")

    def test_reset_retires_pointer_and_keeps_thread_id_for_audit(self):
        ident = _ident()
        self.reg.bind_thread(self.key(ident), ident, UUID_A, None)
        entry = self.reg.retire(self.key(ident), "admin reset")
        self.assertEqual(entry["thread_id"], UUID_A)
        self.assertIsNone(self.reg.get(self.key(ident), ident))
        self.assertEqual(self.reg.snapshot()["retired"][0]["thread_id"], UUID_A)

    def test_register_new_is_idempotent_and_does_not_clobber(self):
        ident = _ident(session="fresh")
        self.assertTrue(self.reg.register_new(self.key(ident), ident))
        self.assertFalse(self.reg.register_new(self.key(ident), ident))
        self.reg.bind_thread(self.key(ident), ident, UUID_A, None)
        self.assertFalse(self.reg.register_new(self.key(ident), ident))
        self.assertEqual(self.reg.get(self.key(ident), ident)["thread_id"], UUID_A)


class Concurrency(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bridge_v2_conc_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_parallel_processes_do_not_lose_registry_updates(self):
        state.Registry(self.tmp).ensure_dirs()
        n = 12
        with mp.get_context("spawn").Pool(4) as pool:
            done = pool.map(_bind_worker, [(str(self.tmp), i) for i in range(n)])
        self.assertEqual(sorted(done), list(range(n)))
        sessions = state.Registry(self.tmp).snapshot()["sessions"]
        self.assertEqual(len(sessions), n)
        json.loads((self.tmp / "registry.json").read_text())  # still valid JSON

    def test_lock_excludes_other_process_and_is_released(self):
        lock_path = self.tmp / "locks" / "x.lock"
        ready = self.tmp / "ready"
        ctx = mp.get_context("spawn")
        proc = ctx.Process(target=_hold_lock, args=((str(lock_path), 1.5, str(ready)),))
        proc.start()
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        mine = state.FileLock(lock_path)
        with self.assertRaises(state.LockTimeout):
            mine.acquire(0.3)
        mine.acquire(10)  # succeeds once the other process releases
        mine.release()
        proc.join(5)

    def test_lock_is_released_when_holder_dies(self):
        lock_path = self.tmp / "locks" / "y.lock"
        ready = self.tmp / "ready2"
        proc = mp.get_context("spawn").Process(target=_hold_lock, args=((str(lock_path), 60, str(ready)),))
        proc.start()
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        proc.kill()
        proc.join(5)
        lock = state.FileLock(lock_path)
        lock.acquire(5)  # OS released it: no stale lock
        lock.release()


if __name__ == "__main__":
    unittest.main()
