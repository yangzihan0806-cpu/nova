"""Task A contract tests. Each case uses an isolated SQLite database."""
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

PARAMS = dict(width=64, height=64, tile_size=32, max_iter=1,
              x_min=-2.2, x_max=1.0, y_min=-1.2, y_max=1.2)


def test_schema_valid_bounds():
    from schemas import JobParams, JobCreate
    p = JobParams(**PARAMS)
    assert p.model_dump() == PARAMS
    assert JobCreate(task_type='mandelbrot_v1', params=PARAMS).params.width == 64
    assert JobParams(**(PARAMS | dict(width=4096, height=4096, tile_size=64, max_iter=20000)))


@pytest.mark.parametrize('change', [
    {'width': -1}, {'width': 63}, {'width': 4097}, {'height': 65},
    {'tile_size': 16}, {'tile_size': True}, {'width': 64.0},
    {'max_iter': 999999}, {'max_iter': 0}, {'max_iter': True},
    {'x_min': float('nan')}, {'x_max': float('inf')}, {'y_max': 4.1},
    {'x_min': 1.0}, {'y_min': 1.2}, {'unexpected': 1},
    {'width': 4096, 'height': 4096, 'tile_size': 32},
])
def test_schema_rejects_invalid_job_params(change):
    from pydantic import ValidationError
    from schemas import JobParams
    with pytest.raises(ValidationError):
        JobParams(**(PARAMS | change))


@pytest.fixture
def database(tmp_path, monkeypatch):
    import db
    monkeypatch.setenv('TG_DB_PATH', str(tmp_path / 'test.db'))
    monkeypatch.setenv('TG_RESET_DB', '0')
    db.init_db()
    yield db
    db.close_db()


def test_db_transaction_rollback_persistence_and_reset(database, monkeypatch):
    db = database
    assert db.get_conn().isolation_level is None
    with pytest.raises(RuntimeError):
        with db.tx() as c:
            c.execute("INSERT INTO events(ts,type,message) VALUES (0,'example','rollback')")
            raise RuntimeError('rollback')
    with db.tx() as c:
        assert c.execute('SELECT count(*) FROM events').fetchone()[0] == 0
        c.execute("INSERT INTO events(ts,type,message) VALUES (0,'example','persist')")
    db.close_db()
    db.init_db()
    with db.tx() as c:
        assert c.execute('SELECT message FROM events').fetchone()[0] == 'persist'
    monkeypatch.setenv('TG_RESET_DB', '1')
    db.init_db()
    with db.tx() as c:
        assert c.execute('SELECT count(*) FROM events').fetchone()[0] == 0


@pytest.fixture
def scheduler(database):
    return importlib.import_module('scheduler')


def worker(s, now=100.0, **kwargs):
    return s.register_worker(**(dict(name='Test worker', worker_type='native', slots=1,
                                    has_gpu=False, task_types=['mandelbrot_v1'],
                                    client_info=None, now=now) | kwargs))


def job(s, now=100.0, **kwargs):
    from schemas import JobParams
    return s.create_job(JobParams(**(PARAMS | kwargs)), now)


def submit(s, w, task, now=101.0, result=None):
    return s.complete(task['task_id'], w['worker_id'], task['lease_token'],
                      [1] * 1024 if result is None else result, 5, now)


def assert_http(code, function, *args, **kwargs):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        function(*args, **kwargs)
    assert e.value.status_code == code
    return e.value


def task_row(db, task_id):
    with db.tx() as c:
        return dict(c.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone())


def test_job_split_fifo_and_allowlist(scheduler, database):
    s = scheduler
    first = job(s)
    job(s, now=101)
    w = worker(s, slots=8)
    tasks = [s.lease(w['worker_id'], 102) for _ in range(5)]
    assert [t['job_id'] for t in tasks[:4]] == [first['job_id']] * 4
    assert [(t['input']['tile_x'], t['input']['tile_y']) for t in tasks[:4]] == [(0,0),(32,0),(0,32),(32,32)]
    assert tasks[0]['input'] == dict(tile_x=0,tile_y=0,tile_w=32,tile_h=32,
        image_width=64,image_height=64,max_iter=1,x_min=-2.2,x_max=1.0,y_min=-1.2,y_max=1.2)
    incompatible = worker(s, task_types=['arbitrary_code', 'matrix_batch_v1'])
    assert s.lease(incompatible['worker_id'], 102) is None
    with database.tx() as c:
        row = c.execute('SELECT * FROM jobs WHERE id=?',(first['job_id'],)).fetchone()
        assert row['status']=='running' and row['started_at']==102


