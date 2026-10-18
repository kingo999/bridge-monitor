"""Compare container-run candidate functions with host-held trusted test vectors."""

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


RUNNER = """import json
import runpy
import sys

requests = json.loads(sys.stdin.read())
namespace = runpy.run_path('/work/candidate.py')
results = []
for request in requests:
    results.append(namespace[request['function']](*request['args']))
with open('/tmp/result.json', 'w', encoding='utf-8') as output:
    json.dump({'results': results}, output, allow_nan=False)
"""


class VerifierSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image: str = Field(pattern=r"^python@sha256:[0-9a-f]{64}$")
    daemon_endpoint: str = Field(min_length=1)
    timeout_seconds: float = Field(default=12, gt=0, le=120)
    memory_mb: int = Field(default=256, ge=64, le=2048)
    cpus: float = Field(default=1, gt=0, le=4)
    pids_limit: int = Field(default=64, ge=16, le=256)
    max_source_bytes: int = Field(default=16384, gt=0, le=1048576)
    max_test_bytes: int = Field(default=16384, gt=0, le=1048576)
    max_tests: int = Field(default=32, ge=1, le=256)
    max_result_bytes: int = Field(default=65536, ge=1024, le=1048576)


@dataclass(frozen=True)
class VerificationResult:
    state: Literal["pass", "fail", "uncertain"]
    reason: str
    elapsed_seconds: float
    source_sha256: str
    exit_code: int | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""


@dataclass(frozen=True)
class CaseValidation:
    state: Literal["ready", "rejected"]
    reason: str
    reference: VerificationResult
    baseline: VerificationResult | None = None


