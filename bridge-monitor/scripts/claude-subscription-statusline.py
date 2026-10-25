"""Optional approved quota status line; no provider calls or transcript access."""
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import sys
import tomllib

ROOT=Path(__file__).resolve().parents[1]
PRODUCER="claude-subscription-statusline"
TOKEN_ENV="BRIDGE_CLAUDE_SUBSCRIPTION_TOKEN"

def load_capture_main():
    path=ROOT/"scripts/capture-claude-subscription.py"
    spec=importlib.util.spec_from_file_location("bridge_quota_capture",path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main

def render(payload):
    rate=payload.get("rate_limits",{}) if isinstance(payload,dict) else {}
    parts=[]
    for name,label in (("five_hour","5h"),("seven_day","7d")):
        window=rate.get(name) if isinstance(rate,dict) else None
        value=window.get("used_percentage") if isinstance(window,dict) else None
        if type(value) in (int,float) and 0<=value<=100 and math.isfinite(value):
            parts.append(f"{label} {value:g}%")
    return "Claude allowance · "+(" · ".join(parts) if parts else "Unknown")

def main(*,config=None,credential_file=None,stdin=None,stdout=None):
    stdin=stdin or sys.stdin;stdout=stdout or sys.stdout
    raw=stdin.read(131073)
    if len(raw)>131072:
        stdout.write("Claude allowance · Unknown\n")
        return 0
    try:
        payload=json.loads(raw)
    except ValueError:
        payload={}
    stdout.write(render(payload)+"\n")
    try:
        config=Path(config or ROOT/"config/claude-subscription-feed.production.toml")
        settings=tomllib.loads(config.read_text(encoding="utf-8-sig"))["subscription_feed"]
        if not settings.get("enabled",False):
            return 0
        secret=Path(credential_file or ROOT/"var/secrets/claude-subscription.json").resolve(strict=True)
        if not secret.is_relative_to((ROOT/"var/secrets").resolve(strict=True)):
            raise ValueError("foreign credential file")
        if settings.get("credential_env",TOKEN_ENV)!=TOKEN_ENV:
            raise ValueError("unexpected credential reference")
        credential=json.loads(secret.read_text(encoding="utf-8-sig"))
        if (set(credential)!={"producer_id","token"} or credential["producer_id"]!=PRODUCER
                or not isinstance(credential["token"],str) or not credential["token"].strip()):
            raise ValueError("invalid quota producer credential")
        previous=os.environ.get(TOKEN_ENV)
        try:
            os.environ[TOKEN_ENV]=credential["token"]
            load_capture_main()(["--config",str(config)],stdin=io.StringIO(raw))
        finally:
            if previous is None:os.environ.pop(TOKEN_ENV,None)
            else:os.environ[TOKEN_ENV]=previous
    except Exception:
        # Keep the status line usable; failed delivery never advances server freshness.
        pass
    return 0

if __name__=="__main__":
    raise SystemExit(main())
