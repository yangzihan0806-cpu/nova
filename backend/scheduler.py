"""FIFO Mandelbrot scheduling. Server timestamps are passed in by callers.

Public operations own one transaction; internal helpers reuse its connection.
No background threads and no nested acquisition of db.LOCK.
"""
import json
import random
import secrets
from datetime import datetime, timezone

from fastapi import HTTPException

import db
import security_audit
from schemas import JobParams


def _compute_pixel_fallback(px, py, p):          # px, py are full-image pixel coordinates; py = 0 is the top row
    dx = (p["x_max"] - p["x_min"]) / p["image_width"]
    dy = (p["y_max"] - p["y_min"]) / p["image_height"]
    cr = p["x_min"] + (px + 0.5) * dx
    ci = p["y_max"] - (py + 0.5) * dy
    zr = 0.0; zi = 0.0; n = 0
    while n < p["max_iter"] and zr*zr + zi*zi <= 4.0:
        t = zr*zr - zi*zi + cr
        zi = 2.0*zr*zi + ci
        zr = t
        n += 1
    return n


try:
    from mandelbrot import compute_pixel
except ImportError:
    compute_pixel = _compute_pixel_fallback

_RANDOM = random.Random()


def iso(ts):
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def log_event(conn, now, type, message, job_id=None, task_id=None, worker_id=None):
    return conn.execute(
        'INSERT INTO events(ts,type,job_id,task_id,worker_id,message) VALUES (?,?,?,?,?,?)',
        (now, type, job_id, task_id, worker_id, message),
    ).lastrowid


def _worker(conn, worker_id):
    row = conn.execute('SELECT * FROM workers WHERE id=?', (worker_id,)).fetchone()
    if row is None:
        raise HTTPException(404, 'worker_not_found')
    return row


def authenticate(worker_id, token):
    with db.tx() as conn:
        row = conn.execute('SELECT token FROM workers WHERE id=?', (worker_id,)).fetchone()
        if row is None or not token or not token.isascii() or not secrets.compare_digest(row['token'], token):
            raise HTTPException(401, 'unauthorized')


