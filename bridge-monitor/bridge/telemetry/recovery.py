"""Explicit, single-head telemetry recovery. No runtime caller enables this API."""
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import re

from contracts.event_reader import read_event
from contracts.ingest_conflicts import CONFLICT_CODES, OperationConflict
from contracts.operations import candidate_event_hash

_TABLE = """CREATE TABLE IF NOT EXISTS spool_recoveries (
    recovery_id TEXT PRIMARY KEY, seq INTEGER NOT NULL UNIQUE,
    raw_sha256 TEXT NOT NULL, event_sha256 TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('quarantined','acknowledged')),
    record_json TEXT NOT NULL, event_json TEXT NOT NULL, content_json TEXT NOT NULL,
    retained_bytes INTEGER NOT NULL CHECK(retained_bytes>0))"""

class RecoveryError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)

@dataclass(frozen=True)
class HeadIdentity:
    sequence: int
    raw_sha256: str
    event_sha256: str

@dataclass(frozen=True)
class RecoveryReceipt:
    recovery_id: str
    action: str
    sequence: int
    raw_sha256: str
    event_sha256: str
    actor_id: str
    reason_code: str
    expected_conflict_code: str
    conflict_code: str | None
    collector_receipt: str | None
    recorded_at: str

@dataclass(frozen=True)
class RecoveryEvidence:
    receipt: RecoveryReceipt
    event_json: str
    content_json: str

def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)

def _raw_hash(row) -> str:
    return hashlib.sha256(b'bridge-monitor.spool-head.v1\n' + _json(list(row)).encode()).hexdigest()

def _bounded(value, limit=256) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit

