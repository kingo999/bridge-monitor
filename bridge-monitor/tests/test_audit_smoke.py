import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest


@pytest.fixture
def smoke():
    path = Path(__file__).parents[1] / "scripts/outbound-audit/smoke.py"
    spec = importlib.util.spec_from_file_location("audit_smoke", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generic_http_receipt_does_not_mislabel_loopback_as_off_machine(smoke):
    paths = []
    class Receiver(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            self.send_response(204)
            self.end_headers()
        def log_message(self, *args):
            pass
    server = HTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with smoke.transfer("127.0.0.1", server.server_port, "/test-health") as result:
            assert result["remote_status"] == 204
            assert result["sent"] > 12
            assert result["off_machine"] is False
        assert paths == ["/test-health"]
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_attempt_only_coverage_has_partial_exit_and_receipt(smoke, monkeypatch, tmp_path):
    result = tmp_path / "receipt.json"
    monkeypatch.setattr(sys, "argv", ["smoke", __file__, "--result", str(result)])
    monkeypatch.setattr(smoke, "verify", lambda *args: {"status": "partial", "kernel_bytes": None})
    assert smoke.main() == 2
    assert json.loads(result.read_text())["status"] == "partial"


def test_remote_mode_requires_an_explicit_host_and_port(smoke, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["smoke", __file__, "--host", "test.invalid"])
    with pytest.raises(SystemExit) as error:
        smoke.main()
    assert error.value.code == 2