def test_eight_threads_never_duplicate_a_lease(scheduler):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    s=scheduler
    job(s, width=256, height=256)
    workers=[worker(s, slots=8) for _ in range(8)]
    barrier=Barrier(8)
    def take(w):
        barrier.wait()
        return [s.lease(w['worker_id'], 101)['task_id'] for _ in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        leases=sum(pool.map(take, workers), [])
    assert len(leases)==len(set(leases))==64
    assert s.lease(workers[0]['worker_id'],101) is None


def test_concurrent_calls_enforce_single_worker_slots(scheduler):
    from concurrent.futures import ThreadPoolExecutor
    s=scheduler
    job(s)
    w=worker(s)
    with ThreadPoolExecutor(max_workers=8) as pool:
        leases=list(pool.map(lambda _:s.lease(w['worker_id'],101), range(8)))
    assert sum(t is not None for t in leases)==1


def test_expiry_reassignment_old_token_and_duplicate(scheduler, database):
    s=scheduler
    job(s)
    a,b=worker(s),worker(s)
    old=s.lease(a['worker_id'],100)
    s.reap(110)  # strict less-than: the boundary is still valid
    assert task_row(database,old['task_id'])['status']=='leased'
    s.reap(110.1)
    assert task_row(database,old['task_id'])['status']=='pending'
    new=s.lease(b['worker_id'],111)
    assert new['task_id']==old['task_id'] and new['lease_token']!=old['lease_token']
    assert assert_http(409,submit,s,a,old,now=112).detail=='stale_lease'
    assert_http(409,submit,s,a,new,now=112)  # another worker cannot use a stolen lease
    assert submit(s,b,new,now=112)=={'accepted':True}
    assert_http(409,submit,s,b,new,now=112)
    with database.tx() as c:
        assert c.execute('SELECT sum(tasks_done) FROM workers').fetchone()[0]==1


def test_unreaped_late_result_is_accepted(scheduler):
    s=scheduler
    job(s)
    w=worker(s)
    t=s.lease(w['worker_id'],100)
    assert submit(s,w,t,now=200)=={'accepted':True}


def test_pause_allows_completion_resume_allows_lease(scheduler):
    s=scheduler
    job(s)
    w=worker(s)
    t=s.lease(w['worker_id'],100)
    s.pause(w['worker_id'],101)
    assert s.lease(w['worker_id'],102) is None
    submit(s,w,t,now=102)
    s.resume(w['worker_id'],103)
    assert s.lease(w['worker_id'],103) is not None


def test_disconnect_releases_tasks_without_resetting_attempts(scheduler,database):
    s=scheduler
    job(s)
    w=worker(s,slots=2)
    tasks=[s.lease(w['worker_id'],100) for _ in range(2)]
    s.disconnect(w['worker_id'],101)
    for t in tasks:
        row=task_row(database,t['task_id'])
        assert (row['status'],row['worker_id'],row['lease_token'],row['attempts'])==('pending',None,None,1)
        assert_http(409,submit,s,w,t)
    s.resume(w['worker_id'],102)
    assert s.lease(w['worker_id'],102) is None
    with database.tx() as c:
        assert c.execute("SELECT count(*) FROM events WHERE type='lease_released'").fetchone()[0]==2


def test_three_expirations_fail_job_and_no_further_leases(scheduler,database):
    s=scheduler
    j=job(s)
    w=worker(s)
    for now in (100,111,122):
        t=s.lease(w['worker_id'],now)
        s.reap(now+10.1)
    row=task_row(database,t['task_id'])
    assert row['status']=='failed' and row['attempts']==3
    with database.tx() as c:
        assert c.execute('SELECT status FROM jobs WHERE id=?',(j['job_id'],)).fetchone()[0]=='failed'
        assert c.execute("SELECT count(*) FROM events WHERE type='job_failed'").fetchone()[0]==1
    assert s.lease(w['worker_id'],134) is None


def test_disconnect_after_last_attempt_never_grants_fourth(scheduler,database):
    s=scheduler
    job(s)
    w=worker(s)
    for now in (100,101):
        t=s.lease(w['worker_id'],now)
        s.fail(t['task_id'],w['worker_id'],t['lease_token'],'retry',now)
    t=s.lease(w['worker_id'],102)
    s.disconnect(w['worker_id'],103)
    assert task_row(database,t['task_id'])['status']=='pending'
    other=worker(s,now=104)
    assert s.lease(other['worker_id'],104) is None
    assert task_row(database,t['task_id'])['attempts']==3


@pytest.mark.parametrize('result', [[], [1]*1023, [1]*1025, [True]*1024,
                                  [1.0]*1024, [-1]*1024, [2]*1024, [0]*1024, None, 'invalid'])
def test_invalid_result_requeues_and_commits_rejection(scheduler,database,result):
    s=scheduler
    job(s)
    w=worker(s)
    t=s.lease(w['worker_id'],100)
    assert_http(422,s.complete,t['task_id'],w['worker_id'],t['lease_token'],result,5,101)
    row=task_row(database,t['task_id'])
    assert row['status']=='pending' and row['worker_id'] is None and row['lease_token'] is None
    with database.tx() as c:
        assert c.execute("SELECT count(*) FROM events WHERE type='task_rejected'").fetchone()[0]==1
    assert s.lease(w['worker_id'],102)['task_id']==t['task_id']


def test_failure_budget_and_terminal_job_stays_failed(scheduler,database):
    s=scheduler
    j=job(s)
    a,b=worker(s),worker(s)
    other=s.lease(b['worker_id'],100)
    for now in (100,101,102):
        t=s.lease(a['worker_id'],now)
        s.fail(t['task_id'],a['worker_id'],t['lease_token'],'computation failed',now)
    submit(s,b,other,now=103)
    with database.tx() as c:
        row=c.execute('SELECT * FROM jobs WHERE id=?',(j['job_id'],)).fetchone()
        assert row['status']=='failed' and row['finished_at']==102


def test_job_quota_is_atomic(scheduler,database):
    from concurrent.futures import ThreadPoolExecutor
    from fastapi import HTTPException
    s=scheduler
    def create(_):
        try:
            job(s)
            return 200
        except HTTPException as e:
            return e.status_code
    with ThreadPoolExecutor(max_workers=8) as pool:
        codes=list(pool.map(create,range(8)))
    assert sorted(codes)==[200,200,429,429,429,429,429,429]
    with database.tx() as c:
        assert c.execute('SELECT count(*) FROM tasks').fetchone()[0]==8


def test_completed_job_events_and_counters(scheduler,database):
    s=scheduler
    j=job(s)
    w=worker(s)
    for i in range(4):
        t=s.lease(w['worker_id'],100+i)
        submit(s,w,t,now=100+i)
    assert s.lease(w['worker_id'],104) is None
    with database.tx() as c:
        row=c.execute('SELECT * FROM jobs WHERE id=?',(j['job_id'],)).fetchone()
        assert row['status']=='completed' and row['finished_at']==103
        assert c.execute('SELECT tasks_done FROM workers').fetchone()[0]==4
        assert c.execute("SELECT count(*) FROM events WHERE type='job_completed'").fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM tasks t JOIN events e ON e.id=t.done_seq WHERE e.type=\'task_done\'').fetchone()[0]==4


