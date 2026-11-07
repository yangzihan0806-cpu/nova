"""Redacted submission evidence; this does not authenticate job submitters."""
import logging
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

import db

logger = logging.getLogger(__name__)
NO_STORE = {'Cache-Control': 'private, no-store', 'Vary': 'Authorization'}
DETAILS = {
    'SUBMISSION_ACCEPTED': 'Existing submission checks passed; job created.',
    'UNAPPROVED_WORKLOAD': 'Only the approved Mandelbrot task template is accepted.',
    'INVALID_TASK_PARAMS': 'Request fields or task parameters are invalid.',
    'ACTIVE_JOB_LIMIT': 'The server active-job limit has been reached.',
    'SUBMISSION_REJECTED': 'The submission was rejected by the server.',
}


def record(conn, request_id, decision, reason_code, now, job_id=None):
    """Use the caller's transaction so an allowed event and its job commit together."""
    conn.execute('''INSERT INTO security_events(ts,request_id,decision,reason_code,details,job_id)
                    VALUES (?,?,?,?,?,?)''',
                 (now, request_id, decision, reason_code, DETAILS[reason_code], job_id))


def record_denied(request_id, reason_code):
    with db.tx() as conn:
        record(conn, request_id, 'denied', reason_code, time.time())


def unavailable(request_id=None):
    headers = NO_STORE | ({'X-Request-ID': request_id} if request_id else {})
    return HTTPException(503, 'security_events_unavailable', headers=headers)


class AuditedJobRoute(APIRoute):
    """Wrap only POST /api/jobs, including FastAPI's pre-handler validation."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def audited(request: Request):
            request_id = 'req_' + secrets.token_hex(12)
            request.state.audit_request_id = request_id
            try:
                try:
                    response = await original(request)
                except RequestValidationError as exc:
                    unapproved = any(error['loc'] == ('body', 'task_type')
                                     and error['type'] == 'literal_error' for error in exc.errors())
                    reason = 'UNAPPROVED_WORKLOAD' if unapproved else 'INVALID_TASK_PARAMS'
                    await run_in_threadpool(record_denied, request_id, reason)
                    raise
                except StarletteHTTPException as exc:
                    if 400 <= exc.status_code < 500:
                        reason = {400: 'INVALID_TASK_PARAMS', 422: 'INVALID_TASK_PARAMS',
                                  429: 'ACTIVE_JOB_LIMIT'}.get(exc.status_code, 'SUBMISSION_REJECTED')
                        await run_in_threadpool(record_denied, request_id, reason)
                    exc.headers = (exc.headers or {}) | {'X-Request-ID': request_id}
                    raise
            except sqlite3.Error:
                # Never include the DB exception, request body or credentials in logs.
                logger.error('Submission audit storage unavailable; request_id=%s', request_id)
                raise unavailable(request_id) from None
            response.headers['X-Request-ID'] = request_id
            return response

        return audited


def require_reader(authorization: str | None = Header(default=None)):
    expected = os.getenv('TG_SECURITY_AUDIT_TOKEN', '')
    if re.fullmatch(r'[A-Za-z0-9_-]{32,256}', expected) is None:
        raise unavailable()
    scheme, _, supplied = (authorization or '').partition(' ')
    if (scheme.lower() != 'bearer' or not supplied.isascii()
            or not secrets.compare_digest(supplied, expected)):
        raise HTTPException(401, 'audit_auth_required', headers=NO_STORE | {'WWW-Authenticate': 'Bearer'})


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def events(limit):
    with db.tx() as conn:
        counts = dict(conn.execute('SELECT decision,count(*) FROM security_events GROUP BY decision').fetchall())
        rows = conn.execute('SELECT * FROM security_events ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
    return {
        'generated_at': iso(time.time()), 'scope': 'since_audit_enabled',
        'totals': {'allowed': counts.get('allowed', 0), 'blocked': counts.get('denied', 0)},
        'events': [dict(event_id='evt_' + str(r['id']), timestamp=iso(r['ts']),
                        request_id=r['request_id'], actor_id='anonymous', action='submit_job',
                        decision=r['decision'], reason_code=r['reason_code'], details=r['details'],
                        job_id=r['job_id']) for r in rows],
    }


router = APIRouter()


@router.get('/api/security/events', dependencies=[Depends(require_reader)])
def read_events(response: Response, limit: int = Query(default=50, ge=1, le=100)):
    response.headers.update(NO_STORE)
    try:
        return events(limit)
    except sqlite3.Error:
        raise unavailable() from None