def create_job(params, now, audit_request_id=None):
    if not isinstance(params, JobParams):
        params = JobParams.model_validate(params)
    p = params.model_dump()
    size = p['tile_size']
    count = (p['width'] // size) * (p['height'] // size)
    with db.tx() as conn:
        active = conn.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
        if active >= db.MAX_ACTIVE_JOBS:
            raise HTTPException(429, 'too_many_active_jobs')
        while True:
            job_id = 'job_' + secrets.token_hex(3)
            if conn.execute('SELECT 1 FROM jobs WHERE id=?', (job_id,)).fetchone() is None:
                break
        conn.execute('INSERT INTO jobs(id,task_type,params_json,status,task_count,created_at) VALUES (?,?,?,?,?,?)',
                     (job_id, 'mandelbrot_v1', json.dumps(p), 'queued', count, now))
        tiles = []
        for y in range(0, p['height'], size):
            for x in range(0, p['width'], size):
                data = dict(tile_x=x, tile_y=y, tile_w=size, tile_h=size,
                            image_width=p['width'], image_height=p['height'], max_iter=p['max_iter'],
                            **{key: p[key] for key in ('x_min', 'x_max', 'y_min', 'y_max')})
                tiles.append((job_id, len(tiles), 'mandelbrot_v1', json.dumps(data)))
        conn.executemany('INSERT INTO tasks(job_id,idx,task_type,input_json) VALUES (?,?,?,?)', tiles)
        log_event(conn, now, 'job_created', f'Job {job_id} created with {count} tiles', job_id=job_id)
        if audit_request_id is not None:
            security_audit.record(conn, audit_request_id, 'allowed', 'SUBMISSION_ACCEPTED', now, job_id)
    return dict(job_id=job_id, status='queued', task_count=count)


def register_worker(name, worker_type, slots, has_gpu, task_types, client_info, now, device_id=None, owner=None):
    token = secrets.token_urlsafe(24)
    with db.tx() as conn:
        while True:
            worker_id = 'wkr_' + secrets.token_hex(4)
            if conn.execute('SELECT 1 FROM workers WHERE id=?', (worker_id,)).fetchone() is None:
                break
        allowed = ['mandelbrot_v1'] if 'mandelbrot_v1' in task_types else []
        device_id = device_id or 'wkr:' + worker_id
        owner = (owner.strip() or None) if owner is not None else None
        conn.execute('''INSERT INTO workers(id,name,worker_type,token,slots,has_gpu,task_types,
                       client_info,last_heartbeat,registered_at,device_id,owner) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                     (worker_id, name, worker_type, token, slots, int(has_gpu), json.dumps(allowed), client_info, now, now, device_id, owner))
        owner_text = f', owner {owner}' if owner is not None else ''
        log_event(conn, now, 'worker_joined',
                  f"{name} joined ({worker_type}, {slots} {'slot' if slots == 1 else 'slots'}{owner_text})",
                  worker_id=worker_id)
    return dict(worker_id=worker_id, token=token, heartbeat_interval_s=db.HEARTBEAT_S, lease_ttl_s=db.LEASE_TTL_S)


def heartbeat(worker_id, now):
    with db.tx() as conn:
        w = _worker(conn, worker_id)
        conn.execute('UPDATE workers SET last_heartbeat=?,offline_notified=0 WHERE id=?', (now, worker_id))
        if w['offline_notified']:
            log_event(conn, now, 'worker_back', f"Worker {w['name']} is back", worker_id=worker_id)
    return dict(ok=True, paused=bool(w['paused']), server_time=iso(now))


def _fail_jobs(conn, now):
    failed = conn.execute('''SELECT id FROM jobs WHERE status IN ('queued','running')
                            AND EXISTS (SELECT 1 FROM tasks WHERE tasks.job_id=jobs.id AND tasks.status='failed')''').fetchall()
    for row in failed:
        conn.execute("UPDATE jobs SET status='failed',finished_at=? WHERE id=?", (now, row['id']))
        log_event(conn, now, 'job_failed', f"Job {row['id']} failed", job_id=row['id'])


def _release(conn, task, now, event_type):
    failed = task['attempts'] >= db.MAX_ATTEMPTS
    conn.execute('''UPDATE tasks SET status=?,worker_id=NULL,lease_token=NULL,lease_expires=NULL WHERE id=?''',
                 ('failed' if failed else 'pending', task['id']))
    if event_type != 'lease_expired' or not failed:
        log_event(conn, now, event_type,
                  f"Task {task['id']} {event_type.replace('_', ' ')} -> {'failed' if failed else 'requeued'}",
                  job_id=task['job_id'], task_id=task['id'], worker_id=task['worker_id'])
    if failed:
        log_event(conn, now, 'task_failed', f"Task {task['id']} exhausted {task['attempts']} attempts",
                  job_id=task['job_id'], task_id=task['id'], worker_id=task['worker_id'])


def _reap(conn, now):
    for task in conn.execute("SELECT * FROM tasks WHERE status='leased' AND lease_expires < ?", (now,)).fetchall():
        _release(conn, task, now, 'lease_expired')
    # CONTRACT-NOTE: disconnect always releases to pending (§6.6). Exhausted
    # pending tasks fail on the next reap, preventing a fourth lease (§3).
    exhausted = conn.execute("SELECT * FROM tasks WHERE status='pending' AND attempts >= ?", (db.MAX_ATTEMPTS,)).fetchall()
    for task in exhausted:
        conn.execute("UPDATE tasks SET status='failed' WHERE id=?", (task['id'],))
        log_event(conn, now, 'task_failed', f"Task {task['id']} exhausted its attempts",
                  job_id=task['job_id'], task_id=task['id'])
    _fail_jobs(conn, now)
    offline = conn.execute('''SELECT id,name FROM workers WHERE disconnected=0 AND offline_notified=0
                              AND ? - last_heartbeat > ?''', (now, db.OFFLINE_AFTER_S)).fetchall()
    for w in offline:
        conn.execute('UPDATE workers SET offline_notified=1 WHERE id=?', (w['id'],))
        log_event(conn, now, 'worker_offline', f"Worker {w['name']} is offline", worker_id=w['id'])


def reap(now):
    with db.tx() as conn:
        _reap(conn, now)


def lease(worker_id, now):
    with db.tx() as conn:
        _reap(conn, now)
        w = _worker(conn, worker_id)
        if w['paused'] or w['disconnected']:
            return None
        held = conn.execute("SELECT count(*) FROM tasks WHERE status='leased' AND worker_id=?", (worker_id,)).fetchone()[0]
        if held >= w['slots'] or 'mandelbrot_v1' not in json.loads(w['task_types']):
            return None
        token = secrets.token_urlsafe(16)
        task = conn.execute('''UPDATE tasks SET status='leased',worker_id=?,lease_token=?,
                       lease_expires=?,attempts=attempts+1
                       WHERE id=(SELECT t.id FROM tasks t JOIN jobs j ON j.id=t.job_id
                                 WHERE t.status='pending' AND j.status IN ('queued','running')
                                 AND t.task_type='mandelbrot_v1' AND t.attempts < ?
                                 ORDER BY j.created_at,j.rowid,t.idx LIMIT 1)
                       AND status='pending' RETURNING id,job_id,task_type,input_json,attempts''',
                            (worker_id, token, now + db.LEASE_TTL_S, db.MAX_ATTEMPTS)).fetchone()
        if task is None:
            return None
        started = conn.execute("UPDATE jobs SET status='running',started_at=? WHERE id=? AND status='queued'",
                               (now, task['job_id'])).rowcount
        if started:
            log_event(conn, now, 'job_started', f"Job {task['job_id']} started", job_id=task['job_id'])
        log_event(conn, now, 'task_leased', f"Task {task['id']} leased to {w['name']} (attempt {task['attempts']})",
                  job_id=task['job_id'], task_id=task['id'], worker_id=worker_id)
        return dict(task_id=task['id'], job_id=task['job_id'], task_type=task['task_type'],
                    lease_token=token, lease_expires_at=iso(now + db.LEASE_TTL_S), input=json.loads(task['input_json']))


def _held_task(conn, task_id, worker_id, lease_token):
    if not 1 <= task_id <= 2**63 - 1:
        raise HTTPException(404, 'task_not_found')
    t = conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
    if t is None:
        raise HTTPException(404, 'task_not_found')
    if (t['status'] != 'leased' or t['worker_id'] != worker_id
            or not lease_token.isascii()
            or not secrets.compare_digest(t['lease_token'] or '', lease_token)):
        raise HTTPException(409, 'stale_lease')
    return t


def _valid_result(result, p):
    length = p['tile_w'] * p['tile_h']
    if not isinstance(result, list) or len(result) != length:
        return False
    if any(type(n) is not int or n < 0 or n > p['max_iter'] for n in result):
        return False
    for offset in _RANDOM.sample(range(length), min(db.SPOT_CHECKS, length)):
        x = p['tile_x'] + offset % p['tile_w']
        y = p['tile_y'] + offset // p['tile_w']
        if result[offset] != compute_pixel(x, y, p):
            return False
    return True


def complete(task_id, worker_id, lease_token, result, runtime_ms, now):
    with db.tx() as conn:
        # Do not reap here: a late result with an unreaped lease is valid (§7).
        task = _held_task(conn, task_id, worker_id, lease_token)
        valid = _valid_result(result, json.loads(task['input_json']))
        if not valid:
            _release(conn, task, now, 'task_rejected')
            _fail_jobs(conn, now)
        else:
            changed = conn.execute('''UPDATE tasks SET status='done',result_json=?,done_by=?,runtime_ms=?,done_at=?,
                                      credit_iters=?,worker_id=NULL,lease_token=NULL,lease_expires=NULL
                                      WHERE id=? AND status='leased' AND lease_token=?''',
                                   (json.dumps(result), worker_id, runtime_ms, now, sum(result), task_id, lease_token)).rowcount
            if not changed:
                raise HTTPException(409, 'stale_lease')
            seq = log_event(conn, now, 'task_done', f'Task {task_id} completed',
                            job_id=task['job_id'], task_id=task_id, worker_id=worker_id)
            conn.execute('UPDATE tasks SET done_seq=? WHERE id=?', (seq, task_id))
            conn.execute('UPDATE workers SET tasks_done=tasks_done+1 WHERE id=?', (worker_id,))
            finished = conn.execute('''UPDATE jobs SET status='completed',finished_at=? WHERE id=?
                                      AND status IN ('queued','running')
                                      AND NOT EXISTS (SELECT 1 FROM tasks WHERE job_id=? AND status!='done')''',
                                    (now, task['job_id'], task['job_id'])).rowcount
            if finished:
                log_event(conn, now, 'job_completed', f"Job {task['job_id']} completed", job_id=task['job_id'])
    # Raising inside tx would roll back the requeue as well as the event.
    if not valid:
        raise HTTPException(422, 'invalid_result')
    return {'accepted': True}


def fail(task_id, worker_id, lease_token, error, now):
    with db.tx() as conn:
        task = _held_task(conn, task_id, worker_id, lease_token)
        # Client error text may contain credentials; public events use safe copy.
        _release(conn, task, now, 'task_error')
        _fail_jobs(conn, now)
    return {'ok': True}


def _set_paused(worker_id, paused, now):
    with db.tx() as conn:
        w = _worker(conn, worker_id)
        conn.execute('UPDATE workers SET paused=? WHERE id=?', (int(paused), worker_id))
        kind = 'worker_paused' if paused else 'worker_resumed'
        log_event(conn, now, kind, f"Worker {w['name']} {'paused' if paused else 'resumed'}", worker_id=worker_id)
    return {'ok': True}


def pause(worker_id, now):
    return _set_paused(worker_id, True, now)


def resume(worker_id, now):
    return _set_paused(worker_id, False, now)


def disconnect(worker_id, now):
    with db.tx() as conn:
        w = _worker(conn, worker_id)
        conn.execute('UPDATE workers SET disconnected=1 WHERE id=?', (worker_id,))
        tasks = conn.execute("SELECT * FROM tasks WHERE status='leased' AND worker_id=?", (worker_id,)).fetchall()
        for task in tasks:
            conn.execute("UPDATE tasks SET status='pending',worker_id=NULL,lease_token=NULL,lease_expires=NULL WHERE id=?", (task['id'],))
            log_event(conn, now, 'lease_released', f"Task {task['id']} released by {w['name']} -> requeued",
                      job_id=task['job_id'], task_id=task['id'], worker_id=worker_id)
        log_event(conn, now, 'worker_left', f"Worker {w['name']} left", worker_id=worker_id)
    return {'ok': True}


def list_jobs():
    with db.tx() as conn:
        rows = conn.execute('''SELECT j.*, (SELECT count(*) FROM tasks t WHERE t.job_id=j.id AND t.status='done') AS done_count
                               FROM jobs j ORDER BY created_at DESC,rowid DESC LIMIT 20''').fetchall()
        return {'jobs': [dict(job_id=j['id'], status=j['status'], task_count=j['task_count'],
                              done_count=j['done_count'], created_at=iso(j['created_at']),
                              params=json.loads(j['params_json'])) for j in rows]}


def job_view(job_id, since, now):
    with db.tx() as conn:
        _reap(conn, now)
        j = conn.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if j is None:
            raise HTTPException(404, 'job_not_found')
        counts = dict(pending=0, leased=0, done=0, failed=0)
        for r in conn.execute('SELECT status,count(*) AS n FROM tasks WHERE job_id=? GROUP BY status', (job_id,)):
            counts[r['status']] = r['n']
        # Only fetch/decode result arrays for the requested delta, never historical tiles.
        rows = conn.execute('''SELECT t.*,w.worker_type FROM tasks t JOIN workers w ON w.id=t.done_by
                               WHERE t.job_id=? AND t.status='done' AND t.done_seq>? ORDER BY t.done_seq''',
                            (job_id, min(since, 2**63 - 1))).fetchall()
        tiles = []
        for t in rows:
            p = json.loads(t['input_json'])
            tiles.append(dict(task_id=t['id'], idx=t['idx'],
                              **{k: p[k] for k in ('tile_x', 'tile_y', 'tile_w', 'tile_h')},
                              worker_id=t['done_by'], worker_type=t['worker_type'], runtime_ms=t['runtime_ms'],
                              attempts=t['attempts'], data=json.loads(t['result_json'])))
        leased = []
        for t in conn.execute("SELECT id,input_json,worker_id,lease_expires FROM tasks WHERE job_id=? AND status='leased' ORDER BY idx", (job_id,)):
            p = json.loads(t['input_json'])
            leased.append(dict(task_id=t['id'], **{k: p[k] for k in ('tile_x', 'tile_y', 'tile_w', 'tile_h')},
                               worker_id=t['worker_id'], lease_expires_at=iso(t['lease_expires'])))
        # CONTRACT-NOTE: workers enumerates completed-tile contributors; current
        # holders are separately exposed by leased. Empty deltas retain since.
        workers = [dict(worker_id=w['id'], name=w['name'], worker_type=w['worker_type'], tiles_done=w['tiles_done'])
                   for w in conn.execute('''SELECT w.id,w.name,w.worker_type,count(*) AS tiles_done FROM tasks t
                                            JOIN workers w ON w.id=t.done_by WHERE t.job_id=? AND t.status='done'
                                            GROUP BY w.id ORDER BY w.registered_at,w.id''', (job_id,))]
        end = j['finished_at'] if j['finished_at'] is not None else now
        return dict(job_id=j['id'], task_type=j['task_type'], params=json.loads(j['params_json']),
                    status=j['status'], task_count=j['task_count'], counts=counts,
                    created_at=iso(j['created_at']), started_at=iso(j['started_at']), finished_at=iso(j['finished_at']),
                    elapsed_ms=max(0, int((end - j['created_at']) * 1000)), workers=workers, tiles=tiles,
                    leased=leased, next_since=max([since] + [t['done_seq'] for t in rows]))


def _worker_status(w, now):
    if w['disconnected']:
        return 'left'
    if now - w['last_heartbeat'] > db.OFFLINE_AFTER_S:
        return 'offline'
    return 'paused' if w['paused'] else 'online'


def stats(now):
    with db.tx() as conn:
        _reap(conn, now)
        current = {}
        for t in conn.execute("SELECT id,worker_id FROM tasks WHERE status='leased' ORDER BY id"):
            current.setdefault(t['worker_id'], []).append(t['id'])
        workers = []
        totals = dict(online=0, paused=0, offline=0, left=0, active_jobs=0, tasks_done=0, slots_online=0)
        for w in conn.execute('SELECT * FROM workers ORDER BY registered_at,id'):
            status = _worker_status(w, now)
            totals[status] += 1
            totals['tasks_done'] += w['tasks_done']
            if status == 'online':
                totals['slots_online'] += w['slots']
            workers.append(dict(worker_id=w['id'], name=w['name'], worker_type=w['worker_type'], status=status,
                                slots=w['slots'], has_gpu=bool(w['has_gpu']), tasks_done=w['tasks_done'],
                                last_heartbeat_age_s=max(0, now - w['last_heartbeat']),
                                current_task_ids=current.get(w['id'], []), registered_at=iso(w['registered_at'])))
        order = dict(online=0, paused=1, offline=2, left=3)
        workers.sort(key=lambda w: order[w['status']])
        totals['active_jobs'] = conn.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
        return dict(server_time=iso(now), totals=totals, workers=workers)


def events(since, limit, job_id):
    with db.tx() as conn:
        where = ['e.id > ?']
        # Cursors beyond SQLite's maximum ID describe an empty future page.
        args = [min(since, 2**63 - 1)]
        if job_id:
            where.append('e.job_id=?')
            args.append(job_id)
        order = 'DESC' if since == 0 else 'ASC'
        rows = conn.execute(f'''SELECT e.*,w.name AS worker_name FROM events e LEFT JOIN workers w ON w.id=e.worker_id
                               WHERE {' AND '.join(where)} ORDER BY e.id {order} LIMIT ?''', (*args, limit)).fetchall()
        if since == 0:
            rows.reverse()
        data = [dict(id=e['id'], ts=iso(e['ts']), type=e['type'], job_id=e['job_id'], task_id=e['task_id'],
                     worker_id=e['worker_id'], worker_name=e['worker_name'], message=e['message']) for e in rows]
        # CONTRACT-NOTE: preserve the caller's cursor on empty/filtered pages.
        return dict(events=data, next_since=max([since] + [e['id'] for e in rows]))


def contributions(since, until, now):
    """Lifetime device metadata with task-time-filtered accepted contributions.

    Aggregate tasks per worker before grouping sessions into devices; otherwise
    joining every tile would multiply session counts and online durations.
    """
    with db.tx() as conn:
        rows = conn.execute('''
            WITH task_credit AS (
                SELECT done_by, count(*) AS tiles_done,
                       sum(COALESCE(credit_iters, legacy_credit_iters(result_json))) AS iters,
                       integer_sum(COALESCE(runtime_ms, 0)) AS compute_ms
                FROM tasks
                WHERE status='done'
                  AND (? IS NULL OR done_at >= ?) AND (? IS NULL OR done_at <= ?)
                GROUP BY done_by
            ), sessions AS (
                SELECT w.*, COALESCE(device_id, 'wkr:' || id) AS device_key,
                       row_number() OVER (
                           PARTITION BY COALESCE(device_id, 'wkr:' || id)
                           ORDER BY registered_at DESC, rowid DESC
                       ) AS recency
                FROM workers w
            ), devices AS (
                SELECT device_key, group_concat(DISTINCT worker_type) AS worker_types,
                       count(*) AS workers, sum(COALESCE(t.tiles_done, 0)) AS tiles_done,
                       sum(COALESCE(t.iters, 0)) AS iters,
                       integer_sum(COALESCE(t.compute_ms, 0)) AS compute_ms,
                       sum(max(0, last_heartbeat - registered_at)) AS online_s,
                       min(registered_at) AS first_seen, max(last_heartbeat) AS last_seen
                FROM sessions s LEFT JOIN task_credit t ON t.done_by=s.id
                GROUP BY device_key
            )
            SELECT d.*, s.name AS display_name, s.owner
            FROM devices d JOIN sessions s ON s.device_key=d.device_key AND s.recency=1
            ORDER BY d.iters DESC, d.device_key
        ''', (since, since, until, until)).fetchall()
    total_iters = sum(row['iters'] for row in rows)
    devices = []
    for row in rows:
        devices.append(dict(
            device_id=row['device_key'], display_name=row['display_name'], owner=row['owner'],
            worker_types=sorted(row['worker_types'].split(',')), workers=row['workers'],
            tiles_done=row['tiles_done'], verified_miter=row['iters'] / 1_000_000,
            share=round(row['iters'] / total_iters, 4) if total_iters else 0.0,
            compute_ms=int(row['compute_ms']), online_s=float(row['online_s']),
            first_seen=iso(row['first_seen']), last_seen=iso(row['last_seen']),
        ))
    return dict(generated_at=iso(now), unit='Miter',
                basis='sum of per-pixel iterations in accepted, spot-checked results',
                totals=dict(devices=len(devices), tiles_done=sum(d['tiles_done'] for d in devices),
                            verified_miter=total_iters / 1_000_000), devices=devices)