@pytest.fixture
def client(database):
    from fastapi.testclient import TestClient
    from main import app
    with TestClient(app) as c:
        yield c


def api_worker(client, **changes):
    r=client.post('/api/workers/register',json=dict(name='HTTP worker',worker_type='browser',slots=1,
                     has_gpu=False,task_types=['mandelbrot_v1']) | changes)
    assert r.status_code==200
    w=r.json()
    return w, {'Authorization':'Bearer '+w['token']}


def test_http_route_shapes_auth_and_completion(client):
    import re
    assert client.get('/healthz').json()=={'ok':True}
    assert client.get('/docs').status_code==200
    assert client.get('/api/planner').status_code in (404,405)
    assert client.post('/api/planner',json={'prompt':'render'}).json()=={'detail':'planner_unavailable'}
    assert client.post('/api/planner',json={'prompt':'render'}).status_code==503
    presets=client.get('/api/presets').json()
    assert presets[0]['id']=='quick' and presets[0]['params']['width']==512
    w,h=api_worker(client)
    assert set(w)=={'worker_id','token','heartbeat_interval_s','lease_ttl_s'}
    assert re.fullmatch('wkr_[0-9a-f]{8}',w['worker_id'])
    for action in ['heartbeat','lease','pause','resume','disconnect']:
        url=f"/api/workers/{w['worker_id']}/{action}"
        assert client.post(url,json={}).status_code==401
        assert client.post(url,json={},headers={'Authorization':'Bearer bad'}).status_code==401
    lease_url=f"/api/workers/{w['worker_id']}/lease"
    empty=client.post(lease_url,json={},headers=h)
    assert empty.status_code==204 and empty.content==b''
    j=client.post('/api/jobs',json={'task_type':'mandelbrot_v1','params':PARAMS}).json()
    assert set(j)=={'job_id','status','task_count'} and j['task_count']==4
    assert re.fullmatch('job_[0-9a-f]{6}',j['job_id'])
    t=client.post(lease_url,json={},headers=h).json()
    assert set(t)=={'task_id','job_id','task_type','lease_token','lease_expires_at','input'}
    assert re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z',t['lease_expires_at'])
    payload=dict(worker_id=w['worker_id'],lease_token=t['lease_token'],result=[1]*1024,runtime_ms=5)
    url=f"/api/tasks/{t['task_id']}/complete"
    assert client.post(url,json=payload).status_code==401
    assert client.post(url,json=payload,headers=h).json()=={'accepted':True}
    duplicate=client.post(url,json=payload,headers=h)
    assert duplicate.status_code==409 and duplicate.json()=={'detail':'stale_lease'}
    hb=client.post(f"/api/workers/{w['worker_id']}/heartbeat",json={},headers=h).json()
    assert set(hb)=={'ok','paused','server_time'} and hb['paused'] is False
    view=client.get('/api/jobs/'+j['job_id']).json()
    assert set(view)=={'job_id','task_type','params','status','task_count','counts','created_at','started_at','finished_at','elapsed_ms','workers','tiles','leased','next_since'}
    assert view['counts']=={'pending':3,'leased':0,'done':1,'failed':0}
    assert set(view['tiles'][0])=={'task_id','idx','tile_x','tile_y','tile_w','tile_h','worker_id','worker_type','runtime_ms','attempts','data'}
    assert set(view['workers'][0])=={'worker_id','name','worker_type','tiles_done'}
    assert view['tiles'][0]['data']==[1]*1024
    delta=client.get(f"/api/jobs/{j['job_id']}?since={view['next_since']}").json()
    assert delta['tiles']==[] and delta['next_since']==view['next_since']
    jobs=client.get('/api/jobs').json()
    assert set(jobs)=={'jobs'}
    assert set(jobs['jobs'][0])=={'job_id','status','task_count','done_count','created_at','params'}
    assert jobs['jobs'][0]['done_count']==1
    assert client.get('/api/jobs/job_missing').status_code==404
    assert client.get('/api/jobs/'+j['job_id']+'?since=-1').status_code==422


