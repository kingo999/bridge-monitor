"""Content-addressed pre-solution test and baseline registration."""

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from bridge.code_fix_verifier import DockerVerifier
from contracts.router import CaseReceipt, RouterCase, RouterPolicy


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _case_root(project_root: Path) -> Path:
    project = project_root.resolve(strict=True)
    root = project / "var" / "router" / "cases"
    root.mkdir(parents=True, exist_ok=True)
    if not root.resolve().is_relative_to(project):
        raise ValueError("case storage escaped project root")
    return root


def load_case(project_root: Path, verifier_ref: str) -> RouterCase:
    if not re.fullmatch(r"[0-9a-f]{64}", verifier_ref):
        raise ValueError("invalid verifier reference")
    path = _case_root(Path(project_root)) / f"{verifier_ref}.json"
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != verifier_ref:
        raise ValueError("case digest mismatch")
    return RouterCase.model_validate_json(raw)


def register_case(project_root: Path, case: RouterCase,
                  policy: RouterPolicy) -> CaseReceipt:
    if policy.verifier is None:
        raise ValueError("pinned verifier required")
    if case.solution_available_at and case.solution_available_at <= case.captured_at:
        raise ValueError("solution existed at capture")
    if case.captured_at > datetime.now(timezone.utc):
        raise ValueError("future case capture is invalid")
    if len(case.baseline_source.encode("utf-8")) > policy.max_source_bytes:
        raise ValueError("baseline exceeds configured source limit")
    verifier = DockerVerifier(policy.verifier, Path(project_root) / "var" / "router" / "verify")
    baseline = verifier.verify(case.baseline_source, case.tests)
    if baseline.state != "fail" or baseline.reason != "tests_failed":
        raise ValueError("baseline must fail trusted tests")
    raw = _canonical(case.model_dump(mode="json"))
    digest = hashlib.sha256(raw).hexdigest()
    root = _case_root(Path(project_root))
    target = root / f"{digest}.json"
    if target.exists():
        load_case(Path(project_root), digest)
    else:
        with tempfile.NamedTemporaryFile(mode="wb", prefix="case-", suffix=".tmp",
                                         dir=root, delete=False) as tmp:
            pending = Path(tmp.name)
            tmp.write(raw)
            tmp.flush()
            os.fsync(tmp.fileno())
        try:
            try:
                pending.rename(target)
            except FileExistsError:
                load_case(Path(project_root), digest)
        finally:
            pending.unlink(missing_ok=True)
    return CaseReceipt(verifier_ref=digest,
        baseline_sha256=hashlib.sha256(case.baseline_source.encode("utf-8")).hexdigest(),
        tests_sha256=hashlib.sha256(_canonical(case.tests)).hexdigest(),
        verifier_image=policy.verifier.image, captured_at=case.captured_at)
