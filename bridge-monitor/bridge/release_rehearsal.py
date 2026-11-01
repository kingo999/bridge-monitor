"""Host-owned, local-only security-ai release rehearsal. Run via the leased run_checks tool."""
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path


SOURCE = "mri/mri-java-services-2"
TARGET = "mri/mri-java-services-4"
BASE_COMMIT = "15392a2395def5a6acb9d2db539a5f94a380d8a5"
BASE_SHA = "d988a33f0647c36bf0d81da6568139f4534eda36cfa8a615e6dd3ab57f1d589a"


def plain(root, relative):
    path = root
    for part in Path(relative).parts:
        if part in ("..", ".") or Path(part).anchor or ":" in part:
            raise ValueError("unsafe relative path")
        path = path / part
        entry = path.lstat()
        if stat.S_ISLNK(entry.st_mode) or getattr(entry, "st_file_attributes", 0) & 1024:
            raise ValueError("linked path refused")
    return path


@contextmanager
def scratch_checkout(target):
    reserved = target / ".security-ai-rehearsal"
    reserved.mkdir()  # Exclusive. Never adopt or clean up a prior run.
    identity = reserved.stat()
    try:
        with tempfile.TemporaryDirectory(dir=reserved) as name:
            path = Path(name)
            if plain(target, path.relative_to(target)).resolve() != path:
                raise ValueError("unsafe scratch directory")
            yield path
    finally:
        if not os.path.samestat(identity, reserved.lstat()):
            raise RuntimeError("scratch directory changed during rehearsal")
        reserved.rmdir()