@pytest.mark.parametrize('payload', [
    {'task_type':'run_code','params':PARAMS},
    {'task_type':'mandelbrot_v1','params':PARAMS | {'height':65}},
    {'task_type':'mandelbrot_v1','params':PARAMS,'code':'print(1)'},
    {'task_type':'mandelbrot_v1','params':PARAMS | {'url':'https://example.com'}},
])
def test_http_invalid_job_envelopes(client,payload):
    assert client.post('/api/jobs',json=payload).status_code==422


def test_http_nonfinite_parameters_return_422_not_500(client):
    import json
    raw=json.dumps({'task_type':'mandelbrot_v1','params':PARAMS | {'x_min':float('nan')}})
    response=client.post('/api/jobs',content=raw,headers={'Content-Type':'application/json'})
    assert response.status_code==422 and 'detail' in response.json()


@pytest.mark.parametrize('changes', [{'name':''},{'name':'a'*41},{'slots':0},{'slots':9},
                                      {'worker_type':'hpc'},{'client_info':'x'*201},{'extra':1}])
def test_http_registration_constraints(client,changes):
    payload=dict(name='Worker',worker_type='native',slots=1,task_types=['mandelbrot_v1']) | changes
    assert client.post('/api/workers/register',json=payload).status_code==422


def test_http_rejected_result_releases_lease_and_task_auth(client,database):
    a,ha=api_worker(client)
    b,hb=api_worker(client)
    for _ in range(2):
        assert client.post('/api/jobs',json={'task_type':'mandelbrot_v1','params':PARAMS}).status_code==200
    assert client.post('/api/jobs',json={'task_type':'mandelbrot_v1','params':PARAMS}).status_code==429
    t=client.post(f"/api/workers/{a['worker_id']}/lease",json={},headers=ha).json()
    payload=dict(worker_id=a['worker_id'],lease_token=t['lease_token'],result=[True]*1024,runtime_ms=5)
    url=f"/api/tasks/{t['task_id']}/complete"
    assert client.post(url,json=payload,headers=hb).status_code==401
    assert client.post(url,json=payload | {'worker_id':b['worker_id']},headers=hb).status_code==409
    assert client.post(url,json=payload,headers=ha).status_code==422
    assert task_row(database,t['task_id'])['status']=='pending'
    assert client.post(f"/api/tasks/{t['task_id']}/fail",json=dict(worker_id=a['worker_id'],lease_token=t['lease_token'],error='failed'),headers=ha).status_code==409
    assert client.post(f"/api/workers/{a['worker_id']}/pause",json={'unexpected':1},headers=ha).status_code==422


def test_worker_state_order_heartbeat_and_no_public_tokens(scheduler):
    s=scheduler
    left=worker(s)
    offline=worker(s)
    paused=worker(s)
    online=worker(s,slots=3)
    s.disconnect(left['worker_id'],101)
    s.pause(paused['worker_id'],101)
    s.heartbeat(paused['worker_id'],112)
    s.heartbeat(online['worker_id'],112)
    data=s.stats(113)
    assert set(data)=={'server_time','totals','workers'}
    assert data['totals']==dict(online=1,paused=1,offline=1,left=1,active_jobs=0,tasks_done=0,slots_online=3)
    assert [w['status'] for w in data['workers']]==['online','paused','offline','left']
    assert set(data['workers'][0])=={'worker_id','name','worker_type','status','slots','has_gpu','tasks_done','last_heartbeat_age_s','current_task_ids','registered_at'}
    assert data['workers'][0]['last_heartbeat_age_s']==1
    assert data['workers'][0]['has_gpu'] is False
    s.stats(114)
    assert sum(e['type']=='worker_offline' for e in s.events(0,500,None)['events'])==1
    s.heartbeat(offline['worker_id'],114)
    assert sum(e['type']=='worker_back' for e in s.events(0,500,None)['events'])==1
    assert 'token' not in str(s.events(0,500,None))


