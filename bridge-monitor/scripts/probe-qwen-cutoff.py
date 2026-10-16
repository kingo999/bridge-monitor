"""Exercise the real Qwen adapter at a short shared cutoff without policy activation."""

import json
import sys
import time
import tomllib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from bridge.adapters.qwen_runtime import QwenAdapter
from contracts.work import DispatchLease


class RecordingEmitter:
    def __init__(self):
        self.events = []

    def emit(self, event_type, **kwargs):
        self.events.append(event_type.value)
        return True


def main() -> int:
    with (PROJECT / "config" / "pilot-policy.proposal.toml").open("rb") as source:
        deployment = tomllib.load(source)["deployment"]
    with (PROJECT / "config" / "runtime-capabilities.proposal.toml").open("rb") as source:
        qwen = tomllib.load(source)["qwen"]
    with httpx.Client() as client:
        adapter = QwenAdapter(client, endpoint=deployment["qwen_endpoint"],
            model=deployment["qwen_model_profile"],
            expected_digest=deployment["qwen_model_digest"],
            context_tokens=qwen["context_tokens"], output_tokens=256,
            timeout_seconds=10, keep_alive=deployment["qwen_keep_alive"],
            temperature=deployment["qwen_temperature"])
        preflight = adapter.preflight()
        if not preflight.ready:
            raise RuntimeError(f"model preflight failed: {preflight.reason}")
        emitter = RecordingEmitter()
        lease = DispatchLease(lease_id=str(uuid.uuid4()), call_id=str(uuid.uuid4()),
            owner_incarnation=str(uuid.uuid4()), fencing_token=1,
            policy_version="capability-probe")
        started = time.monotonic()
        result = adapter.run(lease,
            "Write 300 distinct numbered lines explaining basic arithmetic, one line at a time.",
            emitter, deadline_at=datetime.now(timezone.utc) + timedelta(milliseconds=300))
        elapsed = round(time.monotonic() - started, 3)
    receipt = {"model": adapter.model, "observed_digest": preflight.observed_digest,
               "state": result.state, "uncertainty": result.uncertainty,
               "elapsed_seconds": elapsed, "events": emitter.events}
    output = PROJECT / "var" / "probes" / "qwen-cutoff.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n",
                      encoding="utf-8")
    print(json.dumps(receipt, sort_keys=True))
    return 0 if (result.state == "uncertain" and
                 result.uncertainty == "request_may_have_run" and
                 "attempt.uncertain" in emitter.events and
                 "response.completed" not in emitter.events) else 1


if __name__ == "__main__":
    raise SystemExit(main())
