"""Run from backend/: uvicorn main:app --host 0.0.0.0 --port 8000.

Use one application process: the MVP lock/connection are process-local.
"""
import csv
import io
import json
import math
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

import db
import scheduler
import security_audit
from schemas import CompleteReq, ContributionWindow, EmptyReq, FailReq, JobCreate, PlannerReq, RegisterReq

BASE_DIR = Path(__file__).resolve().parent
QUICK = {'id': 'quick', 'label': 'Quick test — full set (small)',
         'params': dict(width=512, height=384, tile_size=64, max_iter=256,
                        x_min=-2.2, x_max=1.0, y_min=-1.2, y_max=1.2)}


@asynccontextmanager
async def lifespan(app):
    db.init_db()
    try:
        yield
    finally:
        db.close_db()


app = FastAPI(title='TartanGrid', version='1.0', lifespan=lifespan)


def _finite_json(value):
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {k: _finite_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_finite_json(v) for v in value]
    return value


@app.exception_handler(RequestValidationError)
def validation_error(request, exc):
    # Keep FastAPI's detail shape; non-finite rejected inputs cannot be emitted
    # as JSON numbers (otherwise the intended 422 becomes a serialization 500).
    request_id = getattr(request.state, 'audit_request_id', None)
    headers = {'X-Request-ID': request_id} if request_id else {}
    return JSONResponse(status_code=422, content={'detail': _finite_json(jsonable_encoder(exc.errors()))},
                        headers=headers)


def require_worker(worker_id, authorization):
    scheme, _, token = (authorization or '').partition(' ')
    if scheme.lower() != 'bearer' or not token:
        raise HTTPException(401, 'unauthorized')
    scheduler.authenticate(worker_id, token)


def worker_auth(id: str, authorization: str | None = Header(default=None)):
    require_worker(id, authorization)


@app.post('/api/workers/register')
def register(body: RegisterReq):
    return scheduler.register_worker(**body.model_dump(), now=time.time())


@app.post('/api/workers/{id}/heartbeat', dependencies=[Depends(worker_auth)])
def heartbeat(id: str, body: EmptyReq):
    return scheduler.heartbeat(id, time.time())


@app.post('/api/workers/{id}/lease', dependencies=[Depends(worker_auth)])
def lease(id: str, body: EmptyReq):
    result = scheduler.lease(id, time.time())
    return Response(status_code=204) if result is None else result


@app.post('/api/workers/{id}/pause', dependencies=[Depends(worker_auth)])
def pause(id: str, body: EmptyReq):
    return scheduler.pause(id, time.time())


@app.post('/api/workers/{id}/resume', dependencies=[Depends(worker_auth)])
def resume(id: str, body: EmptyReq):
    return scheduler.resume(id, time.time())


@app.post('/api/workers/{id}/disconnect', dependencies=[Depends(worker_auth)])
def disconnect(id: str, body: EmptyReq):
    return scheduler.disconnect(id, time.time())


@app.post('/api/tasks/{task_id}/complete')
def complete(task_id: int, body: CompleteReq, authorization: str | None = Header(default=None)):
    require_worker(body.worker_id, authorization)
    return scheduler.complete(task_id, **body.model_dump(), now=time.time())


@app.post('/api/tasks/{task_id}/fail')
def fail(task_id: int, body: FailReq, authorization: str | None = Header(default=None)):
    require_worker(body.worker_id, authorization)
    return scheduler.fail(task_id, **body.model_dump(), now=time.time())


job_submissions = APIRouter(route_class=security_audit.AuditedJobRoute)


@job_submissions.post('/api/jobs')
def create_job(body: JobCreate, request: Request):
    return scheduler.create_job(body.params, time.time(), request.state.audit_request_id)


app.include_router(job_submissions)
app.include_router(security_audit.router)


@app.get('/api/jobs')
def list_jobs():
    return scheduler.list_jobs()


@app.get('/api/jobs/{job_id}')
def job_view(job_id: str, since: int = Query(default=0, ge=0)):
    return scheduler.job_view(job_id, since, time.time())


@app.get('/api/stats')
def stats():
    return scheduler.stats(time.time())


@app.get('/api/events')
def events(since: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=500), job_id: str | None = None):
    return scheduler.events(since, limit, job_id)


def contribution_window(since: str | None = None, until: str | None = None):
    try:
        return ContributionWindow(since=since, until=until)
    except ValidationError as exc:
        errors = exc.errors()
        for error in errors:
            error['loc'] = ('query', *error['loc'])
        raise RequestValidationError(errors) from exc


def contribution_data(window):
    return scheduler.contributions(
        window.since.timestamp() if window.since is not None else None,
        window.until.timestamp() if window.until is not None else None,
        time.time(),
    )


@app.get('/api/contributions')
def contributions(window: ContributionWindow = Depends(contribution_window)):
    return contribution_data(window)


def csv_label(value):
    # CSV quoting alone does not prevent spreadsheets from executing formulas.
    if value and (value.lstrip().startswith(('=', '+', '-', '@'))
                  or value.startswith(('\t', '\r', '\n'))):
        return "'" + value
    return value


@app.get('/api/contributions.csv')
def contributions_csv(window: ContributionWindow = Depends(contribution_window)):
    data = contribution_data(window)
    fields = ('device_id', 'display_name', 'owner', 'worker_types', 'workers', 'tiles_done',
              'verified_miter', 'share', 'compute_ms', 'online_s', 'first_seen', 'last_seen')
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for device in data['devices']:
        writer.writerow(device | {'worker_types': ';'.join(device['worker_types']),
                                  'display_name': csv_label(device['display_name']),
                                  'owner': csv_label(device['owner'])})
    return Response(content=output.getvalue(), media_type='text/csv',
                    headers={'Content-Disposition': 'attachment; filename="tartangrid_contributions.csv"'})


@app.get('/api/presets')
def presets():
    try:
        return json.loads((BASE_DIR / 'presets.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        return [QUICK]


try:
    from planner import router as planner_router
except ImportError:
    # CONTRACT-NOTE: §6.13 allows omitting an unavailable router, while Task A
    # requires every endpoint. Provide only the contracted 503 fallback until
    # D's module is importable; never create planner.py or fake an AI result.
    @app.post('/api/planner')
    def planner_unavailable(body: PlannerReq):
        raise HTTPException(503, 'planner_unavailable')
else:
    app.include_router(planner_router)


@app.get('/healthz')
def healthz():
    return {'ok': True}


STATIC_DIR = BASE_DIR / 'static'
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount('/', StaticFiles(directory=STATIC_DIR, html=True), name='static')
