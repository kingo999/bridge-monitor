"""Live classifier activity is read-only and separate from production capacity."""
import psycopg
import threading
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo

from bridge.classifier import ingest, store
from service.http.app import create_app
from tests.classifier.conftest import table_counts


def test_activity_reports_queue_dispatch_and_completion_without_bodies(cdsn, cfg):
    from service.queries.classifier_activity import query_classifier_activity
    before = table_counts(cdsn)
    first = ingest.capture_text(cdsn, cfg, origin_kind='controlled', origin_ref='live-1',
        request='private prompt', source='private source', facts={}).observation_id
    queued = query_classifier_activity(cdsn)
    assert queued['queued'] == 1 and queued['classifying'] == 0
    assert queued['latest']['observation_id'] == first
    assert queued['latest']['state'] == 'captured'
    store.claim_next(cdsn, 'test-activity', store.utcnow())
    active = query_classifier_activity(cdsn)
    assert active['queued'] == 0 and active['classifying'] == 1
    assert active['latest']['state'] == 'dispatching'
    store.finish(cdsn, first, state='completed', fields={
        'completed_at': store.utcnow(), 'rec_route': 'paid', 'valid': True},
        response=b'private response', response_complete=True)
    done = query_classifier_activity(cdsn)
    assert done['queued'] == done['classifying'] == 0
    assert done['latest']['state'] == 'completed' and done['latest']['rec_route'] == 'paid'
    assert done['latest']['completed_at'] is not None
    assert 'private' not in str(done)
    assert table_counts(cdsn) == before


def test_activity_requires_operator_and_handles_absent_classifier_schema(cdsn):
    from service.queries.classifier_activity import query_classifier_activity
    client = TestClient(create_app(cdsn, producer_tokens={'p': 'producer-token'},
                                   operator_token='operator-token', payload_limit_bytes=1048576))
    path = '/api/v1/classifier/activity'
    assert client.get(path).status_code == 401
    assert client.get(path, headers={'Authorization': 'Bearer producer-token'}).status_code == 401
    response = client.get(path, headers={'Authorization': 'Bearer operator-token'})
    assert response.status_code == 200 and response.json()['queued'] == 0
    missing = query_classifier_activity(make_conninfo(cdsn, options='-c search_path=pg_catalog'))
    assert missing['available'] is False and missing['queued'] is None
    assert missing['classifying'] is None and missing['latest'] is None


def test_activity_database_failure_is_generic_unavailable(cdsn, monkeypatch):
    import service.http.query as query
    monkeypatch.setattr(query, 'query_classifier_activity', lambda _: (_ for _ in ()).throw(
        psycopg.OperationalError('private connection details')))
    client = TestClient(create_app(cdsn, producer_tokens={'p': 'producer-token'}, operator_token='operator-token',
                                   payload_limit_bytes=1048576), raise_server_exceptions=False)
    response = client.get('/api/v1/classifier/activity',
                          headers={'Authorization': 'Bearer operator-token'})
    assert response.status_code == 503
    assert response.json()['detail'] == 'Classifier activity unavailable'
    assert 'private' not in response.text


def test_periodic_cohort_read_times_out_on_database_lock(cdsn):
    locked = threading.Event()
    release = threading.Event()
    def hold_lock():
        with psycopg.connect(cdsn) as conn:
            conn.execute('LOCK TABLE classifier_observations IN ACCESS EXCLUSIVE MODE')
            locked.set()
            release.wait(2)
    worker = threading.Thread(target=hold_lock)
    worker.start()
    assert locked.wait(2)
    try:
        client = TestClient(create_app(cdsn, producer_tokens={'p': 'producer-token'},
            operator_token='operator-token', payload_limit_bytes=1048576), raise_server_exceptions=False)
        response = client.get('/api/v1/classifier/cohort',
                              headers={'Authorization': 'Bearer operator-token'})
        assert response.status_code == 503
        assert response.json()['detail'] == 'Classifier cohort unavailable'
    finally:
        release.set()
        worker.join(3)
