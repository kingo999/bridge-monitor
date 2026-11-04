from concurrent.futures import ThreadPoolExecutor

import pytest
from bridge.audit_hooks import application_call
from bridge.outbound_audit import Journal


def test_instrumented_rpcs_reuse_one_http2_connection(tmp_path, monkeypatch):
    grpc = pytest.importorskip("grpc", reason="optional gRPC integration fixture")
    book = tmp_path / "rpc.db"
    monkeypatch.setenv("BRIDGE_OUTBOUND_AUDIT_DB", str(book))
    peers = []
    def echo(request, context):
        peers.append(context.peer())
        return request
    with ThreadPoolExecutor(max_workers=2) as pool:
        server = grpc.server(pool)
        server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler("audit.Probe",
            {"Echo": grpc.unary_unary_rpc_method_handler(echo)}),))
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()
        try:
            # Give an isolated loopback server a logical authority for application-layer evidence.
            with grpc.insecure_channel(f"127.0.0.1:{port}",
                    options=(("grpc.default_authority", "rpc.test"),)) as channel:
                echo_rpc = channel.unary_unary("/audit.Probe/Echo")
                for body in (b"private one", b"private two"):
                    with application_call(f"http://rpc.test:{port}", "audit.Probe/Echo", category=None):
                        assert echo_rpc(body, timeout=5) == body
        finally:
            server.stop(0).wait()
    assert len(peers) == 2 and peers[0] == peers[1]
    rows = [r["evidence"] for r in Journal(book).records()]
    assert [r["kind"] for r in rows] == ["attempt", "completed"] * 2
    assert len({r["request_id"] for r in rows}) == 2
    assert all("bytes_out" not in r for r in rows)
    assert b"private" not in book.read_bytes()
