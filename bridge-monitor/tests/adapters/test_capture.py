from datetime import datetime, timezone

from contracts.events import EventType
from bridge.telemetry.emitter import TelemetryEmitter
from bridge.telemetry.spool import DurableSpool


class Collector:
    def __init__(self):
        self.events = []
        self.content = []
        self.fail = False

    def ingest_batch(self, producer, events, content):
        if self.fail:
            raise ConnectionError("collector unavailable")
        self.events.extend(events)
        self.content.extend(content)
        return ["committed"] * len(events)


def test_outage_replay_preserves_prompt_and_response(tmp_path):
    collector = Collector()
    collector.fail = True
    spool = DurableSpool(tmp_path / "spool.sqlite", limit_bytes=100000)
    emitter = TelemetryEmitter(spool, collector, producer_id="claude-a", incarnation="boot-1")
    assert emitter.emit(EventType.MESSAGE_SENT, call_id="call-1", body=b"Exact prompt", role="user")
    assert emitter.emit(EventType.RESPONSE_DELTA, call_id="call-1", body=b"Partial answer", role="assistant")
    assert spool.pending_count() == 2
    reopened = DurableSpool(tmp_path / "spool.sqlite", limit_bytes=100000)
    collector.fail = False
    TelemetryEmitter(reopened, collector, producer_id="claude-a", incarnation="boot-1").replay()
    assert [event.event_type for event in collector.events] == [EventType.MESSAGE_SENT, EventType.RESPONSE_DELTA]
    assert [item.raw_bytes for item in collector.content] == [b"Exact prompt", b"Partial answer"]
    assert reopened.pending_count() == 0


def test_spool_round_trips_non_utf8_content_without_truncation(tmp_path):
    collector = Collector()
    collector.fail = True
    raw = bytes([0, 255, 128, 42])
    spool = DurableSpool(tmp_path / "binary.sqlite", limit_bytes=100000)
    emitter = TelemetryEmitter(spool, collector, producer_id="binary-a", incarnation="boot-1")
    assert emitter.emit(EventType.MESSAGE_SENT, call_id="call-binary", body=raw, role="user")
    collector.fail = False
    TelemetryEmitter(DurableSpool(tmp_path / "binary.sqlite", limit_bytes=100000), collector,
        producer_id="binary-a", incarnation="boot-1").replay()
    assert collector.content[0].raw_bytes == raw


def test_overflow_records_gap_and_degrades_admission(tmp_path):
    collector = Collector()
    collector.fail = True
    spool = DurableSpool(tmp_path / "spool.sqlite", limit_bytes=700)
    emitter = TelemetryEmitter(spool, collector, producer_id="qwen-a", incarnation="boot-1")
    assert not emitter.emit(EventType.MESSAGE_SENT, call_id="call-1", body=b"x" * 1000, role="user")
    assert spool.known_gaps() == 1
    assert not emitter.may_admit_new_work("reject_new_work")
    collector.fail = False
    emitter.replay()
    assert any(event.event_type == EventType.CAPTURE_GAP for event in collector.events)
    assert spool.known_gaps() == 1
