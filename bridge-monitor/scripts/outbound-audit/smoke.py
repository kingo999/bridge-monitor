"""Exercise the built reader with this process's loopback traffic only (requires ETW access)."""
import os
import queue
import signal
import socket
import subprocess
import sys
import threading
import time

def main():
    reader = subprocess.Popen([sys.argv[1], "--only-pid", str(os.getpid())],
                              stdout=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    lines = queue.Queue()
    def receive():
        for line in reader.stdout:
            lines.put(line.strip())
        lines.put(None)
    threading.Thread(target=receive, daemon=True).start()
    try:
        first = lines.get(timeout=10)
        send_available = True
        fallback = False
        while first and first.startswith("unavailable"):
            if first.startswith("unavailable\tnetwork\t"):
                send_available = False
            if first.startswith("unavailable\tprocess\t"):
                fallback = True
            print(first, file=sys.stderr)
            first = lines.get(timeout=10)
        if first != "health":
            raise RuntimeError(f"ETW reader did not start (exit={reader.poll()})")
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            dest = server.getsockname()
            with socket.create_connection(dest) as client, server.accept()[0] as peer:
                client.sendall(b"audit-canary")
                assert peer.recv(12) == b"audit-canary"
        deadline = time.monotonic() + 12
        sender_seen = False
        while time.monotonic() < deadline:
            row = lines.get(timeout=3)
            if row is None:
                break
            fields = row.split("\t")
            if fields[0] == "start" and len(fields) == 5 and fields[1] == str(os.getpid()):
                sender_seen = int(fields[2]) > 0 and fields[3] == str(os.getppid())
            if row.startswith("net\t") or row.startswith("gap\t"):
                print(row)
            if (not send_available and len(fields) == 7 and fields[0] == "net" and
                    fields[2:6] == [str(os.getpid()), "4", dest[0], str(dest[1])]):
                print("Connect attempt verified; kernel transfer provider unavailable")
                assert sender_seen or not fallback, "the live process fallback did not identify the sender and its parent"
                return
            if (len(fields) == 9 and fields[0] == "net" and fields[2:6] ==
                    [str(os.getpid()), "10", dest[0], str(dest[1])] and int(fields[6]) >= 12):
                print("Observed the test PID sending to its controlled receiver")
                return
        raise AssertionError("no matching TCP send event")
    finally:
        if reader.poll() is None:
            reader.send_signal(signal.CTRL_BREAK_EVENT)
        reader.wait(timeout=15)

if __name__ == "__main__":
    main()