class DockerVerifier:
    def __init__(self, settings: VerifierSettings, scratch_root: str | Path):
        self.settings = settings
        self.scratch_root = Path(scratch_root)

    def _inputs(self, tests: list[dict]) -> tuple[list[dict], list]:
        if not isinstance(tests, list) or not 0 < len(tests) <= self.settings.max_tests:
            raise ValueError("trusted test count outside configured bounds")
        requests = []
        expected = []
        for test in tests:
            if (not isinstance(test, dict) or set(test) != {"function", "args", "expected"}
                    or not isinstance(test["function"], str)
                    or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", test["function"])
                    or not isinstance(test["args"], list)):
                raise ValueError("invalid trusted test vector")
            requests.append({"function": test["function"], "args": test["args"]})
            expected.append(test["expected"])
        encoded = json.dumps(tests, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > self.settings.max_test_bytes:
            raise ValueError("trusted tests too large")
        return requests, expected

    def _local_daemon(self) -> bool:
        if os.environ.get("DOCKER_HOST") or os.environ.get("DOCKER_CONTEXT"):
            return False
        try:
            process = subprocess.run(["docker", "context", "inspect", "--format",
                "{{.Endpoints.docker.Host}}"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=5)
            return process.returncode == 0 and process.stdout.strip() == self.settings.daemon_endpoint
        except (OSError, subprocess.TimeoutExpired):
            return False

    def verify(self, source: str, tests: list[dict]) -> VerificationResult:
        if not isinstance(source, str):
            raise ValueError("source must be text")
        try:
            source_bytes = source.encode("utf-8")
        except UnicodeEncodeError:
            return VerificationResult("fail", "invalid_utf8_source", 0, "")
        digest = hashlib.sha256(source_bytes).hexdigest()
        if len(source_bytes) > self.settings.max_source_bytes:
            return VerificationResult("fail", "source_too_large", 0, digest)
        requests, expected = self._inputs(tests)
        started = time.monotonic()
        if not self._local_daemon():
            return VerificationResult("uncertain", "daemon_endpoint_mismatch",
                round(time.monotonic() - started, 3), digest)
        self.scratch_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="verify-", dir=self.scratch_root,
                                         ignore_cleanup_errors=True) as folder:
            mount = Path(folder)
            if "," in str(mount.resolve()):
                return VerificationResult("uncertain", "unsupported_mount_path", 0, digest)
            (mount / "candidate.py").write_bytes(source_bytes)
            (mount / "runner.py").write_text(RUNNER, encoding="utf-8")
            result_path = mount / "result.json"
            result_path.write_bytes(b"")
            name = "bridge-shadow-" + uuid.uuid4().hex
            memory = f"{self.settings.memory_mb}m"
            command = ["docker", "run", "-i", "--name", name, "--pull", "never",
                "--network", "none", "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--pids-limit",
                str(self.settings.pids_limit), "--memory", memory,
                "--memory-swap", memory, "--cpus", str(self.settings.cpus),
                "--log-driver", "local", "--log-opt", "max-size=1m",
                "--log-opt", "max-file=1", "--log-opt", "compress=false",
                "--user", "65534:65534", "--ulimit", "nofile=64:64",
                "--ulimit", f"fsize={self.settings.max_result_bytes}:{self.settings.max_result_bytes}",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
                "--mount", f"type=bind,src={mount.resolve()},dst=/work,readonly",
                "--mount", f"type=bind,src={result_path.resolve()},dst=/tmp/result.json",
                "--workdir", "/work", self.settings.image,
                "python", "-I", "-B", "/work/runner.py"]
            run_result = None
            log_tail = ""
            run_reason = None
            cleanup_ok = False
            try:
                try:
                    run_result = subprocess.run(command,
                        input=json.dumps(requests, ensure_ascii=False, allow_nan=False),
                        text=True, encoding="utf-8", errors="replace",
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=self.settings.timeout_seconds)
                    if run_result.returncode != 0:
                        logs = subprocess.run(["docker", "logs", "--tail", "20", name],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=5)
                        log_tail = (logs.stdout + logs.stderr)[-2048:]
                except subprocess.TimeoutExpired:
                    run_reason = "container_timeout"
                except OSError as exc:
                    run_reason = type(exc).__name__
            finally:
                try:
                    cleanup = subprocess.run(["docker", "rm", "-f", name],
                        capture_output=True, timeout=5)
                    cleanup_ok = cleanup.returncode == 0
                except (OSError, subprocess.TimeoutExpired):
                    cleanup_ok = False
            elapsed = round(time.monotonic() - started, 3)
            if not cleanup_ok:
                return VerificationResult("uncertain", "container_cleanup_failed", elapsed, digest)
            if run_reason:
                return VerificationResult("uncertain", run_reason, elapsed, digest)
            if run_result is None or run_result.returncode in (125, 126, 127):
                return VerificationResult("uncertain", "container_unavailable", elapsed,
                    digest, run_result.returncode if run_result else None)
            if run_result.returncode != 0:
                return VerificationResult("fail", "candidate_error", elapsed, digest,
                    run_result.returncode, stderr_tail=log_tail)
            if not result_path.exists() or result_path.stat().st_size == 0:
                return VerificationResult("fail", "missing_trusted_runner_result", elapsed, digest)
            if result_path.stat().st_size > self.settings.max_result_bytes:
                return VerificationResult("fail", "result_too_large", elapsed, digest)
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
                if not isinstance(result, dict) or set(result) != {"results"}:
                    raise ValueError("invalid result shape")
                observed = result["results"]
                if not isinstance(observed, list) or len(observed) != len(expected):
                    raise ValueError("incomplete result list")
                passed = all(json.dumps(actual, sort_keys=True, allow_nan=False) ==
                             json.dumps(want, sort_keys=True, allow_nan=False)
                             for actual, want in zip(observed, expected))
            except (UnicodeError, ValueError, TypeError):
                return VerificationResult("fail", "invalid_trusted_runner_result", elapsed, digest)
            return VerificationResult("pass" if passed else "fail",
                "tests_passed" if passed else "tests_failed", elapsed, digest, 0)

    def validate_case(self, baseline_source: str, reference_source: str,
                      tests: list[dict]) -> CaseValidation:
        reference = self.verify(reference_source, tests)
        if reference.state != "pass":
            return CaseValidation("rejected", "reference_not_passing", reference)
        baseline = self.verify(baseline_source, tests)
        if baseline.state != "fail":
            return CaseValidation("rejected", "baseline_not_failing", reference, baseline)
        return CaseValidation("ready", "trusted_tests_distinguish_fix", reference, baseline)
