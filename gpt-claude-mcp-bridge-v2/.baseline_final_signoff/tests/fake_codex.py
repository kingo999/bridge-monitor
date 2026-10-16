"""A scriptable stand-in for the Codex CLI used by the automated tests.

Behaviour is driven by the JSON file named in ``FAKE_CODEX_SCENARIO``.  Every
invocation appends one JSON line to the scenario's ``log`` path so tests can
assert on exactly which subcommands/flags the bridge used.  No network, no
model, no credentials: this file never reads Codex's real configuration.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

DEFAULT_MODELS = {
    "gpt-6.1-sol": ["low", "medium", "high", "xhigh", "max", "ultra"],
    "gpt-6-astra": ["low", "medium", "high", "xhigh", "max", "ultra"],
    "gpt-5.5": ["low", "medium", "high", "xhigh"],
}
DEFAULT_FEATURES = [
    "hooks", "plugins", "apps", "browser_use", "browser_use_external",
    "browser_use_full_cdp_access", "in_app_browser", "computer_use", "multi_agent", "multi_agent_v2",
    "image_generation", "fast_mode",
]


def load_scenario() -> dict:
    path = os.environ.get("FAKE_CODEX_SCENARIO")
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def log(scn: dict, entry: dict) -> None:
    target = scn.get("log")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")


def option_values(argv: list[str], flag: str) -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == flag]


def main() -> int:
    argv = sys.argv[1:]
    scn = load_scenario()
    base_log = {
        "argv": argv,
        "cwd": os.getcwd(),
        "env_api_key_present": any(k in os.environ for k in ("CODEX_API_KEY", "OPENAI_API_KEY")),
    }

    if argv[:1] == ["--version"]:
        log(scn, {**base_log, "kind": "version"})
        print(f"codex-cli {scn.get('version', '0.162.1')}")
        return 0

    if argv[:2] == ["login", "status"]:
        log(scn, {**base_log, "kind": "login_status"})
        state = scn.get("auth", "chatgpt")
        if state == "chatgpt":
            print("Logged in using ChatGPT")
            return 0
        if state == "api_key":
            print("Logged in using an API key - sk-fake-should-never-be-logged-0000")
            return 0
        if state == "none":
            print("Not logged in")
            return 1
        print("status: ???")
        return 0

    if argv[:2] == ["debug", "models"]:
        log(scn, {**base_log, "kind": "debug_models"})
        models = scn.get("models", DEFAULT_MODELS)
        print(json.dumps({"models": [
            {"slug": slug, "supported_reasoning_levels": [{"effort": e} for e in levels]}
            for slug, levels in models.items()
        ]}))
        return 0

    if argv[:2] == ["features", "list"]:
        log(scn, {**base_log, "kind": "features_list", "disables": option_values(argv, "--disable")})
        if scn.get("features_raw") is not None:           # schema-incompatibility fixture
            print(scn["features_raw"])
            return 0
        disabled = set(option_values(argv, "--disable")) - set(scn.get("ignore_feature_disable", []))
        for name in scn.get("features", DEFAULT_FEATURES):
            state = "false" if name in disabled else "true"
            print(f"{name:<40} stable             {state}")
        return 0

    if argv[:2] == ["mcp", "list"]:
        log(scn, {**base_log, "kind": "mcp_list"})
        if scn.get("mcp_raw") is not None:                  # schema-incompatibility fixture
            print(scn["mcp_raw"])
            return 0
        servers = scn.get("mcp_servers", [])
        if scn.get("mcp_sequence"):                        # configuration that changes between readouts
            counter = Path(str(scn.get("log")) + ".mcpseq")
            idx = int(counter.read_text()) if counter.exists() else 0
            counter.write_text(str(idx + 1))
            seq = scn["mcp_sequence"]
            servers = seq[min(idx, len(seq) - 1)]
        disabled_features = set(option_values(argv, "--disable"))
        overrides = option_values(argv, "-c")
        disabled_by_override = {
            o[len("mcp_servers."):-len(".enabled=false")]
            for o in overrides if o.startswith("mcp_servers.") and o.endswith(".enabled=false")
        }
        rows = []
        known_config = {s["name"] for s in servers if s.get("source", "config") == "config"}
        for name in disabled_by_override:
            if name not in known_config:
                print("Error: failed to load bootstrap configuration", file=sys.stderr)
                return 1
        for s in servers:
            source = s.get("source", "config")
            if source == "plugin" and "plugins" in disabled_features:
                continue
            enabled = True
            if s["name"] in disabled_by_override and s["name"] not in scn.get("ignore_overrides", []):
                enabled = False
            rows.append({"name": s["name"], "enabled": enabled})
        print(json.dumps(rows))
        return 0

    if argv[:1] == ["sandbox"]:
        cmd = argv[argv.index("--") + 1:] if "--" in argv else []
        log(scn, {**base_log, "kind": "sandbox", "cmd": cmd})
        mode = scn.get("sandbox", "ok")
        if mode == "setup_failed":
            print("windows sandbox failed: helper_unknown_error: setup refresh had errors", file=sys.stderr)
            return 1
        if cmd[:3] == ["cmd", "/c", "cd"]:
            print(os.getcwd())
            return 0
        if cmd[:1] == ["findstr"]:
            if mode == "source_unreadable":
                print("Access is denied.", file=sys.stderr)
                return 1
            print("file contents")
            return 0
        if cmd[:2] == ["git", "rev-parse"]:
            if mode == "repo_unreadable":
                print("fatal: Unable to read current working directory: No such file or directory",
                      file=sys.stderr)
                return 128
            print("true")
            return 0
        return 0

    if argv[:1] == ["exec"]:
        return run_exec(argv, scn, base_log)

    print("fake codex: unsupported invocation", file=sys.stderr)
    return 64


def run_exec(argv: list[str], scn: dict, base_log: dict) -> int:
    counter_path = scn.get("counter")
    index = 0
    if counter_path:
        p = Path(counter_path)
        index = int(p.read_text()) if p.exists() else 0
        p.write_text(str(index + 1))
    seq = scn.get("exec_sequence")
    spec = (seq[min(index, len(seq) - 1)] if seq else scn.get("exec", {}))

    resume = len(argv) > 1 and argv[1] == "resume"
    if spec.get("pre_stdin_started_file"):   # written BEFORE stdin is read: proves startup code ran
        Path(spec["pre_stdin_started_file"]).write_text(str(os.getpid()))
    prompt = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    out_file = (option_values(argv, "--output-last-message") or option_values(argv, "-o") or [None])[0]
    log(scn, {
        **base_log,
        "kind": "exec_resume" if resume else "exec_fresh",
        "prompt_chars": len(prompt),
        "prompt_sha": hashlib.sha256(prompt.encode()).hexdigest()[:12],
        "prompt_has_marker": scn.get("prompt_marker", "\0") in prompt,
        "call_index": index,
    })

    if spec.get("spawn_child"):
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(600)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        Path(spec["spawn_child"]).write_text(str(child.pid))
    if spec.get("started_file"):
        Path(spec["started_file"]).write_text(str(os.getpid()))
    if spec.get("mutate"):  # simulate the repository changing while the turn runs (F6)
        target = Path(os.getcwd()) / spec["mutate"]["path"]
        target.write_text(spec["mutate"].get("content", "changed during review\n"), encoding="utf-8")
    if spec.get("sleep"):
        time.sleep(float(spec["sleep"]))

    thread = spec.get("thread_id")
    if thread is None:
        thread = argv[2] if resume else str(uuid.uuid4())
    replacement = {"{thread}": thread}
    events = spec.get("events")
    if events is None:
        usage = spec.get("usage", {
            "input_tokens": 120, "cached_input_tokens": 100, "output_tokens": 30, "reasoning_output_tokens": 10,
        })
        completed = {"type": "turn.completed"}
        if usage is not None:
            completed["usage"] = usage
        events = [
            {"type": "thread.started", "thread_id": "{thread}"},
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}},
            completed,
        ]
    for event in events:
        if isinstance(event, str):
            print(event, flush=True)  # raw (possibly malformed) line
        else:
            line = json.dumps(event)
            for k, v in replacement.items():
                line = line.replace(k, v)
            print(line, flush=True)

    if spec.get("sleep_after_events"):   # a thread was announced, then the turn hangs (timeout/cancel tests)
        time.sleep(float(spec["sleep_after_events"]))
    if spec.get("stderr"):
        print(spec["stderr"], file=sys.stderr, flush=True)
    answer = spec.get("answer", "FAKE ANSWER")
    if answer is not None and out_file:
        Path(out_file).write_text(answer, encoding="utf-8")
    return int(spec.get("exit", 0))


if __name__ == "__main__":
    sys.exit(main())
