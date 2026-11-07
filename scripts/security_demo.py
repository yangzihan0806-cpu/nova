#!/usr/bin/env python3
"""Verify submission auditing on a fresh, disposable loopback server.

Set TG_SECURITY_AUDIT_TOKEN in both processes; the token is never printed.
Uses real HTTP, the existing Python kernel and a temporary worker registration.
"""
import argparse
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

import requests

PARAMS = dict(width=64, height=64, tile_size=32, max_iter=1,
              x_min=-2.2, x_max=1, y_min=-1.2, y_max=1.2)
MARKER = 'private-demo-value-do-not-log'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', required=True, help='fresh disposable loopback server URL')
    args = parser.parse_args()
    parts = urlsplit(args.server)
    if (parts.scheme not in ('http', 'https') or parts.hostname not in ('127.0.0.1', 'localhost', '::1')
            or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('', '/')):
        parser.error('--server must be a loopback origin for a disposable test server')
    token = os.getenv('TG_SECURITY_AUDIT_TOKEN', '')
    if not token:
        parser.error('TG_SECURITY_AUDIT_TOKEN must be configured in this test process')
    base = args.server.rstrip('/')
    reader = {'Authorization': 'Bearer ' + token}
    session = requests.Session()
    worker = None

    def request(method, path, expected=200, headers=None, body=None, raw=None):
        response = session.request(method, base + path, headers=headers, json=body, data=raw,
                                   timeout=10, allow_redirects=False)
        require(response.status_code == expected, f'{method} {path}: expected HTTP {expected}, got {response.status_code}')
        return response

    try:
        request('GET', '/healthz')
        require(request('GET', '/api/jobs').json()['jobs'] == [], 'Use a fresh disposable database: jobs already exist')
        initial = request('GET', '/api/security/events', headers=reader).json()
        require(initial['totals'] == {'allowed': 0, 'blocked': 0}, 'Use a fresh disposable database: audits already exist')
        require(not request('GET', '/api/stats').json()['workers'], 'Use an idle disposable server without other workers')
        request('GET', '/api/security/events', 401)
        request('GET', '/api/security/events', 401, headers={'Authorization': 'Bearer wrong'})
        print('PASS 1/7 reader access control and disposable-server precondition')

        denied_ids = []
        invalid = [
            {'task_type': 'arbitrary_python', 'params': {'command': MARKER}},
            {'task_type': 'mandelbrot_v1', 'params': PARAMS | {'script': MARKER}},
            {'task_type': 'mandelbrot_v1', 'params': PARAMS | {'width': 100000}},
        ]
        for body in invalid:
            response = request('POST', '/api/jobs', 422, body=body)
            denied_ids.append(response.headers['X-Request-ID'])
        response = request('POST', '/api/jobs', 422, headers={'Content-Type': 'application/json'}, raw='{"incomplete":')
        denied_ids.append(response.headers['X-Request-ID'])
        require(len(set(denied_ids)) == 4, 'Each rejected request must have a unique server request ID')
        print('PASS 2/7 invalid template, script, oversized params and malformed JSON rejected')

        require(request('GET', '/api/jobs').json()['jobs'] == [], 'Rejected submissions created a job')
        worker = request('POST', '/api/workers/register', body={
            'name': 'Audit demo worker', 'worker_type': 'native', 'task_types': ['mandelbrot_v1'],
            'device_id': 'dev_auditdemo', 'owner': 'audit-demo'}).json()
        wh = {'Authorization': 'Bearer ' + worker['token']}
        lease = '/api/workers/' + worker['worker_id'] + '/lease'
        request('POST', lease, 204, headers=wh, body={})
        print('PASS 3/7 rejected submissions leave no job or executable lease')

        response = request('POST', '/api/jobs', body={'task_type': 'mandelbrot_v1', 'params': PARAMS})
        allowed_id = response.headers['X-Request-ID']
        job = response.json()
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
        from mandelbrot import compute_tile
        for _ in range(4):
            task = request('POST', lease, headers=wh, body={}).json()
            request('POST', '/api/tasks/' + str(task['task_id']) + '/complete', headers=wh, body={
                'worker_id': worker['worker_id'], 'lease_token': task['lease_token'],
                'result': compute_tile(task['input']), 'runtime_ms': 0})
        detail = request('GET', '/api/jobs/' + job['job_id']).json()
        require(detail['status'] == 'completed' and detail['counts']['done'] == 4, 'Accepted job did not complete')
        print('PASS 4/7 accepted job completes through existing worker lease/result APIs')

        data = request('GET', '/api/security/events', headers=reader).json()
        require(data['totals'] == {'allowed': 1, 'blocked': 4}, 'Audit counters do not match actual requests')
        by_id = {e['request_id']: e for e in data['events']}
        require(len(by_id) == 5 and by_id[allowed_id]['job_id'] == job['job_id'], 'Accepted audit event is missing or duplicated')
        require(all(by_id[r]['decision'] == 'denied' and by_id[r]['job_id'] is None for r in denied_ids), 'Denied audit correlation failed')
        print('PASS 5/7 persisted counters and request/job correlations match real requests')

        import json
        text = json.dumps(data)
        require(all(value not in text for value in (token, worker['token'], MARKER)), 'Audit exposed credentials or raw input')
        require(all(e['actor_id'] == 'anonymous' for e in data['events']), 'Unverified submitters were presented as authenticated')
        print('PASS 6/7 audit details redact raw input and credentials; actors remain anonymous')

        credit = request('GET', '/api/contributions').json()['totals']
        require(credit['tiles_done'] == 4 and credit['verified_miter'] == 0.004096, 'Contribution accounting regressed')
        print('PASS 7/7 existing contribution accounting remains correct')
        print('Summary: 7/7 PASS. Audit-only scope; user authorization and per-user quotas are not implemented.')
        return 0
    except (RuntimeError, requests.RequestException, ValueError, KeyError, TypeError) as exc:
        # Network exceptions can contain URLs/headers; report only our safe check messages.
        print('FAIL: ' + (str(exc) if type(exc) is RuntimeError else type(exc).__name__), file=sys.stderr)
        return 1
    finally:
        if worker:
            try:
                session.post(base + '/api/workers/' + worker['worker_id'] + '/disconnect', json={},
                             headers={'Authorization': 'Bearer ' + worker['token']}, timeout=5, allow_redirects=False)
            except requests.RequestException:
                pass
        session.close()


if __name__ == '__main__':
    raise SystemExit(main())
