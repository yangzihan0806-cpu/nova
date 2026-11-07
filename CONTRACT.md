# TartanGrid Engineering Contract (CONTRACT v1.2)

> v1.2 (2026-11-07): verified iteration credit is aggregated per random `device_id`; added optional `owner`, a lossless schema migration, and contribution JSON / CSV (§5, §6.1, §6.4, §6.15–16, §9); `TG_SPOT_CHECKS` default changed to 8. Updated with the increments agreed in the team TODO; existing lease / retry semantics are unchanged.

> v1.1 (2026-11-07): when `ANTHROPIC_API_KEY` is absent, the AI Planner uses the organizer-provided OpenRouter key (§1, §3); clarified that "a pending task that has used up its attempts is marked failed at the next reap" (§7). Everything else unchanged.

> Shared by all four members and **frozen**. Nobody (including your agent) may unilaterally change this contract or another person's files.
> If you find a problem with the contract: raise it in the group chat; once everyone agrees, D bumps the version (v1.2…) and notifies everyone.
> This file lives at the repo root as `CONTRACT.md`.

---

## 0. Non-negotiable principles

1. Only the allowlisted template `mandelbrot_v1` runs. User-submitted code, commands or URLs are **never accepted or executed**.
2. The **server** clock is authoritative. The DB stores unix seconds (`REAL`); the API outputs ISO 8601 UTC with millisecond precision and a trailing `Z`, e.g. `2026-11-07T17:20:45.123Z`.
3. No fabricated data: every number on the Dashboard comes from the API; anything not connected (GPU pool, CMU HPC) may only be labelled `Planned / Not connected`.
4. Multiple slots on one physical device are **one** worker; they must not pretend to be multiple computers.
5. Never commit API keys, tokens or `.db` files to git (`.gitignore` includes `*.db` and `.env`).
6. Only credit points are recorded; there is no money, token or redemption feature. Final rewards are decided separately by the team. `device_id` is randomly generated and must not use a hardware fingerprint; `owner` is voluntary, used by the team to match contribution records, and is not authentication.

---

## 1. Tech stack and how to run

| Part | Choice |
|---|---|
| Backend | Python ≥ 3.10, FastAPI, uvicorn, Pydantic v2, stdlib `sqlite3`, numpy |
| Frontend | **Plain HTML + CSS + JS**: no framework, no build step, no CDN dependency; served statically by FastAPI |
| Browser worker | Dedicated Web Worker (`static/worker.js`) |
| Native worker | Python + requests + numba (optional; falls back to numpy when missing) |
| Public access | `cloudflared tunnel --url http://localhost:8000`; every device uses the `https://*.trycloudflare.com` URL it prints |
| AI Planner | Anthropic API first (`ANTHROPIC_API_KEY`); otherwise OpenRouter (`OPENROUTER_API_KEY`, read from `.env` in the repo root). If neither is available it returns 503 and the frontend falls back to the form |

Start-up:

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
# in another terminal
cloudflared tunnel --url http://localhost:8000
# on a contributor machine
python workers/native_worker.py --server https://xxxx.trycloudflare.com --name Native-Harry --slots 2
```

Frontend pages (all served by FastAPI from `backend/static/` under `/`):

| URL | File | Purpose |
|---|---|---|
| `/` | `index.html` | Dashboard |
| `/new.html` | `new.html` | New job (form + AI Planner) |
| `/job.html?id=job_xxxxxx` | `job.html` | Job details + canvas |
| `/contribute.html` | `contribute.html` | Contribute compute from a browser |
| `/contributions.html` | `contributions.html` | Device contribution ranking, time filter and CSV export |

All frontend requests use **relative paths** `/api/...` (same origin, no CORS).

---

## 2. Directory layout and file ownership

```text
nova/
├─ CONTRACT.md                     # everyone (D maintains the version)
├─ README.md                       # D
├─ .gitignore                      # A
├─ backend/
│  ├─ requirements.txt             # A (D may append anthropic)
│  ├─ main.py                      # A  FastAPI app, routes, static hosting
│  ├─ db.py                        # A  SQLite connection, schema, global lock
│  ├─ schemas.py                   # A  Pydantic models (JobParams, etc.)
│  ├─ scheduler.py                 # A  splitting, lease, complete, reap, events
│  ├─ mandelbrot.py                # C  compute kernel (imported by A's validation and D's tests)
│  ├─ planner.py                   # D  AI Planner, exposes `router`
│  ├─ presets.json                 # D  preset parameters
│  └─ static/
│     ├─ index.html  new.html  job.html  contribute.html  contributions.html   # B
│     ├─ common.js  style.css      # B  shared JS utilities and styles
│     ├─ mandelbrot_kernel.js      # C  JS kernel
│     ├─ worker.js                 # C  Web Worker execution loop
│     └─ contributor.js            # C  browser contributor controller (called by B's pages)
├─ workers/
│  ├─ native_worker.py             # C
│  └─ benchmark.py                 # C
├─ scripts/
│  └─ smoke_test.py                # D
└─ tests/
   ├─ test_scheduler.py            # A
   ├─ test_kernel.py               # C
   └─ js_kernel_dump.js            # C
