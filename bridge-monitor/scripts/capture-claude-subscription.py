"""Optional status-line collector. Disabled by default; prints no UI or secret values."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import tomllib
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bridge.telemetry.claude_subscription import FeedConfig,capture_statusline,deliver_pending

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None

def main(argv=None,*,stdin=None):
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",type=Path,required=True)
    args=parser.parse_args(argv)
    settings=FeedConfig.model_validate(tomllib.loads(args.config.read_text(encoding="utf-8"))["subscription_feed"])
    if not settings.enabled:
        return 0
    state=(ROOT/settings.state_path).resolve()
    if not state.is_relative_to(ROOT/"var"/"spool"):
        raise ValueError("subscription state must stay inside project var/spool")
    token=os.environ.get(settings.credential_env,"")
    if not token:
        raise ValueError("subscription producer credential is unavailable")
    raw=(stdin or sys.stdin).read(131073)
    if len(raw)>131072:
        raise ValueError("status-line input too large")
    payload=json.loads(raw)
    capture_statusline(payload,state)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    deadline=time.monotonic()+2
    def send(body):
        remaining=deadline-time.monotonic()
        if remaining<=0:
            raise TimeoutError()
        request=urllib.request.Request(settings.endpoint,data=json.dumps(body).encode("utf-8"),
            headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"},method="POST")
        with opener.open(request,timeout=min(1,remaining)) as response:
            if response.status!=200:
                raise ValueError("subscription observation was not accepted")
            return json.loads(response.read(4096))
    deliver_pending(state,send)
    return 0

if __name__=="__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("bridge-subscription: capture unavailable; allowance freshness was not renewed",file=sys.stderr)
        raise SystemExit(0)
