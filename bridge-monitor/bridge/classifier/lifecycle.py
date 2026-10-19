"""Project-local start/status/stop for the portable Ollama serving the classifier.

Process-scoped environment only: no PATH, driver, system Ollama or startup changes.
A process is stopped only when a launcher receipt matches its PID, executable and
creation time exactly (100 ns precision).
"""

import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from bridge.classifier import winproc
from bridge.classifier.config import RuntimeConfig

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
GPU_LINE = re.compile(r'msg="inference compute".*')
KV = re.compile(r'(\w+)=("[^"]*"|\S+)')

EXIT = {"ready": 0, "problem": 1, "up_gpu_not_demonstrated": 2, "stopped": 3,
        "unowned_listener": 4, "refused": 5}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def manifest_path(cfg: RuntimeConfig) -> Path:
    name, _, tag = cfg.model.partition(":")
    return cfg.path("models_dir") / "manifests" / "registry.ollama.ai" / "library" / name / (tag or "latest")


def _write_new(path: Path, data: dict) -> None:
    """Exclusive create so an existing receipt or log is never overwritten."""
    with open(path, "x", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


class RuntimeController:
    def __init__(self, cfg: RuntimeConfig):
        self.cfg = cfg
        self.state_dir = cfg.path("state_dir")
        self.log_dir = cfg.path("log_dir")
        self.exe = cfg.path("runtime_dir") / "ollama.exe"
        self.current = self.state_dir / "current.json"
        self.http = httpx.Client(timeout=5.0, trust_env=False)

    # -- receipt and identity -------------------------------------------------
    def read_receipt(self) -> dict | None:
        try:
            return json.loads(self.current.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def verify_owner(self, receipt: dict | None) -> tuple[str, winproc.ProcessIdentity | None]:
        """owned | no_receipt | not_running | identity_mismatch"""
        if not receipt:
            return "no_receipt", None
        live = winproc.identify(int(receipt["pid"]))
        if live is None:
            return "not_running", None
        if (not winproc.same_image(live.image_path, receipt["executable"])
                or not winproc.same_image(live.image_path, str(self.exe))
                or live.created_ticks != int(receipt["created_ticks"])):
            return "identity_mismatch", live
        return "owned", live

    def _archive(self, receipt: dict, prefix: str, extra: dict | None = None) -> Path:
        target = self.state_dir / f"{prefix}-{receipt['launch_id']}.json"
        _write_new(target, {**receipt, **(extra or {})})
        self.current.unlink()
        return target

    # -- start ---------------------------------------------------------------
    def preflight(self) -> list[str]:
        problems = []
        if not self.exe.is_file():
            problems.append("runtime_missing")
        elif _sha256(self.exe) != self.cfg.runtime_sha256:
            problems.append("runtime_digest_mismatch")
        manifest = manifest_path(self.cfg)
        if not manifest.is_file():
            problems.append("model_manifest_missing")
        elif _sha256(manifest) != self.cfg.manifest_sha256:
            problems.append("model_manifest_digest_mismatch")
        return problems

    def environment(self, profile_dir: Path) -> dict:
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("OLLAMA_")}
        env.update({
            "USERPROFILE": str(profile_dir), "HOME": str(profile_dir),
            "OLLAMA_HOST": f"{self.cfg.host}:{self.cfg.port}",
            "OLLAMA_MODELS": str(self.cfg.path("models_dir")),
            "OLLAMA_NUM_PARALLEL": "1", "OLLAMA_MAX_LOADED_MODELS": "1",
            "OLLAMA_MAX_QUEUE": str(self.cfg.max_queue),
            "OLLAMA_CONTEXT_LENGTH": str(self.cfg.context_length),
            "OLLAMA_KEEP_ALIVE": self.cfg.keep_alive, "OLLAMA_NO_CLOUD": "1",
            "OLLAMA_DEBUG": "1" if self.cfg.debug_logging else "0"})
        return env

    def start(self, *, warm: bool = False) -> dict:
        receipt = self.read_receipt()
        owner, _ = self.verify_owner(receipt)
        if owner == "owned":
            return {"result": "already_running", "pid": receipt["pid"], "exit": EXIT["ready"]}
        if owner == "identity_mismatch":
            return {"result": "refused", "reason": "receipt_identity_mismatch",
                    "detail": "a different process now holds the recorded PID", "exit": EXIT["refused"]}
        if winproc.port_open(self.cfg.host, self.cfg.port):
            return {"result": "refused", "reason": "port_occupied_by_unowned_process",
                    "port": self.cfg.port, "exit": EXIT["unowned_listener"]}
        if receipt:  # dead owner: keep the evidence, free the slot
            self._archive(receipt, "stale", {"archived_at": _now()})
        problems = self.preflight()
        if problems:
            return {"result": "refused", "reason": "preflight_failed", "problems": problems,
                    "exit": EXIT["problem"]}
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        launch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
        profile = self.state_dir / "profile"
        profile.mkdir(exist_ok=True)
        stdout = self.log_dir / f"ollama-{launch_id}.stdout.log"
        stderr = self.log_dir / f"ollama-{launch_id}.stderr.log"
        with open(stdout, "xb") as out, open(stderr, "xb") as err:
            process = subprocess.Popen(
                [str(self.exe), "serve"], cwd=str(self.exe.parent), env=self.environment(profile),
                stdin=subprocess.DEVNULL, stdout=out, stderr=err, close_fds=True,
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)
        identity = winproc.identify(process.pid)
        if identity is None or not winproc.same_image(identity.image_path, str(self.exe)):
            return {"result": "failed", "reason": "launched_process_identity_unverified",
                    "exit": EXIT["problem"]}
        receipt = {"launch_id": launch_id, "pid": identity.pid, "executable": identity.image_path,
                   "created_ticks": identity.created_ticks, "created_utc": identity.created_utc,
                   "launcher_pid": os.getpid(), "started_at": _now(),
                   "endpoint": self.cfg.endpoint, "model": self.cfg.model,
                   "manifest_sha256": self.cfg.manifest_sha256,
                   "runtime_sha256": self.cfg.runtime_sha256,
                   "stdout_log": str(stdout), "stderr_log": str(stderr),
                   "environment": {k: v for k, v in self.environment(profile).items()
                                   if k.startswith("OLLAMA_")}}
        _write_new(self.current, receipt)
        deadline = time.monotonic() + self.cfg.startup_timeout_seconds
        while time.monotonic() < deadline:
            if winproc.identify(identity.pid) is None:
                return {"result": "failed", "reason": "server_exited_during_startup",
                        "stderr_log": str(stderr), "exit": EXIT["problem"]}
            try:
                if self.http.get(f"{self.cfg.endpoint}/api/version").status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.25)
        else:
            self.stop()
            return {"result": "failed", "reason": "startup_timeout", "exit": EXIT["problem"]}
        result = {"result": "started", "pid": identity.pid, "created_utc": identity.created_utc,
                  "endpoint": self.cfg.endpoint, "stderr_log": str(stderr), "exit": EXIT["ready"]}
        if warm:
            result["warm"] = self.warm()
        return result

    def warm(self) -> dict:
        """Load the pinned model with the classifier's context so no reload happens later."""
        began = time.perf_counter()
        try:
            response = self.http.post(f"{self.cfg.endpoint}/api/generate", timeout=180.0,
                json={"model": self.cfg.model, "prompt": "", "stream": False,
                      "keep_alive": self.cfg.keep_alive,
                      "options": {"num_ctx": self.cfg.context_length}})
            ok = response.status_code == 200
        except httpx.HTTPError as exc:
            return {"loaded": False, "error": type(exc).__name__}
        return {"loaded": ok, "seconds": round(time.perf_counter() - began, 3)}

    # -- status --------------------------------------------------------------
    def log_evidence(self, receipt: dict | None) -> dict:
        if not receipt or not receipt.get("stderr_log"):
            return {"state": "no_log"}
        devices = []
        try:
            text = Path(receipt["stderr_log"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return {"state": "log_unreadable"}
        for line in text.splitlines():
            if GPU_LINE.search(line):
                pairs = {k: v.strip('"') for k, v in KV.findall(line)}
                devices.append({k: pairs.get(k) for k in
                                ("library", "compute", "description", "type", "total", "available")})
        good = [d for d in devices if d["library"] == self.cfg.expected_gpu_library
                and d["compute"] == self.cfg.expected_gpu_compute and d["type"] == "discrete"]
        return {"state": "expected_gpu_selected" if good and len(devices) == len(good)
                else ("no_inference_device_logged" if not devices else "unexpected_device"),
                "devices": devices}

    def status(self, *, probe: bool = False) -> dict:
        receipt = self.read_receipt()
        owner, live = self.verify_owner(receipt)
        listening = winproc.port_open(self.cfg.host, self.cfg.port)
        report = {"checked_at": _now(), "endpoint": self.cfg.endpoint,
                  "process": {"state": owner, "pid": receipt["pid"] if receipt else None,
                              "created_utc": live.created_utc if live else None},
                  "port": {"listening": listening}}
        if owner != "owned":
            overall = "unowned_listener" if listening else (
                "problem" if owner == "identity_mismatch" else "stopped")
            return {**report, "overall": overall, "exit": EXIT[overall]}
        from bridge.classifier.client import OllamaClassifier
        from contracts.classifier import GenerationProfile
        client = OllamaClassifier(endpoint=self.cfg.endpoint, model=self.cfg.model,
                                  expected_digest=self.cfg.manifest_sha256,
                                  profile=GenerationProfile(num_ctx=self.cfg.context_length),
                                  client=self.http)
        identity = client.check_identity()
        report["http"] = {"reachable": identity.reason != "endpoint_unavailable",
                          "runtime_version": identity.runtime_version}
        report["model"] = {"state": identity.reason, "observed_digest": identity.observed_digest}
        if probe and identity.ok:
            report["warm"] = self.warm()
        residency = client.residency()
        report["gpu"] = {"residency": residency.evidence(), "log": self.log_evidence(receipt)}
        gpu_ok = (residency.gpu_demonstrated
                  and report["gpu"]["log"]["state"] == "expected_gpu_selected")
        if not identity.ok:
            overall = "problem"
        elif gpu_ok:
            overall = "ready"
        else:
            overall = "up_gpu_not_demonstrated"
        return {**report, "overall": overall, "exit": EXIT[overall]}

    # -- stop ----------------------------------------------------------------
    def stop(self) -> dict:
        receipt = self.read_receipt()
        owner, live = self.verify_owner(receipt)
        if owner == "no_receipt":
            if winproc.port_open(self.cfg.host, self.cfg.port):
                return {"result": "refused", "reason": "listener_not_owned_by_this_launcher",
                        "exit": EXIT["unowned_listener"]}
            return {"result": "nothing_to_stop", "exit": EXIT["stopped"]}
        if owner == "identity_mismatch":
            return {"result": "refused", "reason": "pid_reused_or_executable_changed",
                    "exit": EXIT["refused"]}
        if owner == "not_running":
            path = self._archive(receipt, "stale", {"archived_at": _now()})
            return {"result": "already_stopped", "receipt": str(path), "exit": EXIT["stopped"]}
        try:  # release GPU memory on this endpoint only
            self.http.post(f"{self.cfg.endpoint}/api/generate", timeout=10.0,
                           json={"model": self.cfg.model, "keep_alive": 0})
        except httpx.HTTPError:
            pass
        runtime_dir = str(self.exe.parent)
        killed, skipped = [], []
        for pid in reversed(winproc.descendants(live.pid)):
            child = winproc.identify(pid)
            if child and os.path.normcase(child.image_path).startswith(os.path.normcase(runtime_dir)):
                winproc.terminate(pid)
                killed.append(pid)
            else:
                skipped.append(pid)
        winproc.terminate(live.pid)
        exited = winproc.wait_exit(live.pid, self.cfg.stop_timeout_seconds)
        listening = winproc.port_open(self.cfg.host, self.cfg.port)
        if not exited:
            return {"result": "failed", "reason": "process_did_not_exit", "exit": EXIT["problem"]}
        path = self._archive(receipt, "stopped", {"stopped_at": _now(), "children_terminated": killed,
                                                   "children_skipped_not_ours": skipped,
                                                   "port_listening_after": listening})
        return {"result": "stopped", "receipt": str(path), "children_terminated": killed,
                "port_listening_after": listening,
                "exit": EXIT["stopped"] if not listening else EXIT["problem"]}