```

**Only edit your own files.** To change someone else's file, ask its owner.

---

## 3. Configuration constants (overridable by environment variables)

| Environment variable | Default | Meaning |
|---|---:|---|
| `TG_DB_PATH` | `backend/tartangrid.db` | SQLite path |
| `TG_RESET_DB` | `0` | `1` = wipe the database at start-up |
| `TG_LEASE_TTL_S` | `10` | task lease lifetime (seconds) |
| `TG_HEARTBEAT_S` | `3` | worker heartbeat interval (sent to the worker at register) |
| `TG_OFFLINE_AFTER_S` | `12` | a worker with no heartbeat for longer than this is offline |
| `TG_MAX_ATTEMPTS` | `3` | maximum number of times a task can be leased |
| `TG_MAX_ACTIVE_JOBS` | `2` | maximum number of jobs in queued/running at once |
| `TG_SPOT_CHECKS` | `8` | pixels per result that the server randomly recomputes |
| `TG_PLANNER_MODEL` | `claude-haiku-5-5` | model used by the Planner |
| `ANTHROPIC_API_KEY` | — | if set, the Planner uses Anthropic |
| `OPENROUTER_API_KEY` | — | without an Anthropic key the Planner uses OpenRouter (can be put in `.env` in the repo root) |
| `OPENROUTER_MODEL` | `openai/gpt-4o` | OpenRouter model name |

---

## 4. Task template `mandelbrot_v1`

### 4.1 JobParams (enforced server-side with Pydantic, `extra="forbid"`)

| Field | Type | Constraint |
|---|---|---|
| `width` | int | 64 ≤ w ≤ 4096, and a multiple of `tile_size` |
| `height` | int | 64 ≤ h ≤ 4096, and a multiple of `tile_size` |
| `tile_size` | int | ∈ {32, 64, 128} |
| `max_iter` | int | 1 ≤ m ≤ 20000 |
| `x_min`, `x_max`, `y_min`, `y_max` | float | finite, absolute value ≤ 4, `x_min < x_max`, `y_min < y_max` |

Derived: `task_count = (width / tile_size) * (height / tile_size)`, which must be ≤ 4096. Violating any rule → 422.

### 4.2 Splitting rule

```
cols = width / tile_size,  rows = height / tile_size
for ty_i in 0..rows-1, tx_i in 0..cols-1:
    idx    = ty_i * cols + tx_i
    tile_x = tx_i * tile_size,  tile_y = ty_i * tile_size
    tile_w = tile_h = tile_size
```

Each task's `input` (a JSON object with fixed fields):

```json
{"tile_x": 640, "tile_y": 128, "tile_w": 64, "tile_h": 64,
 "image_width": 1024, "image_height": 768, "max_iter": 350,
 "x_min": -2.2, "x_max": 1.0, "y_min": -1.2, "y_max": 1.2}
