import json
from types import SimpleNamespace
import subprocess
import zipfile

import pytest

from bridge.repository import RepositoryPolicy, RepositoryTools


def test_unpinned_grants_keep_existing_lease_identity():
    # A parent already running at upgrade time hashes the original grant shape.
    assert "release_rehearsal_commit" not in RepositoryPolicy().model_dump()
    assert RepositoryPolicy(release_rehearsal_commit="a"*40).model_dump()["release_rehearsal_commit"] == "a"*40


def test_release_command_needs_host_pin_and_separate_task_opt_in(tmp_path):
    prefix = "mri/mri-java-services-4/.security-ai-rehearsal/"
    configured = dict(enabled=True, roots={"ideaprojects": str(tmp_path)},
                      write_prefixes={"ideaprojects": ["mri/"]})
    context = dict(project_root=str(tmp_path), repository_ids="ideaprojects",
                   repository_access="read-write",
                   repository_write_prefixes=json.dumps({"ideaprojects": [prefix]}))
    def command(pin="", opt_in="", tests=""):
        policy = RepositoryPolicy(**configured, release_rehearsal_commit=pin)
        task = SimpleNamespace(scope=["repository-execution"],
                               context={**context, "release_rehearsal": opt_in})
        tools = RepositoryTools(policy.for_task(task), lambda: True)
        return tools.build_command("ideaprojects", "security-ai-release", "rehearse", tests)

    for pin, opt_in, tests in [("", "true", ""), ("a"*40, "", ""), ("a"*40, "true", "extra")]:
        with pytest.raises((ValueError, PermissionError), match="rehearsal"):
            command(pin, opt_in, tests)
    assert command("a"*40, "true")[-1] == "a"*40
    context["repository_write_prefixes"] = '{"ideaprojects":["mri/"]}'
    with pytest.raises((ValueError, PermissionError), match="rehearsal"):
        command("a"*40, "true")


def test_scratch_is_exclusive_and_restored_on_failure(tmp_path):
    from bridge.release_rehearsal import scratch_checkout
    reserved = tmp_path / ".security-ai-rehearsal"
    with pytest.raises(RuntimeError, match="synthetic failure"):
        with scratch_checkout(tmp_path) as work:
            (work / "probe").write_text("synthetic")
            raise RuntimeError("synthetic failure")
    assert not reserved.exists()
    reserved.mkdir()
    sentinel = reserved / "owned-by-someone-else"
    sentinel.write_text("keep")
    with pytest.raises(FileExistsError):
        with scratch_checkout(tmp_path):
            pytest.fail("pre-existing path must be refused")
    assert sentinel.read_text() == "keep"


@pytest.mark.parametrize("damage", [False, True])
def test_local_release_fixture_runs_both_paths_and_preserves_target(tmp_path, monkeypatch, damage):
    from bridge import release_rehearsal as runner

    def seed(root, files):
        root.mkdir(parents=True)
        for name, contents in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents, encoding="utf-8", newline="\n")
        for args in (["init", "-q"], ["add", "."], ["-c", "user.name=Test", "-c",
                     "user.email=test@invalid", "commit", "-qm", "fixture"]):
            subprocess.run(["git", "-c", "core.hooksPath=" + str(tmp_path / "no-hooks"),
                            *args], cwd=root, check=True, capture_output=True)
        return runner.head(root)

    # Only synthetic release tools execute. Real release blobs are never fixture inputs.
    packager = """
from pathlib import Path
import hashlib, json, subprocess, zipfile
commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
folder = Path('pt/releases') / commit
folder.mkdir(parents=True)
archive = folder / 'security-ai-v1.1.0.zip'
with zipfile.ZipFile(archive, 'w') as z:
    z.write('security-ai/VERSION')
    z.writestr('security-ai/example.md', 'changed in minor version')
(folder / 'RELEASE.json').write_text(json.dumps(dict(source_commit=commit, version='1.1.0',
    archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())))
(folder / (archive.name + '.sha256')).write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+chr(10))
"""
    installer = """
import argparse, hashlib, zipfile
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('action', choices=['apply'])
p.add_argument('--zip', type=Path, required=True)
p.add_argument('--target', type=Path, required=True)
p.add_argument('--sha256', required=True)
p.add_argument('--baseline-zip', type=Path)
p.add_argument('--baseline-sha256')
a = p.parse_args()
assert hashlib.sha256(a.zip.read_bytes()).hexdigest() == a.sha256
upgrading = (a.target / 'security-ai/VERSION').is_file()
assert upgrading == bool(a.baseline_zip)
if upgrading:
    assert hashlib.sha256(a.baseline_zip.read_bytes()).hexdigest() == a.baseline_sha256
with zipfile.ZipFile(a.zip) as z:
    z.extractall(a.target)  # Synthetic fixture members only.
"""
    if damage:
        installer += "\nif upgrading: (a.target / 'pt/campaigns/fixture/state.json').write_text('replaced')\n"
    source, target = tmp_path / runner.SOURCE, tmp_path / runner.TARGET
    version = seed(source, {".gitignore": "pt/\n", "security-ai/VERSION": "1.1.0",
                   "security-ai/tools/security_ai_distribution.py": packager,
                   "security-ai/tools/test_release_install.py": "import unittest\nclass Suite(unittest.TestCase):\n    def test_fixture(self): self.assertTrue(True)",
                   "security-ai/tools/release_install.py": installer})
    target_head = seed(target, {"application.md": "keep"})
    subprocess.run(["git", "config", "--local", "core.hooksPath", ".githooks"], cwd=target, check=True)
    baseline = source / "pt/releases" / runner.BASE_COMMIT / "security-ai-v1.0.0.zip"
    baseline.parent.mkdir(parents=True)
    with zipfile.ZipFile(baseline, "w") as z:
        z.writestr("security-ai/VERSION", "1.0.0")
        z.writestr("security-ai/example.md", "previous version")
    monkeypatch.setattr(runner, "BASE_SHA", runner.digest(baseline))
    if damage:
        with pytest.raises(ValueError, match="altered existing project or campaign"):
            runner.rehearse(tmp_path, version)
    else:
        result = runner.rehearse(tmp_path, version, tmp_path / "receipt")
        assert result["cleaned"] and result["target"] == target_head
        assert result["baseline_files"] == result["upgrade_files"] == result["fresh_files"] == 2
        assert runner.digest(tmp_path / "receipt/security-ai-v1.1.0.zip") == result["candidate_sha256"]
    assert (target / "application.md").read_text() == "keep"
    assert not (target / ".security-ai-rehearsal").exists()
