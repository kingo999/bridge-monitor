"""Fixture-only HTTP smoke for the OBS-01 dashboard.

Builds a disposable fake state dir, serves it on 127.0.0.1 (port 0), exercises the
fixed GET routes plus a few refusals, prints one line per check and exits non-zero
on any failure. No model calls, no external network, no real bridge state.

    python tests/smoke_http.py
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # works under python -I

import support
import dashboard


def main():
    ws = support.Workspace()
    failures = []

    def check(label, ok, detail=""):
        print("%s  %s%s" % ("PASS" if ok else "FAIL", label, ("  " + detail) if detail and not ok else ""))
        if not ok:
            failures.append(label)

    try:
        ws.write_calls(support.three_turn_records())
        ws.write_marker("a.json", {"state": "running", "phase": "running", "call_id": support.call_id(9),
                                   "started_at": "2026-10-14T08:50:00Z", "model": support.MODEL})
        digest = support.tree_digest(ws.state)
        server = dashboard.DashboardServer(0, ws.state, "smoke fixture")
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def get(method, path, host=None, headers=None):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
                conn.putheader("Host", host or "127.0.0.1:%d" % port)
                for k, v in (headers or {}).items():
                    conn.putheader(k, v)
                conn.endheaders()
                resp = conn.getresponse()
                return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
            finally:
                conn.close()

        try:
            check("listener is loopback only", server.server_address[0] == "127.0.0.1")
            for path in ("/", "/static/app.js", "/static/style.css"):
                status, headers, body = get("GET", path)
                check("GET %s -> 200 with nosniff" % path,
                      status == 200 and headers.get("x-content-type-options") == "nosniff" and body)
            status, headers, body = get("GET", "/api/snapshot")
            snap = json.loads(body)
            check("GET /api/snapshot -> 200 json", status == 200 and snap["schema"] == "obs-01/1")
            by_n = {c["call_id"]: c for c in snap["calls"]}
            got = [by_n[support.call_id(n)]["usage_delta"] for n in (1, 2, 3)]
            check("input deltas 181960/172553/118896",
                  [d["input_tokens"] for d in got] == [181960, 172553, 118896])
            check("cached deltas 136448/167424/117504",
                  [d["cached_input_tokens"] for d in got] == [136448, 167424, 117504])
            check("output deltas 3652/1335/139", [d["output_tokens"] for d in got] == [3652, 1335, 139])
            check("run_ms 141652/53113/14297",
                  [by_n[support.call_id(n)]["durations"]["run_ms"] for n in (1, 2, 3)] == [141652, 53113, 14297])
            check("in-flight marker projected as running",
                  snap["inflight"] and snap["inflight"][0]["kind"] == "running")
            check("POST refused (405)", get("POST", "/api/snapshot")[0] == 405)
            check("foreign Host refused (403)", get("GET", "/api/snapshot", host="evil.example")[0] == 403)
            check("foreign Origin refused (403)",
                  get("GET", "/api/snapshot", headers={"Origin": "http://evil.example"})[0] == 403)
            check("traversal refused (404)", get("GET", "/static/../dashboard.py")[0] == 404)
            check("state dir byte-identical after serving", support.tree_digest(ws.state) == digest)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            check("server stopped cleanly", not thread.is_alive())
    finally:
        ws.cleanup()
    print("smoke: %s" % ("FAILED (%d)" % len(failures) if failures else "ok"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