def run(root, *args):
    # The release tools need only Python, local Git, and temporary storage.
    env = {k: v for k, v in os.environ.items() if k.upper() in
           {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP"}}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT="0", GIT_ALLOW_PROTOCOL="file", GIT_OPTIONAL_LOCKS="0",
               GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.hooksPath", GIT_CONFIG_VALUE_0=os.devnull)
    proc = subprocess.run(args, cwd=root, env=env, stdin=subprocess.DEVNULL, capture_output=True)
    if proc.returncode:
        raise RuntimeError(f"{Path(args[0]).name} {Path(args[-1]).name} failed (exit {proc.returncode})")
    return proc.stdout


def head(root):
    if Path(os.fsdecode(run(root, "git", "rev-parse", "--show-toplevel").strip())).resolve() != root:
        raise ValueError("expected an independent checkout")
    return run(root, "git", "rev-parse", "HEAD").decode("ascii").strip()


def clone(source, dest, expected):
    run(dest.parent, "git", "clone", "--quiet", "--no-local", "--single-branch", "--no-tags",
        "--depth=1", "--", str(source), str(dest))
    if head(dest) != expected:
        raise ValueError("checkout changed while preparing rehearsal")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def payload(target, archive, version):
    with zipfile.ZipFile(archive) as package:
        for item in package.infolist():
            path = plain(target, item.filename)
            if not path.is_file() or path.read_bytes() != package.read(item):
                raise ValueError("installed file differs from release")
        if plain(target, "security-ai/VERSION").read_text().strip() != version:
            raise ValueError("installed version differs from release")
        return len(package.infolist())


def snapshot(root):
    files = {}
    for folder, dirs, names in os.walk(root):
        if Path(folder) == root:
            dirs.remove(".git")
        for name in names:
            relative = (Path(folder) / name).relative_to(root)
            files[relative.as_posix()] = digest(plain(root, relative))
    return files


def install(dest, installer, archive, sha, baseline=None):
    previous = ["--baseline-zip", str(baseline), "--baseline-sha256", BASE_SHA] if baseline else []
    run(dest, sys.executable, "-I", "-B", str(installer), "apply",
        "--zip", str(archive), "--sha256", sha, "--target", str(dest), *previous)


def exercise(work, installer, target, target_head, baseline, archive, sha):
    counts = {}
    local_keys = run(target, "git", "config", "--local", "--name-only", "--list").lower().splitlines()
    has_hooks = b"core.hookspath" in local_keys
    if has_hooks and run(target, "git", "config", "--local", "--get", "core.hooksPath").strip() != b".githooks":
        raise ValueError("target has a hook configuration outside the rehearsal fixture")
    for case in ("fresh", "upgrade"):
        dest = work / case
        clone(target, dest, target_head)
        if has_hooks:
            run(dest, "git", "config", "--local", "core.hooksPath", ".githooks")
        initial = snapshot(dest)
        local_config = dest / ".git/config"
        config_digest = digest(local_config)
        if (dest / "pt").exists() or (dest / "security-ai").exists():
            raise ValueError("target fixture must start without PT data or an installed framework")
        if case == "upgrade":
            install(dest, installer, baseline, BASE_SHA)
            counts["baseline_files"] = payload(dest, baseline, "1.0.0")
            verify_contents(dest, initial, baseline)
            for name in ("security-ai/config/repository.json", "pt/campaigns/fixture/state.json"):
                fixture = dest / name
                fixture.parent.mkdir(parents=True, exist_ok=True)
                with fixture.open("xb") as stream:
                    stream.write(b'{"fixture":"preserve existing state"}\n')
                initial[name] = digest(fixture)
        install(dest, installer, archive, sha, baseline if case == "upgrade" else None)
        counts[case + "_files"] = payload(dest, archive, "1.1.0")
        verify_contents(dest, initial, archive)
        if digest(local_config) != config_digest or head(dest) != target_head:
            raise ValueError("installer altered target Git configuration or HEAD")
        run(dest, "git", "diff", "--cached", "--quiet")
    return counts


def verify_contents(dest, original, archive):
    after = snapshot(dest)
    if any(after.get(name) != value for name, value in original.items()):
        raise ValueError("installer altered existing project or campaign files")
    with zipfile.ZipFile(archive) as package:
        extra = after.keys() - original.keys() - set(package.namelist())
    if extra:
        raise ValueError("unexpected install footprint")


def rehearse(root, pin, receipt=None):
    if not re.fullmatch("[0-9a-f]{40}", pin):
        raise ValueError("a full candidate commit is required")
    source, target = plain(root, SOURCE), plain(root, TARGET)
    if head(source) != pin or plain(source, "security-ai/VERSION").read_text().strip() != "1.1.0":
        raise ValueError("source must be the approved 1.1.0 commit")
    target_head = head(target)
    for repo in (source, target):
        if run(repo, "git", "status", "--porcelain", "--untracked-files=all"):
            raise ValueError("both checkouts must be clean")
    baseline = plain(source, f"pt/releases/{BASE_COMMIT}/security-ai-v1.0.0.zip")
    if digest(baseline) != BASE_SHA:
        raise ValueError("baseline digest differs from approved release")
    with scratch_checkout(target) as work:
        producer = work / "producer"
        clone(source, producer, pin)
        suite = plain(producer, "security-ai/tools/test_release_install.py")
        run(producer, sys.executable, "-E", "-s", "-B", "-m", "unittest",
            "discover", "-s", str(suite.parent), "-p", suite.name)
        run(producer, sys.executable, "-E", "-s", "-B",
            "security-ai/tools/security_ai_distribution.py", "package")
        archive = plain(producer, f"pt/releases/{pin}/security-ai-v1.1.0.zip")
        metadata = json.loads((archive.parent / "RELEASE.json").read_bytes())
        sha = digest(archive)
        if (metadata["archive_sha256"], metadata["source_commit"], metadata["version"]) != (sha, pin, "1.1.0"):
            raise ValueError("candidate release metadata mismatch")
        installer = plain(producer, "security-ai/tools/release_install.py")
        results = exercise(work, installer, target, target_head, baseline, archive, sha)
        if receipt is not None:
            # Only the existing run's private Bridge log directory is passed by run_checks.
            checksum = plain(archive.parent, archive.name + ".sha256")
            if checksum.read_text(encoding="ascii").split() != [sha, archive.name]:
                raise ValueError("candidate checksum file mismatch")
            receipt.mkdir()
            for artifact in (archive, checksum, plain(archive.parent, "RELEASE.json")):
                shutil.copyfile(artifact, receipt / artifact.name)
            if digest(receipt / archive.name) != sha:
                raise ValueError("retained package differs from verified archive")
            results["archive"] = str(receipt / archive.name)
    if head(target) != target_head or run(target, "git", "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("target checkout changed during rehearsal")
    return dict(source=pin, target=target_head, baseline_sha256=BASE_SHA, candidate_sha256=sha,
                cleaned=True, **results)


if __name__ == "__main__":
    print(json.dumps(rehearse(Path.cwd().resolve(), sys.argv[1], Path(sys.argv[2])), sort_keys=True))
