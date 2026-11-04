import json
import os
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from bridge.audit_cli import main
from bridge.audit_collector import Collector, EPOCH
from bridge.audit_hooks import ObservedClient, application_call, current, register_process
from bridge.outbound_audit import Journal, Observation, Policy


def test_attribution_pid_reuse_shared_helper_and_loss(tmp_path):
    book = Journal(tmp_path / "audit.db")
    feed = Collector(book, ["10.0.0.1"])
    stamp = EPOCH + 1000
    for pid in (10, 20):
        book.register(pid, stamp, {"agent": "worker", "run": str(pid)})
        feed.line(f"start\t{pid}\t{stamp}\t1\tworker.exe")
    def start(pid, parent, offset=1):
        feed.line(f"start\t{pid}\t{stamp + offset}\t{parent}\thelper.exe")
    def packet(pid, opcode=10, dest="192.0.2.8"):
        feed.line(f"net\t{stamp + 10}\t{pid}\t{opcode}\t{dest}\t443\t18")
    start(11, 10)
    start(21, 20)
    for pid in (11, 21, 30):
        packet(pid)
    packet(11, 4)
    packet(11, 12)
    for local in ("127.0.0.1", "::1", "::ffff:127.0.0.1", "10.0.0.1"):
        packet(11, dest=local)
    feed.line(f"end\t11\t{stamp+1}")
    start(11, 1, 2)
    feed.line(f"end\t11\t{stamp+1}")  # delayed exit of old process
    packet(11, 58, "2001:db8::1")
    feed.line("gap\tloss")
    packet(21)
    rows = [r["evidence"] for r in book.records()]
    assert len(rows) == 8
    assert [r.get("run") for r in rows[:3]] == ["10", "20", None]
    assert rows[0]["parent_pid"] == 10 and rows[0]["bytes_out"] == 18
    assert "bytes_out" not in rows[3] and rows[3]["kind"] == "attempt"
    assert "bytes_out" not in rows[4] and rows[4]["kind"] == "connected"
    assert rows[5]["transport"] == "udp" and "run" not in rows[5]
    assert rows[6]["kind"] == "gap" and "run" not in rows[7]


def test_policy_rehearsal_against_controlled_receiver(tmp_path, monkeypatch, capsys):
    path = tmp_path / "audit.db"
    monkeypatch.setenv("BRIDGE_OUTBOUND_AUDIT_DB", str(path))
    draft = tmp_path / "draft.json"
    draft.write_text('{"default":"deny","rules":[{"id":"allowed","effect":"allow","hostname":"allowed.invalid"}]}')
    main(["--db", str(path), "policy", str(draft)])
    version = capsys.readouterr().out.strip()
    received = []
    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(204)
            self.end_headers()
        def log_message(self, *args):
            pass
    server = HTTPServer(("127.0.0.1", 0), Receiver)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    original = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **kw:
                        original("127.0.0.1" if host in ("approved-test.invalid", b"approved-test.invalid")
                                 else host, *a, **kw))
    try:
        with httpx.Client(trust_env=False) as client:
            result = ObservedClient(client).post(f"http://approved-test.invalid:{server.server_port}/?token=SENSITIVE",
                headers={"Authorization":"SENSITIVE"}, content=b"private prompt")
            assert result.status_code == 204 and received == [b"private prompt"]
    finally:
        server.shutdown()
        worker.join()
        server.server_close()
    Journal(path).record(Observation(kind="transfer", ip="192.0.2.9", bytes_out=14))
    main(["--db", str(path), "show"])
    live = [json.loads(row) for row in capsys.readouterr().out.splitlines()]
    main(["--db", str(path), "replay", str(draft)])
    replay = [json.loads(row) for row in capsys.readouterr().out.splitlines()]
    assert live == replay
    assert all(r["version"] == version for r in live)
    assert [r["decision"] for r in live] == ["would deny", "would deny", "insufficient evidence"]
    assert live[0]["evidence"]["request_id"] == live[1]["evidence"]["request_id"]
    assert b"SENSITIVE" not in path.read_bytes() and b"private prompt" not in path.read_bytes()


def test_two_logical_rpcs_one_connection_and_observation_failure(tmp_path, monkeypatch):
    path = tmp_path / "audit.db"
    monkeypatch.setenv("BRIDGE_OUTBOUND_AUDIT_DB", str(path))
    for _ in range(2):
        with application_call("https://rpc.invalid:443", "inventory.v1.Lookup"):
            pass
    feed = Collector(Journal(path))
    feed.line(f"net\t{EPOCH+100}\t42\t10\t192.0.2.9\t443\t250")
    rows = [r["evidence"] for r in Journal(path).records()]
    assert len({r["request_id"] for r in rows if r["kind"] == "completed"}) == 2
    assert sum(r.get("bytes_out", 0) for r in rows) == 250
    monkeypatch.setenv("BRIDGE_OUTBOUND_AUDIT_DB", str(tmp_path / "missing" / "bad.db"))
    with application_call("https://rpc.invalid", "inventory.v1.Lookup"):
        pass
    with pytest.raises(ConnectionError):
        with application_call("https://rpc.invalid", "inventory.v1.Lookup"):
            raise ConnectionError("original network error")


@pytest.mark.skipif(os.name != "nt", reason="Windows process creation identity")
def test_registered_root_matches_real_child_handle(tmp_path, monkeypatch):
    path = tmp_path / "audit.db"
    monkeypatch.setenv("BRIDGE_OUTBOUND_AUDIT_DB", str(path))
    owner = {"agent":"custom", "run":"test-id"}
    token = current.set(owner)
    try:
        with subprocess.Popen([sys.executable, "-c", "pass"]) as process:
            register_process(process)
            with Journal(path).connect() as db:
                pid, birth = db.execute("SELECT pid,birth FROM roots").fetchone()
            assert pid == process.pid and birth > EPOCH
            assert Journal(path).owner(pid, birth) == owner
            assert Journal(path).owner(pid, birth + 1) is None
    finally:
        current.reset(token)


def test_invalid_policy_preserves_previous_version(tmp_path):
    book = Journal(tmp_path / "audit.db")
    original = Policy(default="allow", rules=[])
    book.activate(original)
    with pytest.raises(ValueError):
        Policy(default="deny", rules=[{"id":"r","effect":"allow","url":"https://secret"}])
    book.record(Observation(kind="attempt", hostname="example.net"))
    assert next(book.records())["version"] == original.version
