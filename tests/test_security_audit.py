"""Exercise submission audit boundaries with real HTTP routing and temporary SQLite."""
import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

TOKEN = 'test-only-audit-reader-' + 'a' * 32
AUTH = {'Authorization': 'Bearer ' + TOKEN}
PARAMS = dict(width=64, height=64, tile_size=32, max_iter=1,
              x_min=-2.2, x_max=1, y_min=-1.2, y_max=1.2)
JOB = {'task_type': 'mandelbrot_v1', 'params': PARAMS}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('TG_DB_PATH', str(tmp_path / 'audit.db'))
    monkeypatch.setenv('TG_RESET_DB', '0')
    monkeypatch.setenv('TG_SECURITY_AUDIT_TOKEN', TOKEN)
    from main import app
    with TestClient(app) as c:
        yield c


def snapshot():
    import db
    with db.tx() as conn:
        return tuple(conn.execute('SELECT count(*) FROM ' + t).fetchone()[0]
                     for t in ('jobs', 'tasks'))


def audit(client, **query):
    response = client.get('/api/security/events', headers=AUTH, params=query)
    assert response.status_code == 200
    assert 'no-store' in response.headers['cache-control']
    return response.json()


@pytest.mark.parametrize('headers', [{}, {'Authorization': 'Bearer wrong'},
                                   {'Authorization': 'Basic ' + TOKEN}])
def test_read_requires_dedicated_credential(client, headers):
    response = client.get('/api/security/events', headers=headers)
    assert response.status_code == 401
    assert 'events' not in response.json()
    assert response.headers['www-authenticate'] == 'Bearer'
    assert 'no-store' in response.headers['cache-control']


@pytest.mark.parametrize('configured', [None, '', 'short', ' ' * 40, 'é' * 40, 'x' * 257])
def test_missing_or_invalid_configuration_never_opens_read_access(client, monkeypatch, configured):
    if configured is None:
        monkeypatch.delenv('TG_SECURITY_AUDIT_TOKEN')
    else:
        monkeypatch.setenv('TG_SECURITY_AUDIT_TOKEN', configured)
    response = client.get('/api/security/events', headers=AUTH)
    assert response.status_code == 503
    assert response.json()['detail'] == 'security_events_unavailable'
    # Read configuration cannot disable existing job submission.
    assert client.post('/api/jobs', json=JOB).status_code == 200


def test_allowed_event_is_redacted_correlated_and_does_not_claim_an_identity(client):
    response = client.post('/api/jobs', json=JOB, headers={
        'X-Request-ID': 'forged', 'X-Actor-ID': 'admin', 'Authorization': 'Bearer private-secret'})
    assert response.status_code == 200 and snapshot() == (1, 4)
    data = audit(client)
    assert data['totals'] == {'allowed': 1, 'blocked': 0}
    assert data['scope'] == 'since_audit_enabled'
    assert len(data['events']) == 1
    event = data['events'][0]
    assert set(event) == {'event_id', 'timestamp', 'request_id', 'actor_id', 'action',
                          'decision', 'reason_code', 'details', 'job_id'}
    assert event['request_id'] == response.headers['x-request-id']
    assert re.fullmatch(r'req_[a-f0-9]{24}', event['request_id'])
    assert event['actor_id'] == 'anonymous' and event['action'] == 'submit_job'
    assert event['decision'] == 'allowed' and event['reason_code'] == 'SUBMISSION_ACCEPTED'
    assert event['job_id'] == response.json()['job_id']
    assert event['timestamp'].endswith('Z')
    assert all(secret not in json.dumps(data) for secret in ('forged', 'admin', 'private-secret'))


@pytest.mark.parametrize('payload,reason', [
    ({'task_type': 'arbitrary_python', 'params': PARAMS}, 'UNAPPROVED_WORKLOAD'),
    (JOB | {'script': 'private-secret'}, 'INVALID_TASK_PARAMS'),
    ({'task_type': 'mandelbrot_v1', 'params': PARAMS | {'command': 'private-secret'}}, 'INVALID_TASK_PARAMS'),
    ({'task_type': 'mandelbrot_v1', 'params': PARAMS | {'width': 100000}}, 'INVALID_TASK_PARAMS'),
    ({}, 'INVALID_TASK_PARAMS'),
    (['private-secret'], 'INVALID_TASK_PARAMS'),
])
def test_schema_denials_are_audited_once_without_creating_work(client, payload, reason):
    before = snapshot()
    response = client.post('/api/jobs', json=payload)
    assert response.status_code == 422 and snapshot() == before
    data = audit(client)
    assert data['totals'] == {'allowed': 0, 'blocked': 1}
    assert len(data['events']) == 1
    event = data['events'][0]
    assert event['request_id'] == response.headers['x-request-id']
    assert event['decision'] == 'denied' and event['reason_code'] == reason
    assert event['job_id'] is None and 'private-secret' not in json.dumps(data)