class SpoolRecovery:
    def __init__(self, spool, collector, *, producer_id: str, allow_recovery: bool = False,
                 journal_limit_bytes: int | None = None):
        if type(allow_recovery) is not bool:
            raise TypeError('allow_recovery must be a boolean')
        if allow_recovery and getattr(spool, 'allow_operations', False) is not True:
            raise ValueError('operation-enabled spool required')
        if not _bounded(producer_id):
            raise ValueError('bounded producer identity required')
        bound = spool.limit_bytes if journal_limit_bytes is None else journal_limit_bytes
        if type(bound) is not int or not 0 < bound <= spool.limit_bytes:
            raise ValueError('journal limit must be a positive integer within spool limit')
        self.spool = spool
        self.collector = collector
        self.producer_id = producer_id
        self.allow_recovery = allow_recovery
        self.journal_limit_bytes = bound

    def _enabled(self):
        if not self.allow_recovery:
            raise RecoveryError('recovery_disabled')

    @staticmethod
    def _row(db):
        return db.execute('SELECT seq,event_json,content_json,size_bytes FROM entries ORDER BY seq LIMIT 1').fetchone()

    def _decode(self, row):
        if row is None:
            raise RecoveryError('head_changed')
        try:
            decoded = read_event(row[1], allow_candidate=True)
            if decoded.kind != 'operation_v2' or row[2] != 'null':
                raise RecoveryError('unsupported_head')
            if row[3] != len(row[1].encode()) + len(row[2].encode()):
                raise RecoveryError('invalid_head')
            event = decoded.event
        except RecoveryError:
            raise
        except Exception:
            raise RecoveryError('invalid_head') from None
        if event.producer_id != self.producer_id:
            raise RecoveryError('producer_mismatch')
        return HeadIdentity(row[0], _raw_hash(row), candidate_event_hash(event)), event

    def inspect_head(self) -> HeadIdentity | None:
        self._enabled()
        with closing(self.spool._connect()) as db:
            row = self._row(db)
        return self._decode(row)[0] if row is not None else None

    @staticmethod
    def _has_table(db):
        return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='spool_recoveries'").fetchone())

    def _lookup(self, recovery_id):
        with closing(self.spool._connect()) as db:
            if not self._has_table(db):
                return None
            row = db.execute('SELECT seq,event_json,content_json,raw_sha256,event_sha256,action,record_json,retained_bytes FROM spool_recoveries WHERE recovery_id=?',
                             (recovery_id,)).fetchone()
        if row is None:
            return None
        try:
            record = json.loads(row[6])
            receipt = RecoveryReceipt(**record['receipt'])
            source = (row[0], row[1], row[2], len(row[1].encode()) + len(row[2].encode()))
            decoded = read_event(row[1], allow_candidate=True)
            if (decoded.kind != 'operation_v2' or row[2] != 'null' or
                row[3] != _raw_hash(source) or row[4] != candidate_event_hash(decoded.event) or
                receipt.recovery_id != recovery_id or receipt.sequence != row[0] or
                receipt.raw_sha256 != row[3] or receipt.event_sha256 != row[4] or
                receipt.action != row[5] or row[7] != source[3]+len(row[6].encode())):
                raise ValueError('invalid retained evidence')
        except Exception:
            raise RecoveryError('journal_corrupt') from None
        if decoded.event.producer_id != self.producer_id:
            raise RecoveryError('producer_mismatch')
        return record['request'], RecoveryEvidence(receipt, row[1], row[2])

    def evidence(self, recovery_id: str) -> RecoveryEvidence | None:
        self._enabled()
        if not _bounded(recovery_id):
            raise RecoveryError('invalid_metadata')
        result = self._lookup(recovery_id)
        return result[1] if result else None

    def _usage(self, db):
        if not self._has_table(db):
            return 0
        return db.execute('SELECT coalesce(sum(retained_bytes),0) FROM spool_recoveries').fetchone()[0]

    def _persist(self, row, request, receipt):
        record_json = _json(dict(request=request, receipt=asdict(receipt)))
        retained = len(row[1].encode()) + len(row[2].encode()) + len(record_json.encode())
        with closing(self.spool._connect()) as db:
            with db:
                db.execute('BEGIN IMMEDIATE')
                if self._row(db) != row:
                    raise RecoveryError('head_changed')
                if self._usage(db) + retained > self.journal_limit_bytes:
                    raise RecoveryError('journal_full')
                db.execute(_TABLE)
                db.execute('INSERT INTO spool_recoveries(recovery_id,seq,raw_sha256,event_sha256,action,record_json,event_json,content_json,retained_bytes) VALUES(?,?,?,?,?,?,?,?,?)',
                           (receipt.recovery_id,row[0],receipt.raw_sha256,receipt.event_sha256,
                            receipt.action,record_json,row[1],row[2],retained))
                removed = db.execute('DELETE FROM entries WHERE seq=? AND event_json=? AND content_json=? AND size_bytes=?', row)
                if removed.rowcount != 1:
                    raise RecoveryError('head_changed')
        return receipt

    def recover_head(self, expected: HeadIdentity, *, recovery_id: str, actor_id: str,
                     reason_code: str, expected_conflict_code: str) -> RecoveryReceipt:
        self._enabled()
        if (not isinstance(expected, HeadIdentity) or type(expected.sequence) is not int or expected.sequence <= 0 or
            any(not isinstance(h,str) or not re.fullmatch('[a-f0-9]{64}',h) for h in [expected.raw_sha256,expected.event_sha256]) or
            not _bounded(recovery_id) or not _bounded(actor_id) or not isinstance(reason_code,str) or
            not re.fullmatch('[a-z][a-z0-9_.-]{0,127}',reason_code) or
            not isinstance(expected_conflict_code,str) or expected_conflict_code not in CONFLICT_CODES):
            raise RecoveryError('invalid_metadata')
        request = dict(expected=asdict(expected),producer_id=self.producer_id,recovery_id=recovery_id,
                       actor_id=actor_id,reason_code=reason_code,expected_conflict_code=expected_conflict_code)
        with self.spool.drain_lock():
            previous = self._lookup(recovery_id)
            if previous:
                if previous[0] != request:
                    raise RecoveryError('recovery_id_conflict')
                return previous[1].receipt
            with closing(self.spool._connect()) as db:
                row = self._row(db)
            actual, event = self._decode(row)
            if actual != expected:
                raise RecoveryError('head_changed')
            timestamp = datetime.now(timezone.utc).isoformat()
            def receipt(action, conflict=None, collector=None):
                return RecoveryReceipt(recovery_id,action,row[0],actual.raw_sha256,actual.event_sha256,
                                       actor_id,reason_code,expected_conflict_code,conflict,collector,timestamp)
            choices = [receipt('quarantined',expected_conflict_code),
                       receipt('acknowledged',collector='committed'),receipt('acknowledged',collector='duplicate')]
            reservation = max(len(row[1].encode())+len(row[2].encode())+
                              len(_json(dict(request=request,receipt=asdict(item))).encode()) for item in choices)
            with closing(self.spool._connect()) as db:
                if self._usage(db)+reservation > self.journal_limit_bytes:
                    raise RecoveryError('journal_full')
            try:
                receipts = self.collector.ingest_batch(self.producer_id,[event],[])
            except OperationConflict as error:
                if error.code != expected_conflict_code or error.event_sha256 != actual.event_sha256:
                    raise RecoveryError('conflict_evidence_mismatch') from None
                result = choices[0]
            except Exception:
                raise RecoveryError('conflict_not_confirmed') from None
            else:
                if not isinstance(receipts,(list,tuple)) or len(receipts)!=1 or receipts[0] not in ('committed','duplicate'):
                    raise RecoveryError('conflict_not_confirmed')
                result = choices[1] if receipts[0]=='committed' else choices[2]
            return self._persist(row,request,result)
