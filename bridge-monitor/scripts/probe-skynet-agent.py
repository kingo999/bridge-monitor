"""Bounded, model-only smoke probe for the proposed Skynet agent route."""

import argparse
import os
import sys
import tomllib
import uuid
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import httpx


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from bridge.adapters.qwen_agent import QwenAgentAdapter  # noqa: E402
from contracts.policy import PolicyInput  # noqa: E402


class Sink:
    def __init__(self):
        self.counts = Counter()

    def emit(self, kind, **_kwargs):
        self.counts[kind.value] += 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--capabilities", type=Path, required=True)
    parser.add_argument("--max-seconds", type=float, required=True)
    parser.add_argument("--max-output-tokens", type=int, required=True)
    args = parser.parse_args()
    if args.max_seconds <= 0 or args.max_output_tokens <= 0:
        raise ValueError("positive probe caps required")
    policy = PolicyInput.model_validate(tomllib.loads(args.policy.read_text(encoding="utf-8")))
    capabilities = tomllib.loads(args.capabilities.read_text(encoding="utf-8"))
    deployment = policy.deployment
    token = os.environ.get(deployment.qwen_agent_token_ref.removeprefix("env:"))
    if not token:
        raise ValueError("configured agent token unavailable")
    adapter = QwenAgentAdapter(httpx.Client(), endpoint=deployment.qwen_agent_endpoint,
        token=token, model=deployment.qwen_model_profile,
        expected_digest=deployment.qwen_model_digest,
        context_tokens=capabilities["qwen"]["context_tokens"],
        output_tokens=min(deployment.qwen_output_tokens, args.max_output_tokens),
        timeout_seconds=min(policy.limits.qwen_execution_seconds, args.max_seconds),
        first_output_seconds=min(deployment.qwen_first_output_seconds, args.max_seconds / 2),
        idle_output_seconds=min(deployment.qwen_idle_output_seconds, args.max_seconds / 2),
        ack_seconds=deployment.qwen_ack_seconds, poll_seconds=deployment.qwen_poll_seconds,
        poll_timeout_seconds=deployment.qwen_poll_timeout_seconds,
        poll_failures=deployment.qwen_poll_failures,
        keep_alive=deployment.qwen_keep_alive,
        temperature=deployment.qwen_temperature)
    preflight = adapter.preflight()
    if not preflight.ready:
        print(f"preflight_failed={preflight.reason}")
        return 2
    call_id = str(uuid.uuid4())
    lease = SimpleNamespace(call_id=call_id, lease_id=str(uuid.uuid4()),
                            fencing_token=1, policy_version="agent-probe")
    sink = Sink()
    result = adapter.run(lease, "Reply with the single word READY.", sink)
    print(f"job_id={call_id} state={result.state} reason={result.uncertainty}")
    print(f"answer={result.answer!r} usage={result.usage}")
    print(f"events={dict(sink.counts)}")
    return 0 if result.state == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