def test_job_delta_keeps_cursor_and_leased_list(scheduler):
    s=scheduler
    j=job(s)
    w=worker(s,slots=2)
    first=s.lease(w['worker_id'],100)
    second=s.lease(w['worker_id'],100)
    submit(s,w,first)
    view=s.job_view(j['job_id'],0,101)
    assert len(view['tiles'])==1 and len(view['leased'])==1
    assert view['leased'][0]['task_id']==second['task_id']
    assert set(view['leased'][0])=={'task_id','tile_x','tile_y','tile_w','tile_h','worker_id','lease_expires_at'}
    submit(s,w,second,now=102)
    delta=s.job_view(j['job_id'],view['next_since'],102)
    assert [t['task_id'] for t in delta['tiles']]==[second['task_id']]
    assert delta['next_since']>view['next_since']
    empty=s.job_view(j['job_id'],delta['next_since'],103)
    assert empty['tiles']==[] and empty['next_since']==delta['next_since']
    assert empty['elapsed_ms']==3000


def test_events_latest_limit_paging_and_filter(scheduler):
    s=scheduler
    first=job(s)
    second=job(s,now=101)
    w=worker(s)
    for n in range(5):
        s.pause(w['worker_id'],102+n)
    all_events=s.events(0,500,None)['events']
    latest=s.events(0,3,None)
    assert latest['events']==all_events[-3:]
    assert latest['next_since']==all_events[-1]['id']
    assert s.events(all_events[1]['id'],2,None)['events']==all_events[2:4]
    assert all(e['job_id']==first['job_id'] for e in s.events(0,500,first['job_id'])['events'])
    assert s.events(900,10,second['job_id'])=={'events':[],'next_since':900}
    assert set(all_events[0])=={'id','ts','type','job_id','task_id','worker_id','worker_name','message'}


def test_http_event_limit_and_stats(client):
    assert client.get('/api/events?limit=501').status_code==422
    assert client.get('/api/events?limit=0').status_code==422
    assert client.get('/api/events?since=-1').status_code==422
    assert client.get('/api/events?job_id=').json()=={'events':[],'next_since':0}
    assert client.get('/api/stats').json()['totals']['tasks_done']==0


def test_non_ascii_bearer_token_returns_401(client):
    w,h=api_worker(client)
    response=client.post(f"/api/workers/{w['worker_id']}/heartbeat",json={},
                         headers=[(b'authorization',b'Bearer \xc3\xa9')])
    assert response.status_code==401


@pytest.mark.parametrize('action',['complete','fail'])
def test_non_ascii_lease_token_returns_409(client,action):
    w,h=api_worker(client)
    client.post('/api/jobs',json={'task_type':'mandelbrot_v1','params':PARAMS})
    t=client.post(f"/api/workers/{w['worker_id']}/lease",json={},headers=h).json()
    body=dict(worker_id=w['worker_id'],lease_token='é')
    body.update(dict(result=[1]*1024,runtime_ms=1) if action=='complete' else dict(error='failed'))
    response=client.post(f"/api/tasks/{t['task_id']}/{action}",json=body,headers=h)
    assert response.status_code==409 and response.json()=={'detail':'stale_lease'}


@pytest.mark.parametrize('action',['complete','fail'])
def test_unrepresentable_task_id_is_not_found(client,action):
    w,h=api_worker(client)
    body=dict(worker_id=w['worker_id'],lease_token='unused')
    body.update(dict(result=[1]*1024,runtime_ms=1) if action=='complete' else dict(error='failed'))
    response=client.post(f'/api/tasks/{2**63}/{action}',json=body,headers=h)
    assert response.status_code==404


def test_future_cursors_return_empty_pages_without_overflow(client):
    j=client.post('/api/jobs',json={'task_type':'mandelbrot_v1','params':PARAMS}).json()
    cursor=2**100
    view=client.get(f"/api/jobs/{j['job_id']}?since={cursor}")
    assert view.status_code==200
    assert view.json()['tiles']==[] and view.json()['next_since']==cursor
    events=client.get(f'/api/events?since={cursor}')
    assert events.status_code==200 and events.json()=={'events':[],'next_since':cursor}


def test_unrepresentable_runtime_is_422_and_keeps_lease(client,database):
    w,h=api_worker(client)
    client.post('/api/jobs',json={'task_type':'mandelbrot_v1','params':PARAMS})
    t=client.post(f"/api/workers/{w['worker_id']}/lease",json={},headers=h).json()
    response=client.post(f"/api/tasks/{t['task_id']}/complete",headers=h,json=dict(
        worker_id=w['worker_id'],lease_token=t['lease_token'],result=[1]*1024,runtime_ms=2**63))
    assert response.status_code==422
    assert task_row(database,t['task_id'])['status']=='leased'


