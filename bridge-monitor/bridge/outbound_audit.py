"""Metadata-only outbound evidence and deterministic, observe-only policy evaluation."""
import hashlib
import ipaddress
import json
import socket
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def address(value):
    if value is None:
        return None
    ip = ipaddress.ip_address(value)
    return str(getattr(ip, "ipv4_mapped", None) or ip)


def dns_name(value):
    if value is None:
        return None
    import re
    name = value.rstrip(".").encode("idna").decode("ascii").lower()
    if len(name) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part)
                                  for part in name.split(".")):
        raise ValueError("invalid hostname")
    return name


class Fields(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent: str | None = None
    hostname: str | None = None   # Requested name only, never inferred from an IP.
    ip: str | None = None
    port: int | None = Field(default=None, ge=0, le=65535)
    transport: Literal["tcp", "udp"] | None = None

    _normalize_ip = field_validator("ip")(address)
    _normalize_name = field_validator("hostname")(dns_name)


class Observation(Fields):
    kind: Literal["attempt", "connected", "transfer", "completed", "failed", "gap", "health"]
    layer: Literal["host", "application", "collector"] = "host"
    at_ns: int = Field(default_factory=time.time_ns)
    host: str = Field(default_factory=socket.gethostname)
    pid: int | None = None
    birth: int | None = None   # Windows creation FILETIME, including its 100 ns precision.
    parent_pid: int | None = None
    local_ip: str | None = None
    local_port: int | None = None
    peer_ip: str | None = None
    peer_port: int | None = None
    image: str | None = None
    cached_names: tuple[str, ...] = ()  # Passive local DNS evidence; not a requested name.
    task: str | None = None
    run: str | None = None
    model: str | None = None
    operation: str | None = None
    request_id: str | None = None
    status: int | None = None
    bytes_out: int | None = Field(default=None, ge=0)
    category: Literal["task brief", "control"] | None = None


class Rule(Fields):
    id: str = Field(min_length=1, max_length=128)
    effect: Literal["allow", "deny"]


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    default: Literal["allow", "deny"]
    rules: tuple[Rule, ...]

    @model_validator(mode="after")
    def unique_rules(self):
        if len({r.id for r in self.rules}) != len(self.rules):
            raise ValueError("duplicate rule id")
        return self

    @property
    def version(self):
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()

    def evaluate(self, event):
        # Consider every possible outcome when a predicate lacks evidence.
        possible = set()
        for effect in ("deny", "allow"):
            for rule in self.rules:
                if rule.effect != effect:
                    continue
                pairs = [(getattr(rule, key), getattr(event, key)) for key in Fields.model_fields
                         if getattr(rule, key) is not None]
                if any(actual is not None and expected != actual for expected, actual in pairs):
                    continue
                possible.add(effect)
                if all(actual is not None for _, actual in pairs):
                    if len(possible) > 1:
                        return "insufficient evidence", None
                    return "would " + effect, rule.id
        possible.add(self.default)
        if len(possible) > 1:
            return "insufficient evidence", None
        return "would " + self.default, "default"


class Journal:
    def __init__(self, path):
        self.path = Path(path)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS policy(version TEXT PRIMARY KEY, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS active(singleton INTEGER PRIMARY KEY CHECK(singleton=1), version TEXT);
                CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY, evidence TEXT NOT NULL,
                    version TEXT, decision TEXT, matched TEXT);
                CREATE TABLE IF NOT EXISTS roots(host TEXT, pid INTEGER, birth INTEGER, owner TEXT NOT NULL,
                    PRIMARY KEY(host,pid,birth));
            """)

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=1)) as db, db:
            yield db

    def activate(self, policy):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO policy VALUES (?,?)", (policy.version, policy.model_dump_json()))
            db.execute("INSERT OR REPLACE INTO active VALUES (1,?)", (policy.version,))

    def record(self, event):
        self.record_many((event,))

    def record_many(self, events):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT version,document FROM active JOIN policy USING(version)").fetchone()
            policy = Policy.model_validate_json(row[1]) if row else None
            for event in events:
                version = decision = matched = None
                if policy and event.layer != "collector":
                    version = row[0]
                    decision, matched = policy.evaluate(event)
                db.execute("INSERT INTO observations(evidence,version,decision,matched) VALUES (?,?,?,?)",
                           (event.model_dump_json(exclude_none=True), version, decision, matched))

    def records(self, policy=None, after=0):
        with self.connect() as db:
            for i, body, version, decision, matched in db.execute(
                    "SELECT * FROM observations WHERE id>? ORDER BY id", (after,)):
                event = Observation.model_validate_json(body)
                if policy and event.layer != "collector":
                    version = policy.version
                    decision, matched = policy.evaluate(event)
                yield dict(id=i, evidence=json.loads(body), version=version, decision=decision, matched=matched)

    def register(self, pid, birth, owner):
        with self.connect() as db:
            key = (socket.gethostname(), pid, birth)
            encoded = json.dumps(owner, sort_keys=True)
            db.execute("INSERT OR IGNORE INTO roots VALUES (?,?,?,?)", (*key, encoded))
            if db.execute("SELECT owner FROM roots WHERE host=? AND pid=? AND birth=?", key).fetchone()[0] != encoded:
                raise ValueError("process instance already has a different owner")

    def owner(self, pid, birth):
        with self.connect() as db:
            row = db.execute("SELECT owner FROM roots WHERE host=? AND pid=? AND birth=?",
                             (socket.gethostname(), pid, birth)).fetchone()
            return json.loads(row[0]) if row else None
