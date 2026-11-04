"""Run with python -m bridge.audit_cli --db PATH COMMAND."""
import argparse
import json
import os
import signal
import ctypes
import subprocess
import time
from pathlib import Path

from bridge.audit_collector import Collector
from bridge.outbound_audit import Journal, Policy, address, dns_name


def refresh_addresses(collector):
    # These are local tables. No reverse-DNS queries are made on the agent's behalf.
    script = """$ErrorActionPreference='Stop'
    $ips = @(Get-NetIPAddress | ForEach-Object IPAddress)
    $names = @(Get-DnsClientCache -ErrorAction SilentlyContinue | Where-Object { $_.Type -in 1,28 } |
        Select-Object Entry,Data)
    @{ ips=$ips; names=$names } | ConvertTo-Json -Compress"""
    raw = subprocess.check_output(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                  text=True, timeout=15)
    tables = json.loads(raw)
    collector.local = {address(ip) for ip in tables["ips"]}
    names = {}
    for item in tables["names"]:
        try:
            ip, name = address(item["Data"]), dns_name(item["Entry"])
            if ip and name:
                names.setdefault(ip, set()).add(name)
        except (ValueError, UnicodeError):
            continue
    collector.cached_names = {ip: tuple(sorted(items)) for ip, items in names.items()}


def stop_reader(child):
    try:
        if child.poll() is None:
            child.send_signal(signal.CTRL_BREAK_EVENT)
        rc = child.wait(timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        child.kill()
        rc = child.wait(timeout=5)
    if rc:
        # An abnormal ETW producer exit can leave its kernel session alive. This name is
        # derived solely from the child we launched, never from reader output.
        subprocess.run(["logman.exe", "stop", f"BridgeOutboundAudit-{child.pid}", "-ets"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
    return rc


def observe(journal, reader):
    collector = Collector(journal, batch_size=128)
    refresh_addresses(collector)
    refreshed = time.monotonic()
    collector.status("gap", "starting collector; earlier host activity unknown")
    with subprocess.Popen([str(reader.resolve(strict=True))], stdout=subprocess.PIPE,
                          encoding="utf-8", creationflags=subprocess.CREATE_NEW_PROCESS_GROUP) as child:
        try:
            for line in child.stdout:
                if time.monotonic() - refreshed > 60:
                    refreshed = time.monotonic()
                    try:
                        refresh_addresses(collector)
                    except (OSError, ValueError, subprocess.SubprocessError):
                        collector.cached_names.clear()
                        collector.status("gap", "local network tables unavailable")
                collector.line(line)
        finally:
            rc = stop_reader(child)
            collector.status("gap", "collector stopped")
    if rc:
        raise SystemExit(f"ETW reader exited with code {rc}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bridge outbound audit (observe only)")
    parser.add_argument("--db", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show").add_argument("--after", type=int, default=0)
    for cmd in ("policy", "replay"):
        commands.add_parser(cmd).add_argument("file", type=Path)
    commands.add_parser("collect").add_argument("reader", type=Path)
    root = commands.add_parser("register", help="associate a running Windows worker with its agent and run")
    root.add_argument("--pid", required=True, type=int)
    root.add_argument("--agent", required=True)
    root.add_argument("--run", required=True)
    root.add_argument("--task")
    args = parser.parse_args(argv)
    draft = Policy.model_validate_json(args.file.read_text(encoding="utf-8")) if hasattr(args, "file") else None
    journal = Journal(args.db)
    if args.command == "policy":
        journal.activate(draft)
        print(draft.version)
    elif args.command == "register":
        if os.name != "nt" or args.pid <= 0:
            parser.error("a positive Windows process ID is required")
        from bridge.audit_hooks import creation_time
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, args.pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            journal.register(args.pid, creation_time(handle),
                             dict(agent=args.agent, run=args.run, task=args.task))
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
    elif args.command == "collect":
        if os.name != "nt":
            parser.error("the host collector requires Windows")
        import msvcrt
        with open(str(args.db.resolve()) + ".collector.lock", "a+b") as lock:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            observe(journal, args.reader)
    else:
        for record in journal.records(draft, after=getattr(args, "after", 0)):
            print(json.dumps(record))


if __name__ == "__main__":
    main()
