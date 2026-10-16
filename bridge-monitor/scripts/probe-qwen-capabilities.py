"""Bounded local-only Qwen capability probe; no bridge policy or routing changes."""

import json
import time
import tomllib
from pathlib import Path

import httpx


PROJECT = Path(__file__).resolve().parents[1]


def main() -> int:
    with (PROJECT / "config" / "pilot-policy.proposal.toml").open("rb") as source:
        deployment = tomllib.load(source)["deployment"]
    endpoint = deployment["qwen_endpoint"].rstrip("/")
    model = deployment["qwen_model_profile"]
    expected_digest = deployment["qwen_model_digest"]
    with (PROJECT / "config" / "runtime-capabilities.proposal.toml").open("rb") as source:
        context_tokens = tomllib.load(source)["qwen"]["context_tokens"]
    with httpx.Client(timeout=60) as client:
        tags = client.get(f"{endpoint}/api/tags")
        tags.raise_for_status()
        profile = next((item for item in tags.json().get("models", [])
                        if item.get("name") == model), None)
        if not profile or profile.get("digest") != expected_digest:
            raise RuntimeError("selected model digest changed")
        started = time.monotonic()
        response = client.post(f"{endpoint}/api/generate", json={
            "model": model, "prompt": "Reply with OK only.", "stream": False,
            "options": {"num_ctx": context_tokens,
                        "num_predict": min(16, deployment["qwen_output_tokens"]),
                        "temperature": deployment["qwen_temperature"]},
            "keep_alive": deployment["qwen_keep_alive"]})
        response.raise_for_status()
        elapsed = round(time.monotonic() - started, 3)
        body = response.json()
        resident = client.get(f"{endpoint}/api/ps")
        resident.raise_for_status()
        ps = next((item for item in resident.json().get("models", [])
                   if item.get("name") == model), None)
    receipt = {"model": body.get("model"), "done": body.get("done"),
               "done_reason": body.get("done_reason"),
               "prompt_eval_count": body.get("prompt_eval_count"),
               "eval_count": body.get("eval_count"),
               "elapsed_seconds": elapsed, "expected_digest": expected_digest,
               "resident_context_length": ps.get("context_length") if ps else None,
               "response": body.get("response")}
    output = PROJECT / "var" / "probes" / "qwen-capabilities.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, sort_keys=True))
    return 0 if body.get("model") == model and body.get("done") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