# Frozen deployed v1.1 schema: exercise real upgrades without deriving it
# from the implementation being tested.
LEGACY_SCHEMA = '\nCREATE TABLE IF NOT EXISTS workers (\n  id               TEXT PRIMARY KEY,         -- "wkr_" + 8 hex\n  name             TEXT NOT NULL,\n  worker_type      TEXT NOT NULL,            -- browser | native\n  token            TEXT NOT NULL,\n  slots            INTEGER NOT NULL DEFAULT 1,\n  has_gpu          INTEGER NOT NULL DEFAULT 0,\n  task_types       TEXT NOT NULL,            -- JSON 数组，服务端与 allowlist 取交集\n  client_info      TEXT,\n  paused           INTEGER NOT NULL DEFAULT 0,\n  disconnected     INTEGER NOT NULL DEFAULT 0,\n  offline_notified INTEGER NOT NULL DEFAULT 0,\n  last_heartbeat   REAL NOT NULL,\n  registered_at    REAL NOT NULL,\n  tasks_done       INTEGER NOT NULL DEFAULT 0\n);\nCREATE TABLE IF NOT EXISTS jobs (\n  id          TEXT PRIMARY KEY,              -- "job_" + 6 hex\n  task_type   TEXT NOT NULL,\n  params_json TEXT NOT NULL,\n  status      TEXT NOT NULL,                 -- queued | running | completed | failed | cancelled\n  task_count  INTEGER NOT NULL,\n  created_at  REAL NOT NULL,\n  started_at  REAL,\n  finished_at REAL\n);\nCREATE TABLE IF NOT EXISTS tasks (\n  id            INTEGER PRIMARY KEY AUTOINCREMENT,\n  job_id        TEXT NOT NULL,\n  idx           INTEGER NOT NULL,\n  task_type     TEXT NOT NULL,\n  input_json    TEXT NOT NULL,\n  status        TEXT NOT NULL DEFAULT \'pending\',   -- pending | leased | done | failed\n  worker_id     TEXT,\n  lease_token   TEXT,\n  lease_expires REAL,\n  attempts      INTEGER NOT NULL DEFAULT 0,\n  result_json   TEXT,\n  done_by       TEXT,\n  runtime_ms    INTEGER,\n  done_at       REAL,\n  done_seq      INTEGER                           -- = 完成时插入的 task_done 事件 id\n);\nCREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, job_id, idx);\nCREATE TABLE IF NOT EXISTS events (\n  id        INTEGER PRIMARY KEY AUTOINCREMENT,\n  ts        REAL NOT NULL,\n  type      TEXT NOT NULL,\n  job_id    TEXT,\n  task_id   INTEGER,\n  worker_id TEXT,\n  message   TEXT NOT NULL\n);\n'


@pytest.fixture
def legacy_database(tmp_path, monkeypatch):
    import sqlite3
    import db
    db.close_db()
    path=tmp_path/'legacy.db'
    monkeypatch.setenv('TG_DB_PATH',str(path))
    monkeypatch.setenv('TG_RESET_DB','0')
    with sqlite3.connect(path) as c:
        c.executescript(LEGACY_SCHEMA)
        c.execute("""INSERT INTO workers(id,name,worker_type,token,task_types,last_heartbeat,registered_at,tasks_done)
                     VALUES ('wkr_old','Legacy device','native','private-test-token','["mandelbrot_v1"]',120,100,2)""")
        c.execute("INSERT INTO jobs(id,task_type,params_json,status,task_count,created_at) VALUES ('job_old','mandelbrot_v1','{}','completed',2,100)")
        for index,data in enumerate(('[1,2,3]','[4,5,6]')):
            c.execute("""INSERT INTO tasks(job_id,idx,task_type,input_json,status,result_json,done_by,runtime_ms,done_at,done_seq)
                         VALUES ('job_old',?,'mandelbrot_v1','{}','done',?,'wkr_old',10,?,?)""",(index,data,110+index,index+1))
        c.execute("INSERT INTO events(ts,type,message) VALUES (110,'task_done','legacy event')")
    yield db
    db.close_db()


@pytest.mark.parametrize('partial',[False,True])
def test_contribution_migration_preserves_legacy_rows_and_is_repeatable(legacy_database,partial):
    import os,sqlite3
    db=legacy_database
    if partial:
        with sqlite3.connect(os.environ['TG_DB_PATH']) as c:
            c.execute('ALTER TABLE workers ADD COLUMN device_id TEXT')
    for _ in range(2):
        db.init_db()
        with db.tx() as c:
            assert {'device_id','owner'} <= {r['name'] for r in c.execute('PRAGMA table_info(workers)')}
            assert 'credit_iters' in {r['name'] for r in c.execute('PRAGMA table_info(tasks)')}
            w=c.execute("SELECT * FROM workers WHERE id='wkr_old'").fetchone()
            assert w['name']=='Legacy device' and w['tasks_done']==2 and w['device_id'] is None
            assert c.execute('SELECT count(*) FROM tasks').fetchone()[0]==2
            assert c.execute('SELECT count(*) FROM jobs').fetchone()[0]==1
            assert c.execute('SELECT message FROM events').fetchone()[0]=='legacy event'
            assert 'idx_workers_device' in {r['name'] for r in c.execute('PRAGMA index_list(workers)')}


@pytest.mark.parametrize('device_id',['dev_abcdefgh','dev_'+'a'*32,'dev_0123456789abcdef'])
def test_register_accepts_device_identity_and_normalizes_owner(client,database,device_id):
    w,h=api_worker(client,device_id=device_id,owner='  hyang2  ')
    assert set(w)=={'worker_id','token','heartbeat_interval_s','lease_ttl_s'}
    with database.tx() as c:
        row=c.execute('SELECT device_id,owner FROM workers WHERE id=?',(w['worker_id'],)).fetchone()
        assert tuple(row)==(device_id,'hyang2')
        assert 'owner hyang2' in c.execute("SELECT message FROM events WHERE worker_id=? AND type='worker_joined'",(w['worker_id'],)).fetchone()[0]


