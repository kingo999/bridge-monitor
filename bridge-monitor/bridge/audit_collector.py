"""Decode the allowlisted UTF-8 metadata protocol from the Windows ETW reader."""
import ipaddress
import time
import sqlite3
from dataclasses import dataclass
from bridge.outbound_audit import Observation, address

EPOCH = 116444736000000000


@dataclass(frozen=True)
class Process:
    pid: int
    born: int
    parent_pid: int
    image: str
    ancestry: tuple


class Collector:
    def __init__(self, journal, local_addresses=(), cached_names=None, batch_size=1):
        self.journal = journal
        self.local = {address(ip) for ip in local_addresses}
        self.cached_names = cached_names or {}
        self.processes = {}
        self.last_health = 0
        self.pending = []
        self.batch_size = batch_size
        self.max_pending = 4096
        self.ended = {}
        self.dropped = 0
        self.lifetimes_unreliable = False

    def flush(self):
        if not self.pending and not self.ended and not self.dropped:
            return True
        rows = self.pending
        if self.dropped:
            gap = Observation(kind="gap", layer="collector", operation="audit buffer overflow",
                              lost_events=self.dropped)
            rows = [(gap, ())] + rows
        try:
            self.journal.record_collected(rows, self.ended)
        except sqlite3.OperationalError as error:
            if (getattr(error, "sqlite_errorcode", 0) & 255) not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise
            return False
        self.pending.clear()
        self.ended.clear()
        self.dropped = 0
        return True

    def enqueue(self, event, ancestry=()):
        if len(self.pending) >= self.max_pending:
            self.pending.pop(0)
            self.dropped += 1
        self.pending.append((event, () if self.lifetimes_unreliable else ancestry))
        if len(self.pending) >= self.batch_size:
            self.flush()

    def status(self, kind, operation):
        self.enqueue(Observation(kind=kind, layer="collector", operation=operation))
        self.flush()

    def line(self, raw):
        try:
            self._line(raw)
        except (ValueError, IndexError):
            self.processes.clear()
            self.status("gap", "invalid reader metadata")

    def _line(self, raw):
        parts = raw.rstrip("\r\n").split("\t")
        tag = parts[0]
        if tag == "health":
            self.flush()
            if time.monotonic() - self.last_health >= 30 or not self.last_health:
                self.status("health", "collector heartbeat")
                self.last_health = time.monotonic()
        elif tag == "gap":
            self.processes.clear()
            self.status("gap", "ETW evidence lost")
        elif tag == "unavailable":
            _, provider, code = parts
            if provider not in ("process", "network", "connect"):
                raise ValueError("unknown ETW provider")
            self.status("gap", f"{provider} provider unavailable (Windows error {int(code)})")
        elif tag == "start":
            _, pid, born, parent, image = parts
            pid, born, parent = int(pid), int(born), int(parent)
            prior = self.processes.get(parent)
            ancestors = prior.ancestry if prior and prior.born < born else ()
            self.processes[pid] = Process(pid, born, parent, image, ((pid, born),) + ancestors)
        elif tag == "unknown":
            _, pid, latest = parts
            proc = self.processes.get(int(pid))
            if proc and proc.born <= int(latest):
                self.processes.pop(proc.pid, None)
        elif tag == "end":
            _, pid, born, *ended_at = parts
            proc = self.processes.get(int(pid))
            if proc and proc.born == int(born):
                del self.processes[proc.pid]
            at = (int(ended_at[0])-EPOCH)*100 if ended_at else time.time_ns()
            key = (int(pid), int(born))
            if len(self.ended) >= self.max_pending and key not in self.ended:
                self.ended.clear()
                self.lifetimes_unreliable = True
                self.pending = [(event, ()) for event, _ in self.pending]
                self.status("gap", "process lifetime buffer overflow; attribution disabled until restart")
            self.ended[key] = min(at, self.ended.get(key, at))
            self.flush()
        elif tag == "net":
            _, ticks, pid, opcode, dest, port, size, *source = parts
            ip = ipaddress.ip_address(address(dest))
            if ip.is_loopback or str(ip) in self.local:
                return
            pid, ticks, opcode = int(pid), int(ticks), int(opcode)
            if opcode not in (4, 5, 10, 12, 26, 28, 42, 58):
                raise ValueError("unknown network opcode")
            proc = self.processes.get(pid)
            meta = {}
            if source:
                local_ip, local_port = source
                meta.update(local_ip=address(local_ip), local_port=int(local_port))
            if proc and proc.born <= ticks:
                meta.update(birth=proc.born, parent_pid=proc.parent_pid, image=proc.image)
            kind = "attempt" if opcode in (4, 5) else "connected" if opcode in (12, 28) else "transfer"
            self.enqueue(Observation(kind=kind,
                at_ns=(ticks-EPOCH)*100, pid=pid, ip=str(ip), port=int(port),
                cached_names=self.cached_names.get(str(ip), ()),
                transport=None if opcode in (4, 5) else "udp" if opcode in (42, 58) else "tcp",
                bytes_out=int(size) if kind == "transfer" else None, **meta),
                proc.ancestry if proc and proc.born <= ticks else ())
        else:
            raise ValueError("unknown collector record")
