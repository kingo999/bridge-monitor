"""Serve the static dashboard and same-origin APIs with explicit local configuration."""

import argparse
import ipaddress
import json
import os
import sys
import tempfile
import tomllib
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from service.http.app import create_app  # noqa: E402
from bridge.telemetry.claude_subscription import subscription_producer_ids  # noqa: E402
from bridge.desktop_capture import DESKTOP_STATE_PATH  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "dashboard-service.example.toml")
    args = parser.parse_args()
    settings = tomllib.loads(args.config.read_text(encoding="utf-8"))
    listen = settings["listen"]
    host = listen["host"]
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower() == "localhost"
    cert = listen.get("tls_cert_path") or None
    key = listen.get("tls_key_path") or None
    if not loopback and not (cert and key):
        raise SystemExit("non-loopback dashboard binding requires configured TLS certificate and key")
    if bool(cert) != bool(key):
        raise SystemExit("TLS certificate and key must be configured together")
    capture = settings["capture"]
    operator = settings["operator"]
    local_test_login = operator.get("local_test_login", False)
    if type(local_test_login) is not bool:
        raise SystemExit("local_test_login must be a boolean")
    port = int(listen["port"])
    local_test_origin = None
    if local_test_login:
        if host != "127.0.0.1" or not 1 <= port <= 65535:
            raise SystemExit("local_test_login requires a literal 127.0.0.1 listener and a fixed port")
        scheme = "https" if cert else "http"
        authority = host if port == (443 if cert else 80) else f"{host}:{port}"
        local_test_origin = f"{scheme}://{authority}"
    dsn = os.environ.get(capture["dsn_env"], "")
    token = os.environ.get(operator["credential_env"], "")
    producers = json.loads(os.environ.get(capture["producer_tokens_env"], "{}"))
    if not dsn or not token or not isinstance(producers, dict) or not producers:
        raise SystemExit("configured DSN, operator credential and producer credentials are required")
    export = (ROOT / settings["dashboard"]["export_dir"]).resolve()
    if not export.is_relative_to(ROOT):
        raise SystemExit("dashboard export must stay inside bridge-monitor")
    app = create_app(dsn, producer_tokens=producers, operator_token=token,
                     payload_limit_bytes=int(capture["payload_limit_bytes"]),
                     subscription_producer_ids=subscription_producer_ids(capture),
                     dashboard_dir=export,
                     session_ttl_seconds=int(operator["session_ttl_seconds"]),
                     secure_cookies=bool(cert),
                     preview_max_chunks=int(settings["dashboard"].get("preview_max_chunks", 80)),
                     preview_max_bytes=int(settings["dashboard"].get("preview_max_bytes", 16384)),
                     timeline_head_events=int(settings["dashboard"].get("timeline_head_events", 100)),
                     timeline_tail_events=int(settings["dashboard"].get("timeline_tail_events", 400)),
                     work_item_max_limit=int(settings["dashboard"].get("work_item_max_limit", 100)),
                     # Validated by create_app; omitted -> None (decision filter disabled).
                     design_mode_owner_id=settings["dashboard"].get("design_mode_owner_id"),
                     classifier_config_path=settings["dashboard"].get("classifier_config_path"),
                     desktop_state_path=DESKTOP_STATE_PATH,
                     local_test_login_origin=local_test_origin)
    pid_file = os.environ.get("BRIDGE_MONITOR_SERVER_PID_FILE")
    record = None
    if pid_file:
        record = Path(pid_file).resolve()
        if not record.is_relative_to(ROOT / "var" / "run"):
            raise SystemExit("server PID receipt must stay inside project var/run")
        record.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".tmp",
                                         dir=record.parent, delete=False) as pending:
            json.dump({"pid": os.getpid(), "config": str(args.config.resolve()),
                       "script": str(Path(__file__).resolve())}, pending)
            temporary = Path(pending.name)
        os.replace(temporary, record)
    try:
        uvicorn.run(app, host=host, port=port, ssl_certfile=cert, ssl_keyfile=key,
                    proxy_headers=not local_test_login)
    finally:
        if record and record.is_file():
            try:
                if json.loads(record.read_text(encoding="utf-8")).get("pid") == os.getpid():
                    record.unlink()
            except (OSError, ValueError):
                pass


if __name__ == "__main__":
    main()