```

### 4.3 Reference kernel (authoritative definition; every implementation must match it **bit for bit**)

```python
def compute_pixel(px, py, p):          # px, py are full-image pixel coordinates; py = 0 is the top row
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
```

Implementation rules: IEEE double; **do not** rewrite the expressions (e.g. not as `x_min + px*dx + 0.5*dx`); no fastmath; no FMA.
Verified: written this way, scalar Python, vectorized numpy and numba `@njit` give bit-identical results. JS (V8) is also IEEE double and does not contract to FMA, so it should match; C must confirm this with tests.

### 4.4 Result format

`result`: a JSON array of integers of length `tile_w * tile_h`, row-major within the tile (`index = j * tile_w + i`, with `j` starting at the tile's top row); every value ∈ `[0, max_iter]`. A value equal to `max_iter` means the point did not escape (it is in the set).

### 4.5 Server-side result validation

1. Correct length, all integers, values in range;
2. Pick `TG_SPOT_CHECKS` random pixels and recompute them with `compute_pixel`; they must be **exactly equal**;
3. Any failure → 422; the task is requeued (subject to `TG_MAX_ATTEMPTS`) and a `task_rejected` event is recorded.

Spot-check recomputation is a probabilistic check; it cannot guarantee catching every error or cheat. "Verified" means the result passed the checks above, not that every pixel of the tile was recomputed.

### 4.6 Presets (`backend/presets.json`, tuned by D)

```json
[
  {"id": "quick", "label": "Quick test — full set (small)",
   "params": {"width": 512, "height": 384, "tile_size": 64, "max_iter": 256,
              "x_min": -2.2, "x_max": 1.0, "y_min": -1.2, "y_max": 1.2}},
  {"id": "demo", "label": "Demo — Seahorse Valley (heavy)",
   "params": {"width": 2048, "height": 1536, "tile_size": 128, "max_iter": 20000,
              "x_min": -0.8, "x_max": -0.7, "y_min": 0.05, "y_max": 0.125}}
]
```

Reference measurements (cloud test container, not your devices): the `demo` region at `max_iter=5000` averages about 3900 iterations per pixel, about $1.2\times10^{10}$ in total; numba on one core does about $3\times10^{8}$ iter/s, numpy about $1.3\times10^{8}$ iter/s. The real parameters are measured by D on real devices.

---

## 5. Database schema (SQLite, implemented by A)

```sql
CREATE TABLE IF NOT EXISTS workers (
  id               TEXT PRIMARY KEY,         -- "wkr_" + 8 hex
  name             TEXT NOT NULL,
  worker_type      TEXT NOT NULL,            -- browser | native
  token            TEXT NOT NULL,
  slots            INTEGER NOT NULL DEFAULT 1,
  has_gpu          INTEGER NOT NULL DEFAULT 0,
  task_types       TEXT NOT NULL,            -- JSON array, intersected with the server allowlist
  client_info      TEXT,
  paused           INTEGER NOT NULL DEFAULT 0,
  disconnected     INTEGER NOT NULL DEFAULT 0,
  offline_notified INTEGER NOT NULL DEFAULT 0,
  last_heartbeat   REAL NOT NULL,
  registered_at    REAL NOT NULL,
  tasks_done       INTEGER NOT NULL DEFAULT 0,
  device_id        TEXT,
  owner            TEXT
);
CREATE INDEX IF NOT EXISTS idx_workers_device ON workers(device_id);
CREATE TABLE IF NOT EXISTS jobs (
  id          TEXT PRIMARY KEY,              -- "job_" + 6 hex
  task_type   TEXT NOT NULL,
  params_json TEXT NOT NULL,
  status      TEXT NOT NULL,                 -- queued | running | completed | failed | cancelled
  task_count  INTEGER NOT NULL,
  created_at  REAL NOT NULL,
  started_at  REAL,
  finished_at REAL
);
CREATE TABLE IF NOT EXISTS tasks (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id        TEXT NOT NULL,
  idx           INTEGER NOT NULL,
  task_type     TEXT NOT NULL,
  input_json    TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'pending',   -- pending | leased | done | failed
  worker_id     TEXT,
  lease_token   TEXT,
  lease_expires REAL,
  attempts      INTEGER NOT NULL DEFAULT 0,
  result_json   TEXT,
  done_by       TEXT,
  runtime_ms    INTEGER,
  done_at       REAL,
  done_seq      INTEGER,                          -- = id of the task_done event inserted on completion
  credit_iters  INTEGER                           -- sum(result), written when accepted
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, job_id, idx);
CREATE TABLE IF NOT EXISTS events (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  ts        REAL NOT NULL,
  type      TEXT NOT NULL,
  job_id    TEXT,
  task_id   INTEGER,
  worker_id TEXT,
  message   TEXT NOT NULL
);
```

**Lossless migration**: on an existing database, `init_db()` checks with `PRAGMA table_info`, adds each missing column, then creates the indexes. It is idempotent; it must never require wiping the live DB or overwrite existing data.

```sql
ALTER TABLE workers ADD COLUMN device_id TEXT;
ALTER TABLE workers ADD COLUMN owner TEXT;
ALTER TABLE tasks ADD COLUMN credit_iters INTEGER;
CREATE INDEX IF NOT EXISTS idx_workers_device ON workers(device_id);
```

An old worker with `device_id IS NULL` is treated as `'wkr:' || id` at query time. For old completed tasks with `credit_iters IS NULL`, `init_db()` backfills `credit_iters` **once** at start-up by summing the stored `result_json` (the value is the same as summing on the fly; the query keeps the same fallback computation). Reason: summing on the fly recomputes every old result inside the global lock; with about 2000 tiles on the live server that takes about 2 seconds per query, and the Dashboard polls every 5 seconds, which would stall every worker's lease and heartbeat.

Derived worker status (for API output):

```
disconnected = 1                          → "left"
now - last_heartbeat > TG_OFFLINE_AFTER_S → "offline"
paused = 1                                → "paused"
otherwise                                 → "online"
```

---

## 6. HTTP API

General rules:

- Requests and responses are JSON, except the CSV response in §6.16. Errors use FastAPI's default `{"detail": ...}` format.
- **Worker auth**: `/api/workers/{id}/*` (except register) and `/api/tasks/*` require the header `Authorization: Bearer <token>`, and the token must belong to that worker → otherwise 401.
- Status codes: 200 success; 204 no task; 401 auth failed; 404 not found; 409 expired lease or duplicate submission; 422 invalid parameters/result; 429 too many active jobs; 503 Planner unavailable.

### 6.1 `POST /api/workers/register`

```json
// request
{"name": "Browser-Harry", "worker_type": "browser", "slots": 1,
 "has_gpu": false, "task_types": ["mandelbrot_v1"], "client_info": "Chrome 131 / macOS",
 "device_id": "dev_3f9a1c2b7e4d4a10", "owner": "hyang2"}
// response 200
{"worker_id": "wkr_1a2b3c4d", "token": "<opaque>",
 "heartbeat_interval_s": 3, "lease_ttl_s": 10}
```

Constraints: `name` 1–40 characters; `worker_type` ∈ {browser, native}; `slots` 1–8; `client_info` ≤ 200 characters, optional. `has_gpu` is display-only; the MVP does not schedule on it. Records a `worker_joined` event.

- `device_id` is optional; when non-null it must match `^dev_[a-z0-9]{8,32}$`, otherwise 422. When omitted or explicitly `null`, the server uses `"wkr:" + worker_id`, for compatibility with old clients; every registration still gets its own `worker_id` and token, and the response is unchanged.
- `owner` is optional; after trimming whitespace it must be 1–40 characters. An empty string, whitespace only, omitted or `null` all count as not provided; too long returns 422.
- No new event type for `worker_joined`; when an owner is given the message includes it, e.g. `Browser-1658 joined (browser, 1 slot, owner hyang2)`.

### 6.2 `POST /api/workers/{id}/heartbeat`

Request `{}`; response `{"ok": true, "paused": false, "server_time": "...Z"}`. Updates `last_heartbeat`; if `offline_notified=1`, resets it to 0 and records `worker_back`.

### 6.3 `POST /api/workers/{id}/lease`

Request `{}`.

- Runs `reap(now)` first.
- If the worker is paused or left, already holds ≥ `slots` leases, or there is no matching task → **204** (no body).
- Otherwise 200:

```json
{"task_id": 42, "job_id": "job_1a2b3c", "task_type": "mandelbrot_v1",
 "lease_token": "<opaque>", "lease_expires_at": "2026-11-07T17:20:45.123Z",
 "input": { ...the input from 4.2... }}
```

Leasing must be atomic (conditional UPDATE, see §7). On every lease: `attempts += 1` and record `task_leased`; if it is the job's first lease, the job becomes `running`, `started_at` is written, and `job_started` is recorded.

### 6.4 `POST /api/tasks/{task_id}/complete`

```json
// request
{"worker_id": "wkr_1a2b3c4d", "lease_token": "<opaque>", "result": [0, 3, 8000, ...], "runtime_ms": 812}
// 200
{"accepted": true}
```

- Accepted only if `status='leased' AND lease_token` matches; otherwise **409** `{"detail": "stale_lease"}`. This covers duplicate submissions and late results from an old lease.
- Result validation failure → **422**; the task goes back to pending (or becomes failed once attempts are exhausted).
- On acceptance: the task becomes `done`, `done_by`, `runtime_ms` and `done_at` are written, and `done_seq` = the id of this `task_done` event; **in the same transaction** the integer `credit_iters = sum(result)` is written and `workers.tasks_done += 1`; if every task of the job is done → the job becomes `completed`, `finished_at` is written, and `job_completed` is recorded.
- Rejected results (422), expired / reassigned leases (409), and duplicate or other workers' submissions earn no credit. A reassigned tile is credited only to the `done_by` whose final result was accepted. The rule for accepting results whose lease has expired but has not yet been reaped is in §7; credit always follows the result that was actually accepted.

### 6.5 `POST /api/tasks/{task_id}/fail`

Request `{"worker_id": "...", "lease_token": "...", "error": "string ≤ 500"}` → 200 `{"ok": true}` (token mismatch → 409). The task goes back to pending, or becomes failed when `attempts ≥ MAX`. Records `task_error`.

### 6.6 `POST /api/workers/{id}/pause` | `/resume` | `/disconnect`

Request `{}` → `{"ok": true}`.

- pause: every later lease returns 204; **tasks already running can still be submitted normally**. Records `worker_paused`.
- resume: records `worker_resumed`.
- disconnect: `disconnected=1`; the worker's leased tasks immediately go back to pending (`attempts` is not rolled back), with one `lease_released` event per task; then `worker_left` is recorded.

### 6.7 `POST /api/jobs`

```json
// request
{"task_type": "mandelbrot_v1", "params": { ...JobParams... }}
// 200
{"job_id": "job_1a2b3c", "status": "queued", "task_count": 192}
```

422 for invalid parameters; 429 when active jobs (queued + running) ≥ `TG_MAX_ACTIVE_JOBS`. The job and all its tasks are inserted **in the same transaction**. Records `job_created`.

### 6.8 `GET /api/jobs`

The 20 most recent jobs, newest first:

```json
{"jobs": [{"job_id": "job_1a2b3c", "status": "running", "task_count": 192,
           "done_count": 57, "created_at": "...Z", "params": {...}}]}
```

### 6.9 `GET /api/jobs/{job_id}?since=0`

Runs `reap(now)` first. `tiles` returns only completed tiles with `done_seq > since` (incremental); `leased` always returns every currently leased task.

```json
{"job_id": "job_1a2b3c", "task_type": "mandelbrot_v1", "params": {...}, "status": "running",
 "task_count": 192,
 "counts": {"pending": 100, "leased": 3, "done": 89, "failed": 0},
 "created_at": "...Z", "started_at": "...Z", "finished_at": null,
 "elapsed_ms": 15230,
 "workers": [{"worker_id": "wkr_..", "name": "Native-Harry", "worker_type": "native", "tiles_done": 40}],
 "tiles": [{"task_id": 42, "idx": 41, "tile_x": 640, "tile_y": 128, "tile_w": 64, "tile_h": 64,
            "worker_id": "wkr_..", "worker_type": "browser", "runtime_ms": 812, "attempts": 2,
            "data": [0, 3, 350, ...]}],
 "leased": [{"task_id": 43, "tile_x": 704, "tile_y": 128, "tile_w": 64, "tile_h": 64,
             "worker_id": "wkr_..", "lease_expires_at": "...Z"}],
 "next_since": 1234}
```

`elapsed_ms`: once the job has finished = `finished_at - created_at`; while unfinished = `now - created_at`. Unknown job → 404.

### 6.10 `GET /api/stats`

Runs `reap(now)` first.

```json
{"server_time": "...Z",
 "totals": {"online": 3, "paused": 0, "offline": 1, "left": 0,
            "active_jobs": 1, "tasks_done": 812, "slots_online": 4},
 "workers": [{"worker_id": "wkr_..", "name": "Browser-Harry", "worker_type": "browser",
              "status": "online", "slots": 1, "has_gpu": false, "tasks_done": 55,
              "last_heartbeat_age_s": 1.2, "current_task_ids": [43],
              "registered_at": "...Z"}]}
```

`workers` includes every worker (including those that left), ordered online → paused → offline → left.

### 6.11 `GET /api/events?since=0&limit=100&job_id=`

- `since=0`: returns the **latest** `limit` events, in ascending id order;
- `since>0`: returns events with `id > since`, ascending, at most `limit`;
- `limit` ≤ 500; `job_id` is an optional filter.

```json
{"events": [{"id": 101, "ts": "...Z", "type": "lease_expired", "job_id": "job_..",
             "task_id": 26, "worker_id": "wkr_..", "worker_name": "Browser-01",
             "message": "Task 26 lease expired (Browser-01) -> requeued"}],
 "next_since": 101}
```

### 6.12 `GET /api/presets`

Returns the contents of `backend/presets.json` (an array); if the file is missing, returns only the built-in `quick`.

### 6.13 `POST /api/planner` (implemented by D, exposed as `router` in `planner.py`)

```json
// request
{"prompt": "Render a detailed Mandelbrot ... keep each browser worker light."}   // ≤ 500 characters
// 200 (does not create a job; only fills in the frontend form)
{"task_type": "mandelbrot_v1", "params": { ...passes JobParams validation... },
 "explanation": "...", "source": "llm"}
```

503 `{"detail": "planner_unavailable"}`: no key, timeout (15 s) or API error.
422 `{"detail": "planner_invalid_output"}`: the LLM output failed JobParams validation twice in a row.
A mounts it in `main.py`: `try: from planner import router as planner_router; app.include_router(planner_router)` / `except ImportError: pass`.

### 6.14 `GET /healthz` → `{"ok": true}`

### 6.15 `GET /api/contributions?since=&until=`

`since` and `until` are both optional and must be ISO 8601 datetimes with `Z` or a UTC offset, e.g. `2026-11-07T12:00:00Z` or `2026-11-07T08:00:00-04:00`. Filtering is `tasks.done_at >= since` and `tasks.done_at <= until`, **inclusive**, compared in UTC. No timezone, an invalid time, or `since > until` returns 422; omitting one leaves that bound open.

```json
{"generated_at": "2026-11-07T17:20:45.123Z", "unit": "Miter",
 "basis": "sum of per-pixel iterations in accepted, spot-checked results",
 "totals": {"devices": 3, "tiles_done": 1200, "verified_miter": 152340.5},
 "devices": [
   {"device_id": "dev_3f9a1c2b7e4d4a10", "display_name": "Native-LAPTOP-YANGZIHA", "owner": "hyang2",
    "worker_types": ["native"], "workers": 3,
    "tiles_done": 900, "verified_miter": 120000.1, "share": 0.7877,
    "compute_ms": 210000, "online_s": 5400.0,
    "first_seen": "2026-11-07T15:00:00.000Z", "last_seen": "2026-11-07T17:20:42.000Z"}
 ]}
```

- Registration sessions are merged by `COALESCE(workers.device_id, 'wkr:' || workers.id)`; only `tasks.status='done'` is summed, joined on `tasks.done_by = workers.id`. Every device that ever registered is included, keeping `left` / `offline` and zero-credit devices; `totals.devices` counts these device IDs and is not guaranteed to equal the number of physical machines.
- `display_name` and `owner` use the values from the device's **most recent registration**; on a time tie, the later-inserted registration wins. If the most recent registration has no owner, the output is `null`; it does not inherit an older value.
- `worker_types` is the deduplicated, sorted list of types across the whole registration history; `workers` is the number of registrations, each session counted once, never duplicated through the tile join.
- `tiles_done` is the number of accepted tiles in the filtered range. Integer iteration counts are summed exactly first, then divided by 1,000,000 to give `verified_miter`. Totals work the same way; never round per tile first.
- `share = round(this device's iterations / all devices' iterations, 4)`; when total credit is 0 every share is 0. Sorted by credit descending, ties by `device_id` ascending; rounded shares may not sum to exactly 1.
- `compute_ms` is the sum of `runtime_ms` of accepted results in range. `online_s` is the sum of `last_heartbeat - registered_at` over registration sessions, each counted once; it is not deduplicated wall-clock time. Both are informational only and do not affect credit.
- `first_seen = min(registered_at)`, `last_seen = max(last_heartbeat)`. The time filter only affects `tiles_done`, `verified_miter`, `share`, `compute_ms` and the matching totals; registration metadata and `online_s` always cover the full history.
- With no devices, `devices=[]` and all three total fields are 0.

### 6.16 `GET /api/contributions.csv?since=&until=`

Same filtering, validation and ordering as §6.15; UTF-8 CSV with one row per device (no extra totals row); apart from the spreadsheet formula escaping below, values match the JSON for the same range. The header is fixed:

```text
device_id,display_name,owner,worker_types,workers,tiles_done,verified_miter,share,compute_ms,online_s,first_seen,last_seen
```

`worker_types` is `;`-separated, a null owner is exported as an empty field; commas, quotes and newlines use standard CSV quoting. Response header `Content-Disposition: attachment; filename="tartangrid_contributions.csv"`.

If `display_name` / `owner`, after stripping leading whitespace, starts with `= + - @`, or the original value starts with tab / CR / LF, the CSV prefixes the original value with a single quote so spreadsheet software does not execute it as a formula. JSON and the database keep the original value; count fields stay numeric. The mock export follows the same rule.

---

## 7. Scheduling semantics (implemented by A; everyone else uses this to understand the behavior)

- **Global lock**: every DB read and write happens inside one `threading.Lock`; endpoints use plain `def` (not `async def`).
- **Atomic lease** (conditional UPDATE; SQLite ≥ 3.35 supports `RETURNING`):

```sql
UPDATE tasks SET status='leased', worker_id=:w, lease_token=:tok,
       lease_expires=:now + :ttl, attempts=attempts+1
WHERE id = (SELECT t.id FROM tasks t JOIN jobs j ON j.id = t.job_id
            WHERE t.status='pending' AND j.status IN ('queued','running')
              AND t.task_type IN (<the worker's task_types>)
            ORDER BY j.created_at, t.idx LIMIT 1)
  AND status='pending'
RETURNING id, job_id, input_json, attempts;
```

- **complete**: `UPDATE ... SET status='done' ... WHERE id=:id AND status='leased' AND lease_token=:tok`; rowcount = 0 → 409.
- **reap(now)** (lazy, called at the start of lease, `GET /api/stats` and `GET /api/jobs/{id}`; no background thread):
  1. Tasks with `status='leased' AND lease_expires < now`: if `attempts < MAX` → pending, record `lease_expired`; otherwise → failed, record `task_failed`. Both cases clear `worker_id` and `lease_token`.
  2. A job with a failed task → status becomes `failed`, `finished_at` is written, `job_failed` is recorded.
  3. Newly offline workers (`offline_notified=0`, not disconnected, heartbeat timed out) → `offline_notified=1`, record `worker_offline`.
- Exhausted attempts: a task with `status='pending' AND attempts >= MAX` (e.g. its worker disconnected during the 3rd lease) is marked `failed` at the next reap and records `task_failed`; it is never leased a 4th time.
- Late results: if the lease has expired but has **not yet been reaped** and the token still matches → accepted (the result is valid). Already reaped or re-leased → 409.
- Scheduling order: FIFO (job creation time first, then idx). Fair scheduling is P2.

---

## 8. Event types (`events.type`)

`worker_joined` `worker_paused` `worker_resumed` `worker_left` `worker_offline` `worker_back`
`job_created` `job_started` `job_completed` `job_failed`
`task_leased` `task_done` `task_rejected` `task_error` `lease_expired` `lease_released` `task_failed`

`message` is a short human-readable English sentence, e.g. `"Task 26 leased to Native-Harry (attempt 2)"`. "Reassigned" has no event type of its own: a re-lease is simply a new `task_leased`; when `attempts > 1` the message includes `(attempt N)`, and the frontend uses that to highlight it as reassigned.

---

## 9. Browser contributor interface (implemented by C, called by B's `contribute.html`)

`static/contributor.js` defines the global class `TartanContributor`:

```js
const c = new TartanContributor({ name: "Browser-Harry", slots: 1, owner: "hyang2" });
c.on("status", s => {});
//  s = { state: "idle"|"connecting"|"running"|"paused"|"stopped"|"error",
//        workerId, slots, tasksDone, currentTaskIds: [..], lastRuntimeMs, lastError }
c.on("task_done", e => {});   // e = { taskId, jobId, runtimeMs }
c.on("log", msg => {});       // msg: string
await c.start();    // register → start heartbeat → start `slots` Web Workers
await c.pause();    // POST /pause + tell every worker to stop leasing after its current task
await c.resume();   // POST /resume + resume leasing
await c.stop();     // POST /disconnect + terminate every Web Worker
```

Whether to auto-pause when the page is hidden is up to B's page (checked by default; calls `c.pause()` / `c.resume()`).

The browser generates `"dev_" + 16 lowercase hex characters` with `crypto.getRandomValues` and stores it in localStorage as `tg_device_id`, reused across reloads and re-registrations. If localStorage is unavailable, it uses a random ID for the current session; hardware fingerprints must not be collected. `owner` is an optional constructor parameter sent with device_id at registration; the page is responsible for saving the optional owner safely with try/catch and explaining what it is for.

The native worker stores its random device_id in `~/.tartangrid/device_id` (Windows: `%USERPROFILE%\.tartangrid\device_id`), reused across restarts; `--owner` supplies the optional owner. A browser and a native worker on the same machine use separate storage, so they usually show up as two device_ids; they can be matched by hand through the same owner, but this cannot prove identity or the number of physical devices.

`static/mandelbrot_kernel.js` defines the global function `computeTile(input) -> Int32Array` (input as in §4.2), and under Node does `module.exports = { computeTile }`. B's job page "single-machine baseline" button also uses it.

---

## 10. Shared frontend conventions (B implements `common.js`)

`common.js` defines the global `TG`:

- `TG.api(path, {method, body})` → parsed JSON, `null` on 204; on non-2xx it throws an `Error` carrying `status` and `detail`.
- `TG.workerColor(workerId)` → a stable HSL color string (hash of the id).
- `TG.colorize(n, maxIter)` → `[r, g, b]`; `n == maxIter` is black.
- `TG.fmtAgo(seconds)`, `TG.fmtTime(iso)`.

Visual theme: CMU red `#C41230` as the accent color; Browser and Native nodes get different badges.

---

## 11. Milestones (assuming an 11:00 start; shift everything if you start late)

| Time | Milestone | Owner |
|---|---|---|
| 11:20 | Contract frozen, everyone has cloned, `main` has the empty directory skeleton | A |
| 11:30 | Cross-device networking verified (the tunnel URL opens from another device) | D |
| 11:40 | **Stub API**: every endpoint returns the right response shape; lease returns a real, computable input | A |
| 11:40 | `compute_pixel` committed in `mandelbrot.py` (A's validation needs it) | C |
| 12:15 | **M1**: a native worker runs a whole `quick` job against the real backend (no UI needed) | A + C |
| 13:00 | **M2**: browser and native run together; the job page canvas fills in tile by tile | B + C |
| 14:00 | **M3**: the job still completes after killing a node; the smoke test passes. **Hard cut-off: if not reached, drop AI/GPU work** | Everyone |
| 15:00 | **M4**: New Job form, Pause, Dashboard, event stream, measured timings | B + A |
| 16:00 | **M5**: AI Planner (or a working fallback), demo parameters finalized | D |
| 16:30 | Feature freeze; 2 rehearsals; record a backup video | Everyone |

---

## 12. Git conventions

- Branches: `a/backend`, `b/frontend`, `c/workers`, `d/integration`. Open a PR to `main` at each milestone and merge in small steps.
- Only edit your own files; merge conflicts are resolved by the file owner.
- Run your own tests before committing.

---

## 13. Global acceptance (Definition of Done)

1. At least 2 real browser nodes and 1 real native node online at the same time; the Dashboard shows their real status.
2. One job completed jointly by at least two kinds of real nodes and assembled into one complete image ("Color by worker" shows who computed each tile).
3. Close a browser tab (or crash a native worker with `--crash-after`) → the tiles it held are completed by other nodes after the lease expires.
4. After Pause, no new tasks are leased.
5. A stale lease or a duplicate complete returns 409 and is never counted twice.
6. The AI Planner produces a configuration that passes validation; when it is unavailable, the form still works.
7. The reported $T_{\text{single}}$, $T_{\text{distributed}}$ and speedup are all measured values.
