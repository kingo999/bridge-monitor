import contextlib
import http.client
import io
import json
import os
import re
import socket
import threading
import unittest

import support
from support import ObsTestCase, three_turn_records, success
import dashboard

STATIC = os.path.join(support.ROOT, "static")


class ServerCase(ObsTestCase):
    def start(self, state_dir=None):
        self.server = dashboard.DashboardServer(0, state_dir or self.ws.state, "test fixture")
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method="GET", path="/", headers=None, host="default", body=None):
        """Send exactly one request. http.client's automatic Host is always suppressed so the
        Host header is precisely the intended one (or absent when host is None)."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            if host is not None:
                conn.putheader("Host", "127.0.0.1:%d" % self.port if host == "default" else host)
            for key, value in (headers or {}).items():
                conn.putheader(key, value)
            if body is not None:
                conn.putheader("Content-Length", str(len(body)))
            conn.endheaders(body)
            resp = conn.getresponse()
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
        finally:
            conn.close()

    def raw(self, header_lines, path="/api/snapshot", method="GET"):
        """Send hand-written header lines over a bare socket (allows repeated headers).
        Returns the numeric status code."""
        request = "\r\n".join(["%s %s HTTP/1.1" % (method, path)] + header_lines) + "\r\n\r\n"
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.sendall(request.encode("ascii"))
            data = b""
            while b"\r\n" not in data:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
        return int(data.split(b" ", 2)[1])


class Routes(ServerCase):
    def setUp(self):
        super().setUp()
        self.ws.write_calls(three_turn_records())
        self.ws.write_marker("a.json", {"state": "running", "phase": "running",
                                        "call_id": support.call_id(9), "started_at": "2026-10-14T08:50:00Z"})
        self.start()

    def test_binds_loopback_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_fixed_routes_and_security_headers(self):
        for path, ctype in (("/", "text/html"), ("/static/app.js", "text/javascript"),
                            ("/static/style.css", "text/css"), ("/api/snapshot", "application/json")):
            with self.subTest(path):
                status, headers, body = self.request(path=path)
                self.assertEqual(status, 200)
                self.assertTrue(headers["content-type"].startswith(ctype))
                self.assertEqual(headers["x-content-type-options"], "nosniff")
                self.assertIn("default-src 'none'", headers["content-security-policy"])
                self.assertIn("script-src 'self'", headers["content-security-policy"])
                self.assertEqual(headers["cache-control"], "no-store")
                self.assertNotIn("access-control-allow-origin", headers)
                self.assertTrue(body)

    def test_snapshot_reflects_fixture(self):
        _s, _h, body = self.request(path="/api/snapshot")
        snap = json.loads(body)
        self.assertEqual(snap["schema"], "obs-01/1")
        self.assertEqual(snap["summary"]["calls_total"], 3)
        deltas = sorted(c["usage_delta"]["input_tokens"] for c in snap["calls"])
        self.assertEqual(deltas, [118896, 172553, 181960])
        self.assertEqual(snap["inflight"][0]["kind"], "running")
        self.assertTrue(snap["source"]["complete"])
        self.assertEqual(snap["source"]["state_dir"], "test fixture")
        self.assertNotIn(os.path.basename(self.ws.root), body.decode())  # no filesystem paths echoed

    def test_write_and_other_methods_are_rejected(self):
        for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD", "TRACE", "FOO"):
            for path in ("/", "/api/snapshot"):
                with self.subTest(method=method, path=path):
                    status, headers, _b = self.request(method, path, body=b"x=1" if method in ("POST", "PUT", "PATCH") else None)
                    self.assertEqual(status, 405)
                    self.assertEqual(headers.get("allow"), "GET")

    def test_untrusted_host_is_rejected(self):
        for host in ("evil.example", "evil.example:%d" % self.port, "127.0.0.1", "127.0.0.1:1",
                     "localhost.evil.example:%d" % self.port, "0.0.0.0:%d" % self.port, ""):
            with self.subTest(host=host):
                status, _h, body = self.request(path="/api/snapshot", host=host)
                self.assertEqual(status, 403)
                self.assertNotIn(b"calls_total", body)

    def test_missing_host_is_rejected(self):
        status, _h, _b = self.request(path="/api/snapshot", host=None)
        self.assertEqual(status, 403)

    def test_localhost_host_is_accepted(self):
        status, _h, _b = self.request(path="/api/snapshot", host="localhost:%d" % self.port)
        self.assertEqual(status, 200)

    def test_origin_is_checked(self):
        good = "http://127.0.0.1:%d" % self.port
        self.assertEqual(self.request(path="/api/snapshot", headers={"Origin": good})[0], 200)
        for origin in ("http://evil.example", "null", "https://127.0.0.1:%d" % self.port,
                       "http://127.0.0.1:1"):
            with self.subTest(origin=origin):
                self.assertEqual(self.request(path="/api/snapshot", headers={"Origin": origin})[0], 403)

    def test_single_valid_host_is_accepted_over_a_raw_socket(self):
        good = "Host: 127.0.0.1:%d" % self.port
        self.assertEqual(self.raw([good]), 200)

    def test_repeated_host_headers_are_rejected_not_first_wins(self):
        good = "Host: 127.0.0.1:%d" % self.port
        evil = "Host: evil.example"
        for lines in ([good, good], [good, evil], [evil, good]):
            with self.subTest(lines=lines):
                self.assertEqual(self.raw(lines), 400)

    def test_repeated_origin_headers_are_rejected_not_first_wins(self):
        host = "Host: 127.0.0.1:%d" % self.port
        good = "Origin: http://127.0.0.1:%d" % self.port
        evil = "Origin: http://evil.example"
        self.assertEqual(self.raw([host, good]), 200)
        for lines in ([host, good, good], [host, good, evil], [host, evil, good]):
            with self.subTest(lines=lines):
                self.assertEqual(self.raw(lines), 400)

    def test_repeated_fetch_site_headers_are_rejected(self):
        host = "Host: 127.0.0.1:%d" % self.port
        self.assertEqual(self.raw([host, "Sec-Fetch-Site: same-origin", "Sec-Fetch-Site: cross-site"]), 400)

    def test_absolute_form_request_target_is_not_served(self):
        target = "http://127.0.0.1:%d/api/snapshot" % self.port
        status, _h, _b = self.request(path=target)
        self.assertEqual(status, 404)

    def test_cross_site_fetch_metadata_is_rejected(self):
        status, _h, _b = self.request(path="/api/snapshot", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)

    def test_traversal_and_arbitrary_paths_are_404(self):
        paths = ["/static/../dashboard.py", "/static/%2e%2e/dashboard.py", "/../../etc/passwd",
                 "/static/..%5cdashboard.py", "/dashboard.py", "/obs_reader.py", "/tests/support.py",
                 "/telemetry/calls.jsonl", "/state/telemetry/calls.jsonl", "//static/app.js",
                 "/static/app.js?x=1", "/api/snapshot?x=1", "/api/snapshot/", "/static/", "/static",
                 "/inflight/a.json", "/C:/Windows/win.ini", "/static/app.js%00.png", "http://evil/",
                 "/README.md", "/.git/config", "/api/../dashboard.py"]
        for path in paths:
            with self.subTest(path=path):
                status, _h, body = self.request(path=path)
                self.assertEqual(status, 404)
                self.assertNotIn(b"import ", body)
                self.assertNotIn(b"calls_total", body)

    def test_oversized_request_target_is_refused(self):
        status, _h, _b = self.request(path="/" + "a" * 8000)
        self.assertEqual(status, 404)


class SnapshotStates(ServerCase):
    def test_missing_source_is_an_explicit_error_not_an_empty_success(self):
        self.start(os.path.join(self.ws.root, "absent"))
        snap = json.loads(self.request(path="/api/snapshot")[2])
        self.assertFalse(snap["source"]["complete"])
        self.assertEqual(snap["notices"][0]["level"], "error")
        self.assertEqual(snap["calls"], [])

    def test_no_telemetry_yet_is_a_warning(self):
        self.start()
        snap = json.loads(self.request(path="/api/snapshot")[2])
        self.assertEqual(snap["summary"]["calls_total"], 0)
        self.assertIn("no_telemetry", [n["code"] for n in snap["notices"]])

    def test_gaps_make_source_incomplete_and_are_announced(self):
        self.ws.write_calls(three_turn_records(), raw_lines=["{broken"])
        self.start()
        snap = json.loads(self.request(path="/api/snapshot")[2])
        self.assertFalse(snap["source"]["complete"])
        self.assertIn("telemetry_gaps", [n["code"] for n in snap["notices"]])

    def test_hostile_strings_come_back_as_inert_json(self):
        rec = success(1, 1000, 500, 100, 10, resumed=False, outcome="error",
                      error_category="</script><script>alert(1)</script>")
        self.ws.write_calls([rec])
        self.start()
        status, headers, body = self.request(path="/api/snapshot")
        self.assertEqual(headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertTrue(body.isascii())
        self.assertEqual(json.loads(body)["calls"][0]["error_category"],
                         "</script><script>alert(1)</script>")

    def test_serving_does_not_modify_the_state_dir(self):
        self.ws.write_calls(three_turn_records())
        self.ws.write_marker("a.json", {"state": "running"})
        before = support.tree_digest(self.ws.state)
        self.start()
        for path in ("/", "/api/snapshot", "/api/snapshot"):
            self.request(path=path)
        self.request("POST", "/api/snapshot", body=b"{}")
        self.assertEqual(support.tree_digest(self.ws.state), before)


class Shutdown(ServerCase):
    def test_shutdown_releases_the_port(self):
        self.start()
        port = self.port
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())
        # Re-bind to prove the listener was released (cleanup's second stop() is harmless).
        again = dashboard.DashboardServer(port, self.ws.state)
        again.server_close()


class StaticAssets(unittest.TestCase):
    def read(self, name):
        with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
            return fh.read()

    def test_no_html_injection_sinks_in_script(self):
        js = self.read("app.js")
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                     "new Function", "setTimeout(\"", "javascript:", "dangerouslySet"):
            self.assertNotIn(sink, js)

    def test_html_has_no_inline_script_or_handlers(self):
        html = self.read("index.html")
        self.assertIsNone(re.search(r"<script(?![^>]*\bsrc=)", html))
        self.assertIsNone(re.search(r"\son[a-z]+\s*=", html))
        self.assertNotIn("style=", html)
        self.assertNotIn("http://", html.replace('http://www.w3.org', ""))

    def test_raw_counters_are_not_labelled_cumulative_unconditionally(self):
        js = self.read("app.js")
        self.assertNotIn("Cumulative usage (raw, session)", js)
        self.assertIn("Raw counters (as logged)", js)
        # The cumulative label is chosen from the backend-established basis, per row.
        self.assertIn('c.usage_basis === "session_cumulative"', js)
        self.assertIn("basis unknown", js)
        self.assertIn("rawUsageCell(c)", js)

    def test_script_only_fetches_the_fixed_snapshot_endpoint_with_get(self):
        js = self.read("app.js")
        self.assertEqual(re.findall(r"fetch\(([^,)]+)", js), ['"/api/snapshot"'])
        self.assertNotIn("XMLHttpRequest", js)
        self.assertNotIn("POST", js)


class Cli(ObsTestCase):
    def test_snapshot_once_prints_json_and_does_not_serve(self):
        self.ws.write_calls(three_turn_records())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = dashboard.main(["--state-dir", self.ws.state, "--snapshot-once"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["summary"]["calls_total"], 3)

    def test_port_must_be_valid(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                dashboard.main(["--port", "70000", "--snapshot-once"])

    def test_default_state_dir_is_the_v2_home_directory(self):
        self.assertTrue(dashboard.default_state_dir().endswith(".gpt-architect-bridge-v2"))
        self.assertEqual(dashboard.DEFAULT_PORT, 8089)
        self.assertEqual(dashboard.BIND_HOST, "127.0.0.1")

    def test_busy_port_is_reported_not_crashed(self):
        first = dashboard.DashboardServer(0, self.ws.state)
        self.addCleanup(first.server_close)
        with contextlib.redirect_stderr(io.StringIO()):
            code = dashboard.main(["--state-dir", self.ws.state, "--port", str(first.server_address[1])])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