@pytest.mark.parametrize('extra',[{'device_id':'bad'}, {'device_id':'dev_ABCDEF12'}, {'device_id':'dev_1234567'},
                                 {'device_id':'dev_'+'a'*33},{'owner':'a'*41},{'owner':12}])
def test_register_rejects_bad_device_metadata(client,extra):
    r=client.post('/api/workers/register',json=dict(name='Device',worker_type='native',task_types=['mandelbrot_v1'])|extra)
    assert r.status_code==422


@pytest.mark.parametrize('extra',[{}, {'device_id':None,'owner':None},{'owner':' \t '}])
def test_old_clients_get_worker_fallback_identity(client,database,extra):
    w,h=api_worker(client,**extra)
    with database.tx() as c:
        row=c.execute('SELECT device_id,owner FROM workers WHERE id=?',(w['worker_id'],)).fetchone()
        assert tuple(row)==('wkr:'+w['worker_id'],None)


def test_contribution_credit_written_only_once_with_eight_samples(scheduler,database,monkeypatch):
    s=scheduler
    j=job(s)
    w=worker(s)
    task=s.lease(w['worker_id'],100)
    sampled=[]
    def pixel(x,y,p):
        sampled.append((x,y))
        return 1
    monkeypatch.setattr(s,'compute_pixel',pixel)
    assert submit(s,w,task)=={'accepted':True}
    assert len(sampled)==len(set(sampled))==8
    assert task_row(database,task['task_id'])['credit_iters']==1024
    assert_http(409,submit,s,w,task)
    assert task_row(database,task['task_id'])['credit_iters']==1024


def test_contributions_group_sessions_without_multiplying_metadata(scheduler):
    s=scheduler
    job(s)
    a=worker(s,name='Old name',device_id='dev_shared01',owner='old-owner')
    t=s.lease(a['worker_id'],100)
    submit(s,a,t,now=102)
    s.heartbeat(a['worker_id'],110)
    b=worker(s,now=120,name='New name',worker_type='browser',device_id='dev_shared01',owner='new-owner')
    for now in (121,122):
        t=s.lease(b['worker_id'],now)
        submit(s,b,t,now=now)
    s.heartbeat(b['worker_id'],150)
    c=worker(s,now=130,name='Other device',device_id='dev_distinct',owner='new-owner')
    t=s.lease(c['worker_id'],131)
    submit(s,c,t,now=132)
    s.disconnect(c['worker_id'],135)
    view=s.contributions(None,None,160)
    assert set(view)=={'generated_at','unit','basis','totals','devices'}
    assert view['unit']=='Miter'
    assert view['basis']=='sum of per-pixel iterations in accepted, spot-checked results'
    assert view['totals']==dict(devices=2,tiles_done=4,verified_miter=0.004096)
    first,second=view['devices']
    assert first==dict(device_id='dev_shared01',display_name='New name',owner='new-owner',
        worker_types=['browser','native'],workers=2,tiles_done=3,verified_miter=0.003072,share=0.75,
        compute_ms=15,online_s=40.0,first_seen='1970-01-01T00:01:40.000Z',last_seen='1970-01-01T00:02:30.000Z')
    assert second['device_id']=='dev_distinct' and second['share']==0.25
    assert sum(d['share'] for d in view['devices'])==pytest.approx(1)
    bounded=s.contributions(132,132,160)
    assert bounded['totals']==dict(devices=2,tiles_done=1,verified_miter=0.001024)
    assert bounded['devices'][0]['device_id']=='dev_distinct' and bounded['devices'][0]['share']==1
    assert bounded['devices'][1]['online_s']==40.0 and bounded['devices'][1]['workers']==2
    assert bounded['devices'][1]['tiles_done']==0


def test_contribution_credit_excludes_rejection_stale_duplicate_and_reassigned_loser(scheduler,database):
    s=scheduler
    job(s)
    loser=worker(s,device_id='dev_loser123')
    winner=worker(s,device_id='dev_winner12')
    t=s.lease(loser['worker_id'],100)
    assert_http(422,submit,s,loser,t,result=[0]*1024)
    assert task_row(database,t['task_id'])['credit_iters'] is None
    assert s.contributions(None,None,101)['totals']['verified_miter']==0
    old=s.lease(loser['worker_id'],101)
    s.reap(112)
    fresh=s.lease(winner['worker_id'],112)
    assert fresh['task_id']==old['task_id']
    assert_http(409,submit,s,loser,old,now=113)
    assert s.contributions(None,None,113)['totals']['tiles_done']==0
    submit(s,winner,fresh,now=113)
    assert_http(409,submit,s,winner,fresh,now=114)
    view=s.contributions(None,None,114)
    assert view['totals']==dict(devices=2,tiles_done=1,verified_miter=0.001024)
    assert [(d['device_id'],d['tiles_done'],d['share']) for d in view['devices']]==[
        ('dev_winner12',1,1.0),('dev_loser123',0,0.0)]


