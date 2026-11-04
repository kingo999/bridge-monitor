"""Opt-in hooks. Audit failures never alter an adapter's network decision."""
import contextvars
import ctypes
import functools
import inspect
import ipaddress
import logging
import os
import re
import sys
import uuid
from contextlib import contextmanager
from urllib.parse import urlsplit

from bridge.outbound_audit import Journal, Observation

current = contextvars.ContextVar("bridge_audit_run", default={})
log = logging.getLogger(__name__)


@functools.lru_cache(maxsize=4)
def _journal(path):
    return Journal(path)


def _observe(action):
    path = os.environ.get("BRIDGE_OUTBOUND_AUDIT_DB")
    if path:
        try:
            action(_journal(path))
        except Exception as error:
            log.warning("Outbound audit evidence unavailable (%s)", type(error).__name__)


def agent_run(function):
    signature = inspect.signature(function)

    @functools.wraps(function)
    def scoped(self, *args, **kwargs):
        if not os.environ.get("BRIDGE_OUTBOUND_AUDIT_DB"):
            return function(self, *args, **kwargs)
        values = signature.bind(self, *args, **kwargs).arguments
        lease = values["lease"]
        token = current.set(dict(agent=type(self).__name__, task=getattr(values.get("task"), "task_id", None),
                                 run=str(lease.call_id), model=getattr(self, "model", None)))
        try:
            return function(self, *args, **kwargs)
        finally:
            current.reset(token)
    return scoped


def creation_time(handle):
    stamps = [ctypes.c_uint64() for _ in range(4)]
    if not ctypes.windll.kernel32.GetProcessTimes(ctypes.c_void_p(int(handle)),
                                                  *(ctypes.byref(s) for s in stamps)):
        raise OSError("GetProcessTimes")
    return stamps[0].value


def register_process(process):
    def register(journal):
        if os.name != "nt":
            return
        if current.get():
            journal.register(process.pid, creation_time(process._handle), current.get())
    _observe(register)


@contextmanager
def application_call(endpoint, operation, *, category=None, transport="tcp"):
    """One logical HTTP/RPC call. Use only a static operation name, never a URL/path as operation."""
    details = None
    state = {}
    if os.environ.get("BRIDGE_OUTBOUND_AUDIT_DB"):
        try:
            url = urlsplit(endpoint)
            host = url.hostname
            try:
                ip = ipaddress.ip_address(host)
                ip = getattr(ip, "ipv4_mapped", None) or ip
            except ValueError:
                ip = None
            if host and host.rstrip(".").lower() != "localhost" and not (ip and ip.is_loopback):
                safe_operation = operation if re.fullmatch(r"/?[A-Za-z][\w.-]*(?:/[A-Za-z][\w.-]*)?", operation) else None
                details = dict(layer="application", pid=os.getpid(), parent_pid=os.getppid(),
                               image=os.path.basename(sys.executable),
                               request_id=str(uuid.uuid4()), operation=safe_operation,
                               category=category, transport=transport, **current.get(),
                               hostname=None if ip else host, ip=str(ip) if ip else None,
                               port=url.port or (443 if url.scheme == "https" else 80))
        except (ValueError, TypeError):
            log.warning("Outbound audit endpoint metadata unavailable")
    def emit(kind):
        if details is not None:
            fields = {k: state[k] for k in ("status", "local_ip", "local_port", "peer_ip", "peer_port") if k in state}
            _observe(lambda journal: journal.record(Observation(kind=kind, **details, **fields)))
    emit("attempt")
    try:
        yield state
    except BaseException:
        emit("failed")
        raise
    else:
        emit("completed")


class ObservedClient:
    """Wrap the adapter's three HTTP entry points; never inspect headers, bodies or response text."""
    def __init__(self, client, agent=None):
        self.client = client
        self.agent = agent

    @contextmanager
    def call(self, url, operation, category):
        token = current.set({"agent": self.agent, **current.get()})
        try:
            with application_call(url, operation, category=category) as state:
                yield state
        finally:
            current.reset(token)

    def close(self):
        return self.client.close()

    @staticmethod
    def response_metadata(state, result):
        state["status"] = result.status_code
        try:
            stream = result.extensions.get("network_stream")
            sock = stream.get_extra_info("socket") if stream else None
            if sock:
                for prefix, getter in (("local", sock.getsockname), ("peer", sock.getpeername)):
                    ip, port, *_ = getter()
                    state[prefix + "_ip"] = str(ipaddress.ip_address(ip))
                    state[prefix + "_port"] = int(port)
        except (AttributeError, OSError, TypeError, ValueError):
            pass  # Closed, opaque, or non-IP transport.

    def get(self, url, **kwargs):
        with self.call(url, "GET", "control") as state:
            result = self.client.get(url, **kwargs)
            self.response_metadata(state, result)
            return result

    def post(self, url, **kwargs):
        category = "task brief" if urlsplit(url).path.endswith("/v1/jobs") else "control"
        with self.call(url, "POST", category) as state:
            result = self.client.post(url, **kwargs)
            self.response_metadata(state, result)
            return result

    @contextmanager
    def stream(self, method, url, **kwargs):
        with self.call(url, method, "task brief") as state:
            with self.client.stream(method, url, **kwargs) as result:
                self.response_metadata(state, result)
                yield result


def observe_client(client, agent=None):
    if isinstance(client, ObservedClient) or not os.environ.get("BRIDGE_OUTBOUND_AUDIT_DB"):
        return client
    return ObservedClient(client, agent)
