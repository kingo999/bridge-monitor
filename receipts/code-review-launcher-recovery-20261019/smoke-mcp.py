"""Exercise the shipped launcher's local MCP handshake; no review tools are called."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

OUT = Path(__file__).resolve().parent
SOURCE = Path(r"C:\Program Files\WindowsApps\OpenAI.Codex_26.1013.4894.0_x64__2p2nqsd0c76g0\app\resources\plugins\openai-bundled\plugins\code-review")
CACHE = Path(r"C:\Users\kian\.codex\plugins\cache\openai-bundled\code-review\26.1013.40934")
NODE = Path(r"C:\Users\kian\AppData\Local\OpenAI\Codex\runtimes\cua_node\78fb63cda1a9e2b2\bin\node.exe")


def main(mode):
    root = SOURCE if mode == "source" else CACHE
    launcher = root / "scripts/launch_code_review_mcp.cmd"
    receipt = OUT / f"smoke-{mode}.json"
    if receipt.exists():
        raise SystemExit("Refusing to overwrite a smoke receipt")
    if launcher.read_bytes() != (SOURCE / "scripts/launch_code_review_mcp.cmd").read_bytes():
        raise SystemExit("Launcher differs from the installed OpenAI source")
    env = {k: v for k, v in os.environ.items() if k.upper() in {
        "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "PATH", "PATHEXT",
        "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOMEDRIVE", "HOMEPATH"}}
    env["CODEX_MCP_NODE_PATH"] = str(NODE)
    env["CODEX_HOME"] = str(OUT / f"isolated-home-{mode}")
    process = subprocess.Popen([
        str(Path(env["SYSTEMROOT"]) / "System32/cmd.exe"), "/d", "/c",
        r"scripts\launch_code_review_mcp.cmd"], cwd=root, env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
    stdout = queue.Queue()
    stderr = []

    def read_stdout():
        for line in process.stdout:
            stdout.put(line)
        stdout.put(None)

    def read_stderr():
        for line in process.stderr:
            stderr.append(line)

    threading.Thread(target=read_stdout, daemon=True).start()
    threading.Thread(target=read_stderr, daemon=True).start()

    def send(message):
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def request(identifier, method, params=None):
        send({"jsonrpc": "2.0", "id": identifier, "method": method,
              **({"params": params} if params is not None else {})})
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            line = stdout.get(timeout=max(0.01, deadline - time.monotonic()))
            if line is None:
                raise RuntimeError("MCP stdout closed before response")
            message = json.loads(line)
            if message.get("id") == identifier:
                if "error" in message:
                    raise RuntimeError(f"MCP error for {method}: {message['error']}")
                return message["result"]
        raise TimeoutError(method)

    result = {"mode": mode, "launcher": str(launcher), "node": str(NODE),
              "launcher_sha256": hashlib.sha256(launcher.read_bytes()).hexdigest(),
              "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "methods": ["initialize", "notifications/initialized", "ping", "tools/list"],
              "review_tools_called": False, "credentials_forwarded": False}
    try:
        initialized = request(1, "initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "launcher-recovery-smoke", "version": "1.0"}})
        result["server_info"] = {key: value for key, value in initialized.get("serverInfo", {}).items()
                                 if key in ("name", "title", "version")}
        result["protocol_version"] = initialized.get("protocolVersion")
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        result["ping"] = request(2, "ping")
        listed = request(3, "tools/list")
        result["tool_names"] = [tool["name"] for tool in listed.get("tools", [])]
        result["tool_count"] = len(result["tool_names"])
        if initialized.get("serverInfo", {}).get("name") != "code-review":
            raise RuntimeError("Unexpected MCP server identity")
        if not result["tool_count"]:
            raise RuntimeError("No tools advertised")
        result["passed"] = True
    except Exception as error:
        result.update(passed=False, error_type=type(error).__name__, error=str(error))
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
            result["cleanup"] = "stdin_closed_process_exited"
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW, check=False)
            process.wait(timeout=5)
            result["cleanup"] = "test_process_tree_terminated"
        result["process_exit"] = process.returncode
        result["stderr"] = "".join(stderr)[:3000]
        receipt.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