def test_contribution_zero_totals_and_old_client_sessions(scheduler):
    s=scheduler
    assert s.contributions(None,None,100)['totals']==dict(devices=0,tiles_done=0,verified_miter=0)
    w=worker(s)
    v=s.contributions(1000,None,110)
    assert v['totals']==dict(devices=1,tiles_done=0,verified_miter=0)
    assert v['devices'][0]['device_id']=='wkr:'+w['worker_id']
    assert v['devices'][0]['share']==0
    assert v['devices'][0]['owner'] is None


def test_contribution_legacy_results_are_computed_without_backfill(legacy_database):
    import scheduler as s
    db=legacy_database
    db.init_db()
    v=s.contributions(None,None,130)
    assert v['totals']==dict(devices=1,tiles_done=2,verified_miter=0.000021)
    assert v['devices'][0]['device_id']=='wkr:wkr_old'
    assert v['devices'][0]['compute_ms']==20 and v['devices'][0]['online_s']==20
    assert s.contributions(110,110,130)['totals']['verified_miter']==0.000006
    with db.tx() as c:
        assert c.execute('SELECT count(*) FROM tasks WHERE credit_iters IS NULL').fetchone()[0]==2
        assert c.execute('SELECT device_id FROM workers').fetchone()[0] is None


@pytest.mark.parametrize('query',[{'since':'nonsense'},{'until':'2026-11-07'}, {'since':'100'},
    {'since':'2026-11-07T12:00:00'}, {'since':'2026-11-07T14:00:00Z','until':'2026-11-07T13:59:59Z'}])
@pytest.mark.parametrize('path',['/api/contributions','/api/contributions.csv'])
def test_contribution_invalid_dates_return_422(client,path,query):
    assert client.get(path,params=query).status_code==422


def test_contributions_http_time_filter_and_csv_match(client,scheduler):
    import csv,io
    s=scheduler
    job(s)
    w=worker(s,name='笔记本, "A"\nsecond line',device_id='dev_csv12345',owner=' owner1 ')
    for now in (101,102):
        t=s.lease(w['worker_id'],now)
        submit(s,w,t,now=now)
    query={'since':'1970-01-01T01:01:42+01:00','until':'1970-01-01T00:01:42Z'}
    r=client.get('/api/contributions',params=query)
    assert r.status_code==200
    data=r.json()
    assert data['totals']['tiles_done']==1
    response=client.get('/api/contributions.csv',params=query)
    assert response.status_code==200
    assert response.headers['content-type'].startswith('text/csv')
    assert response.headers['content-disposition']=='attachment; filename="tartangrid_contributions.csv"'
    reader=csv.DictReader(io.StringIO(response.text,newline=''))
    assert reader.fieldnames==['device_id','display_name','owner','worker_types','workers','tiles_done',
                              'verified_miter','share','compute_ms','online_s','first_seen','last_seen']
    rows=list(reader)
    assert len(rows)==1
    for key,value in data['devices'][0].items():
        expected=';'.join(value) if key=='worker_types' else '' if value is None else str(value)
        assert rows[0][key]==expected
    assert client.get('/api/contributions.csv?since=9999-01-01T00:00:00Z').status_code==200


def test_empty_contributions_csv_still_has_header(client):
    import csv,io
    r=client.get('/api/contributions.csv')
    assert r.status_code==200
    rows=list(csv.reader(io.StringIO(r.text)))
    assert len(rows)==1 and len(rows[0])==12


@pytest.mark.parametrize('name,owner', [
    ('=1+1', '=HYPERLINK("https://example.org","open")'),
    ('+1+1', '-1+1'),
    ('@SUM(1)', '=1'),
    ('  =1+1', '+1'),
    ('\tname', '-1'),
    ('\rname', '@SUM(1)'),
    ('\nname', '=1'),
])
def test_contributions_csv_escapes_formula_labels_without_changing_json(client,scheduler,name,owner):
    import csv,io
    worker(scheduler,name=name,device_id='dev_formula1',owner=owner)
    data=client.get('/api/contributions').json()['devices'][0]
    assert data['display_name']==name and data['owner']==owner
    response=client.get('/api/contributions.csv')
    assert response.status_code==200
    row=next(csv.DictReader(io.StringIO(response.text,newline='')))
    assert row['display_name']=="'"+name
    assert row['owner']=="'"+owner
    assert row['tiles_done']=='0' and row['verified_miter']=='0.0'


def test_contributions_sum_large_accepted_runtimes_without_sqlite_overflow(scheduler):
    s=scheduler
    job(s)
    first=worker(s,device_id='dev_bigsum12')
    second=worker(s,now=101,device_id='dev_bigsum12')
    runtime=2**63-1
    for w in (first,first,second):
        t=s.lease(w['worker_id'],102)
        s.complete(t['task_id'],w['worker_id'],t['lease_token'],[1]*1024,runtime,102)
    view=s.contributions(None,None,103)
    assert view['devices'][0]['compute_ms']==3*runtime
    assert view['devices'][0]['tiles_done']==3
