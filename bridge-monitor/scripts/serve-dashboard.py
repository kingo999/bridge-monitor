"""Serve the static dashboard and same-origin APIs with explicit local configuration."""

import argparse
import ipaddress
import json
import os
import sys
import tomllib
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from service.http.app import create_app  # noqa: E402


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
                     dashboard_dir=export,
                     session_ttl_seconds=int(operator["session_ttl_seconds"]),
                     secure_cookies=bool(cert))
    uvicorn.run(app, host=host, port=int(listen["port"]), ssl_certfile=cert, ssl_keyfile=key)


if __name__ == "__main__":
    main()
