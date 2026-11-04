"""Verify a test PID's socket evidence using loopback or an operator-selected HTTP receiver."""
import argparse
import ipaddress
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bridge.audit_cli import stop_reader, refresh_addresses
from bridge.audit_collector import Collector


def normalized(ip):
    value = ipaddress.ip_address(ip)
    return str(getattr(value, "ipv4_mapped", None) or value)


@contextmanager
def transfer(host, port, path, local_addresses=()):
    """Expose the actual peer and bytes sent. Remote mode uses a synthetic plain HTTP GET."""
    if host is not None:
        name = f"[{host}]" if ":" in host else host
        request = f"GET {path} HTTP/1.1\r\nHost: {name}:{port}\r\nConnection: close\r\n\r\n".encode("ascii")
        with socket.create_connection((host, port), timeout=8) as client:
            peer_ip, peer_port, *_ = client.getpeername()
            local_port = client.getsockname()[1]
            client.sendall(request)
            # Retain only the status line, never the receiver's headers or body.
            status = bytearray()
            while len(status) < 256 and not status.endswith(b"\r\n"):
                part = client.recv(1)
                if not part:
                    break
                status.extend(part)
            if not re.fullmatch(rb"HTTP/1\.[01] [1-5][0-9]{2}(?: [^\r\n]*)?\r\n", status):
                raise RuntimeError("receiver did not acknowledge the HTTP request")
            remote = normalized(peer_ip)
            off_machine = not ipaddress.ip_address(remote).is_loopback and remote not in local_addresses
            yield dict(ip=remote, port=peer_port, local_port=local_port,
                       sent=len(request), remote_status=int(status.split()[1]), off_machine=off_machine)
    else:
        with socket.socket() as server:
            server.settimeout(8)
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            with socket.create_connection(server.getsockname(), timeout=8) as client, server.accept()[0] as peer:
                peer.settimeout(8)
                client.sendall(b"audit-canary")
                received = b""
                while len(received) < 12:
                    part = peer.recv(12 - len(received))
                    if not part:
                        raise RuntimeError("controlled receiver closed before reading the canary")
                    received += part
                if received != b"audit-canary":
                    raise RuntimeError("controlled receiver got a different canary")
                yield dict(ip="127.0.0.1", port=server.getsockname()[1], local_port=client.getsockname()[1],
                           sent=12, off_machine=False)


def verify(reader_path, host=None, port=None, path="/", require_kernel=False):
    local = Collector(None)
    if host:
        refresh_addresses(local)
    reader = subprocess.Popen([str(reader_path), "--only-pid", str(os.getpid())],
                              stdout=subprocess.PIPE, encoding="utf-8",
                              creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    lines = queue.Queue()
    def receive():
        for line in reader.stdout:
            lines.put(line.strip())
        lines.put(None)
    threading.Thread(target=receive, daemon=True).start()
    try:
        first = lines.get(timeout=10)
        unavailable = []
        while first and first.startswith("unavailable\t"):
            unavailable.append(first.split("\t")[1])
            first = lines.get(timeout=10)
        if first != "health" or (require_kernel and unavailable):
            raise RuntimeError("required trace providers unavailable: " + ",".join(unavailable))
        with transfer(host, port, path, local.local) as sent:
            total = 0
            sender_seen = attempted = False
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                row = lines.get(timeout=3)
                if row is None or row.startswith(("gap\t", "unavailable\t")):
                    raise RuntimeError("trace ended or evidence was lost")
                fields = row.split("\t")
                if fields[0] == "start" and len(fields) == 5 and int(fields[1]) == os.getpid():
                    sender_seen = int(fields[2]) > 0 and int(fields[3]) == os.getppid()
                if fields[0] != "net" or len(fields) not in (7, 9):
                    continue
                if int(fields[2]) != os.getpid() or normalized(fields[4]) != sent["ip"] or int(fields[5]) != sent["port"]:
                    continue
                opcode = int(fields[3])
                attempted = attempted or opcode in (4, 5)
                if opcode in (10, 26) and len(fields) == 9 and int(fields[8]) == sent["local_port"]:
                    total += int(fields[6])
                if sender_seen and (total >= sent["sent"] or ("network" in unavailable and attempted)):
                    if "network" not in unavailable and total != sent["sent"]:
                        raise RuntimeError("kernel byte count differs from synthetic request")
                    return dict(status="partial" if "network" in unavailable else "passed",
                                kernel_bytes=None if "network" in unavailable else total,
                                unavailable=unavailable, sender_verified=True, **sent)
            raise RuntimeError("no matching sender/transfer evidence before the deadline")
    finally:
        stop_reader(reader)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reader", type=Path)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--path", default="/")
    parser.add_argument("--require-kernel", action="store_true")
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    if bool(args.host) != (args.port is not None) or (args.port is not None and not 1 <= args.port <= 65535):
        parser.error("--host and a valid --port must be supplied together")
    if not re.fullmatch(r"/[A-Za-z0-9_./-]*", args.path) or (args.host and any(c.isspace() or c in "/?#@" for c in args.host)):
        parser.error("use a hostname/IP and a static HTTP path without credentials or query parameters")
    try:
        result = verify(args.reader.resolve(strict=True), args.host, args.port, args.path, args.require_kernel)
    except (OSError, RuntimeError, queue.Empty) as error:
        result = dict(status="failed", error=type(error).__name__)
        if isinstance(error, RuntimeError):
            result["reason"] = str(error)
    if args.result:
        args.result.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    return 0 if result["status"] == "passed" else 2 if result["status"] == "partial" else 1


if __name__ == "__main__":
    raise SystemExit(main())
