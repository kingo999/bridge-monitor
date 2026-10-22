"""Fixture-only adapters and effect sentinels; never imported by product code."""
import copy
import json
import socket
import subprocess
from pathlib import Path

import psycopg
import pytest

DIRECTORY = Path(__file__).parents[1] / 'fixtures' / 'operation_lifecycle'


def candidate(kind='started'):
    values = json.loads((DIRECTORY / 'candidate-v2.json').read_text())
    return copy.deepcopy(next(v for v in values if v['event_type'] == 'operation.' + kind))


def golden():
    return json.loads((DIRECTORY / 'legacy-golden.json').read_text())


@pytest.fixture
def no_effects(monkeypatch):
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError('External effect attempted')
    monkeypatch.setattr(psycopg, 'connect', forbidden)
    monkeypatch.setattr(psycopg.Connection, 'connect', classmethod(forbidden))
    monkeypatch.setattr(psycopg.AsyncConnection, 'connect', classmethod(forbidden))
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    yield calls
    assert calls == []
