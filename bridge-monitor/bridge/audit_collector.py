"""Decode the allowlisted UTF-8 metadata protocol from the Windows ETW reader."""
import ipaddress
import time
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

    def flush(self):
        if self.pending:
            self.journal.record_many(self.pending)
            self.pending.clear()

    def status(self, kind, operation):
        self.flush()
        self.journal.record(Observation(kind=kind, layer="collector", operation=operation))

    def line(self, raw):
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
            self.processes.pop(int(parts[1]), None)
        elif tag == "end":
            _, pid, born = parts
            proc = self.processes.get(int(pid))
            if proc and proc.born == int(born):
                del self.processes[proc.pid]
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
                for ancestor in proc.ancestry:
                    owner = self.journal.owner(*ancestor)
                    if owner:
                        meta.update(owner)
                        break
            kind = "attempt" if opcode in (4, 5) else "connected" if opcode in (12, 28) else "transfer"
            self.pending.append(Observation(kind=kind,
                at_ns=(ticks-EPOCH)*100, pid=pid, ip=str(ip), port=int(port),
                cached_names=self.cached_names.get(str(ip), ()),
                transport=None if opcode in (4, 5) else "udp" if opcode in (42, 58) else "tcp",
                bytes_out=int(size) if kind == "transfer" else None, **meta))
            if len(self.pending) >= self.batch_size:
                self.flush()
        else:
            raise ValueError("unknown collector record")