@pytest.mark.parametrize('raw', ['{"private-secret":', '', '{"task_type":"mandelbrot_v1","params":{"width":NaN}}'])
def test_malformed_json_and_nonfinite_values_are_audited(client, raw):
    response = client.post('/api/jobs', content=raw, headers={'Content-Type': 'application/json'})
    assert response.status_code == 422 and snapshot() == (0, 0)
    data = audit(client)
    assert data['totals']['blocked'] == 1
    assert 'private-secret' not in json.dumps(data)


def test_active_limit_records_denial_and_counts_include_rows_outside_page(client):
    assert client.post('/api/jobs', json=JOB).status_code == 200
    assert client.post('/api/jobs', json=JOB).status_code == 200
    response = client.post('/api/jobs', json=JOB)
    assert response.status_code == 429 and snapshot() == (2, 8)
    data = audit(client, limit=1)
    assert data['totals'] == {'allowed': 2, 'blocked': 1}
    assert len(data['events']) == 1
    assert data['events'][0]['reason_code'] == 'ACTIVE_JOB_LIMIT'
    assert data['events'][0]['request_id'] == response.headers['x-request-id']


def test_events_persist_across_repeated_initialization_and_ids_are_unique(client):
    import db
    for _ in range(3):
        client.post('/api/jobs', json={})
    before = audit(client)
    db.close_db(); db.init_db(); db.init_db()
    after = audit(client)
    assert after['events'] == before['events']
    assert after['totals'] == {'allowed': 0, 'blocked': 3}
    assert len({e['request_id'] for e in after['events']}) == 3
    assert len({e['event_id'] for e in after['events']}) == 3


@pytest.mark.parametrize('limit', ['0', '101', '-1', 'bad'])
def test_audit_page_limit_is_bounded(client, limit):
    assert client.get('/api/security/events', headers=AUTH, params={'limit': limit}).status_code == 422


def test_audit_failure_rolls_back_job_and_all_its_tasks(client):
    import db
    with db.tx() as conn:
        conn.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON security_events "
                     "BEGIN SELECT RAISE(ABORT, 'private-storage-detail'); END")
    response = client.post('/api/jobs', json=JOB)
    assert response.status_code == 503
    assert snapshot() == (0, 0)
    assert 'private-storage-detail' not in response.text
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) FROM events').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM security_events').fetchone()[0] == 0


def test_audit_failure_on_rejected_input_returns_unavailable_without_creating_work(client):
    import db
    with db.tx() as conn:
        conn.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON security_events "
                     "BEGIN SELECT RAISE(ABORT, 'private-storage-detail'); END")
    response = client.post('/api/jobs', json={})
    assert response.status_code == 503 and snapshot() == (0, 0)
    assert 'private-storage-detail' not in response.text


def test_audit_reader_worker_tokens_are_not_interchangeable(client):
    w = client.post('/api/workers/register', json={
        'name': 'Audit test worker', 'worker_type': 'browser', 'task_types': ['mandelbrot_v1']}).json()
    assert client.get('/api/security/events', headers={'Authorization': 'Bearer ' + w['token']}).status_code == 401
    assert client.post('/api/workers/' + w['worker_id'] + '/lease', json={}, headers=AUTH).status_code == 401
    assert audit(client)['totals'] == {'allowed': 0, 'blocked': 0}


def test_accepted_job_still_completes_and_credits_contribution(client):
    job = client.post('/api/jobs', json=JOB).json()
    w = client.post('/api/workers/register', json={
        'name': 'Native', 'worker_type': 'native', 'task_types': ['mandelbrot_v1'],
        'device_id': 'dev_audit123', 'owner': 'audit-demo'}).json()
    headers = {'Authorization': 'Bearer ' + w['token']}
    for _ in range(4):
        task = client.post('/api/workers/' + w['worker_id'] + '/lease', json={}, headers=headers).json()
        r = client.post('/api/tasks/' + str(task['task_id']) + '/complete', headers=headers,
                        json={'worker_id': w['worker_id'], 'lease_token': task['lease_token'],
                              'result': [1] * 1024, 'runtime_ms': 1})
        assert r.status_code == 200
    assert client.get('/api/jobs/' + job['job_id']).json()['status'] == 'completed'
    assert client.get('/api/contributions').json()['totals']['verified_miter'] == 0.004096
    assert audit(client)['totals'] == {'allowed': 1, 'blocked': 0}


def test_upgrade_preserves_existing_jobs_and_does_not_invent_historical_audits(client):
    import db
    client.post('/api/jobs', json=JOB)
    with db.tx() as conn:
        conn.execute('DROP TABLE security_events')
    db.close_db(); db.init_db()
    assert snapshot() == (1, 4)
    assert audit(client)['totals'] == {'allowed': 0, 'blocked': 0}
