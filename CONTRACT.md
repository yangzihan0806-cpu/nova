# TartanGrid 工程契约（CONTRACT v1.2）

> v1.2（2026-11-07）：按随机 `device_id` 汇总接受结果的迭代积分；新增选填 `owner`、无损 schema 迁移、贡献 JSON / CSV（§5、§6.1、§6.4、§6.15–16、§9）；`TG_SPOT_CHECKS` 默认改为 8。按团队 TODO 的已定增量更新，既有 lease / retry 语义不变。

> v1.1（2026-11-07）：AI Planner 在没有 `ANTHROPIC_API_KEY` 时改用主办方发的 OpenRouter key（§1、§3）；明确"用完次数的 pending task 在下次 reap 时判 failed"（§7）。其余不变。

> 四人共享、**冻结**。任何人（包括你的 agent）不得单方面修改本契约或别人的文件。
> 发现契约有问题：在群里提出，全员同意后由 D 升版本号（v1.2…）并通知所有人。
> 本文件放在 repo 根目录 `CONTRACT.md`。

---

## 0. 不可违反的原则

1. 只运行 allowlisted 模板 `mandelbrot_v1`。**不接受、不执行**用户提交的任何代码、命令、URL。
2. 时间以**服务端**为准。DB 存 unix 秒（`REAL`）；API 输出 ISO 8601 UTC，毫秒精度，`Z` 结尾，例如 `2026-11-07T17:20:45.123Z`。
3. 不伪造数据：Dashboard 上每个数字都来自 API；未接入的东西（GPU 池、CMU HPC）只能标 `Planned / Not connected`。
4. 同一个物理设备上的多个 slot 是**一个** worker，不能伪装成多台电脑。
5. 不把 API key、token、`.db` 文件提交进 git（`.gitignore` 包含 `*.db`、`.env`）。
6. 只记录积分，不提供金额、代币、兑换功能；最终奖励由团队另行决定。`device_id` 随机生成，不能使用硬件指纹；`owner` 自愿填写，用于团队关联贡献记录，不是身份认证。

---

## 1. 技术栈与运行方式

| 部分 | 选型 |
|---|---|
| 后端 | Python ≥ 3.10，FastAPI，uvicorn，Pydantic v2，stdlib `sqlite3`，numpy |
| 前端 | **原生 HTML + CSS + JS**，无框架、无构建步骤、不依赖 CDN；由 FastAPI 静态托管 |
| 浏览器 Worker | Dedicated Web Worker（`static/worker.js`） |
| Native Worker | Python + requests + numba（可选；缺失时 fallback 到 numpy） |
| 公网访问 | `cloudflared tunnel --url http://localhost:8000`，所有设备用它给出的 `https://*.trycloudflare.com` |
| AI Planner | 优先 Anthropic API（`ANTHROPIC_API_KEY`）；没有则用 OpenRouter（`OPENROUTER_API_KEY`，从仓库根目录 `.env` 读取）。都不可用时返回 503，前端降级到表单 |

启动：

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
# 另一个终端
cloudflared tunnel --url http://localhost:8000
# 贡献者机器
python workers/native_worker.py --server https://xxxx.trycloudflare.com --name Native-Harry --slots 2
```

前端页面（全部由 FastAPI 在 `/` 下托管 `backend/static/`）：

| URL | 文件 | 用途 |
|---|---|---|
| `/` | `index.html` | Dashboard |
| `/new.html` | `new.html` | 新建 Job（表单 + AI Planner） |
| `/job.html?id=job_xxxxxx` | `job.html` | Job 详情 + Canvas |
| `/contribute.html` | `contribute.html` | 浏览器贡献算力 |
| `/contributions.html` | `contributions.html` | 设备贡献排名、时间筛选和 CSV 导出 |

前端所有请求都用**相对路径** `/api/...`（同源，无 CORS）。

---

## 2. 目录与文件所有权

```text
nova/
├─ CONTRACT.md                     # 全员（D 维护版本）
├─ README.md                       # D
├─ .gitignore                      # A
├─ backend/
│  ├─ requirements.txt             # A（D 可追加 anthropic）
│  ├─ main.py                      # A  FastAPI app、路由、静态托管
│  ├─ db.py                        # A  SQLite 连接、schema、全局锁
│  ├─ schemas.py                   # A  Pydantic 模型（JobParams 等）
│  ├─ scheduler.py                 # A  拆分、lease、complete、reap、事件
│  ├─ mandelbrot.py                # C  计算 kernel（A 的验证、D 的测试会 import）
│  ├─ planner.py                   # D  AI Planner，暴露 `router`
│  ├─ presets.json                 # D  预设参数
│  └─ static/
│     ├─ index.html  new.html  job.html  contribute.html  contributions.html   # B
│     ├─ common.js  style.css      # B  共享 JS 工具与样式
│     ├─ mandelbrot_kernel.js      # C  JS kernel
│     ├─ worker.js                 # C  Web Worker 执行循环
│     └─ contributor.js            # C  浏览器贡献者控制器（B 的页面调用）
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

**只改自己的文件。** 要改别人的文件 → 找文件主人。

---

## 3. 配置常量（环境变量可覆盖）

| 环境变量 | 默认 | 含义 |
|---|---:|---|
| `TG_DB_PATH` | `backend/tartangrid.db` | SQLite 路径 |
| `TG_RESET_DB` | `0` | `1` = 启动时清空数据库 |
| `TG_LEASE_TTL_S` | `10` | task lease 有效期（秒） |
| `TG_HEARTBEAT_S` | `3` | worker 心跳间隔（register 时下发给 worker） |
| `TG_OFFLINE_AFTER_S` | `12` | 超过此秒数无心跳视为 offline |
| `TG_MAX_ATTEMPTS` | `3` | 一个 task 最多被 lease 的次数 |
| `TG_MAX_ACTIVE_JOBS` | `2` | 同时处于 queued/running 的 job 上限 |
| `TG_SPOT_CHECKS` | `8` | 每个结果服务端随机重算的像素数 |
| `TG_PLANNER_MODEL` | `claude-haiku-5-5` | Planner 使用的模型 |
| `ANTHROPIC_API_KEY` | — | 有则 Planner 用 Anthropic |
| `OPENROUTER_API_KEY` | — | 没有 Anthropic key 时 Planner 用 OpenRouter（可写在仓库根目录 `.env`） |
| `OPENROUTER_MODEL` | `openai/gpt-4o` | OpenRouter 模型名 |

---

## 4. 任务模板 `mandelbrot_v1`

### 4.1 JobParams（服务端用 Pydantic 强制，`extra="forbid"`）

| 字段 | 类型 | 约束 |
|---|---|---|
| `width` | int | 64 ≤ w ≤ 4096，且是 `tile_size` 的整数倍 |
| `height` | int | 64 ≤ h ≤ 4096，且是 `tile_size` 的整数倍 |
| `tile_size` | int | ∈ {32, 64, 128} |
| `max_iter` | int | 1 ≤ m ≤ 20000 |
| `x_min`, `x_max`, `y_min`, `y_max` | float | 有限值，绝对值 ≤ 4，`x_min < x_max`，`y_min < y_max` |

派生：`task_count = (width / tile_size) * (height / tile_size)`，必须 ≤ 4096。违反任一条 → 422。

### 4.2 拆分规则

```
cols = width / tile_size,  rows = height / tile_size
对 ty_i in 0..rows-1, tx_i in 0..cols-1:
    idx    = ty_i * cols + tx_i
    tile_x = tx_i * tile_size,  tile_y = ty_i * tile_size
    tile_w = tile_h = tile_size
```

每个 task 的 `input`（JSON 对象，字段固定）：

```json
{"tile_x": 640, "tile_y": 128, "tile_w": 64, "tile_h": 64,
 "image_width": 1024, "image_height": 768, "max_iter": 350,
 "x_min": -2.2, "x_max": 1.0, "y_min": -1.2, "y_max": 1.2}
```

### 4.3 参考 kernel（权威定义，所有实现必须与之**逐位一致**）

```python
def compute_pixel(px, py, p):          # px, py 是全图像素坐标；py = 0 是最上面一行
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

实现规则：IEEE double；**不得**改写表达式（例如不能写成 `x_min + px*dx + 0.5*dx`），不开 fastmath，不用 FMA。
已验证：按此写法，标量 Python、numpy 向量化、numba `@njit` 三者结果逐位相同。JS（V8）同样是 IEEE double、不做 FMA 收缩，应当一致，C 必须用测试确认。

### 4.4 结果格式

`result`：JSON 整数数组，长度 `tile_w * tile_h`，tile 内 row-major（`index = j * tile_w + i`，`j` 从 tile 顶行开始），每个值 ∈ `[0, max_iter]`。值等于 `max_iter` 表示未逃逸（在集合内）。

### 4.5 服务端结果验证

1. 长度正确、全为整数、范围正确；
2. 随机抽 `TG_SPOT_CHECKS` 个像素，用 `compute_pixel` 重算，必须**完全相等**；
3. 任一失败 → 422，task 重新入队（受 `TG_MAX_ATTEMPTS` 限制），记 `task_rejected` 事件。

抽样重算是概率性检查，不能保证发现所有错误或作弊；“verified” 表示结果通过以上校验，不表示逐像素重算了整块 tile。

### 4.6 预设（`backend/presets.json`，D 负责调优）

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

参考实测（云端测试容器，非你们的设备）：`demo` 区域 `max_iter=5000` 时平均每像素约 3900 次迭代，总计约 $1.2\times10^{10}$ 次；numba 单核约 $3\times10^{8}$ iter/s，numpy 约 $1.3\times10^{8}$ iter/s。真实参数由 D 用真实设备测出。

---

## 5. 数据库 schema（SQLite，A 实现）

```sql
CREATE TABLE IF NOT EXISTS workers (
  id               TEXT PRIMARY KEY,         -- "wkr_" + 8 hex
  name             TEXT NOT NULL,
  worker_type      TEXT NOT NULL,            -- browser | native
  token            TEXT NOT NULL,
  slots            INTEGER NOT NULL DEFAULT 1,
  has_gpu          INTEGER NOT NULL DEFAULT 0,
  task_types       TEXT NOT NULL,            -- JSON 数组，服务端与 allowlist 取交集
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
  done_seq      INTEGER,                          -- = 完成时插入的 task_done 事件 id
  credit_iters  INTEGER                           -- 接受时写入 sum(result)
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

**无损迁移**：`init_db()` 对已有数据库用 `PRAGMA table_info` 检查，逐项补缺列，再创建索引。可重复执行；不能要求清空现场 DB，也不能覆盖已有数据。

```sql
ALTER TABLE workers ADD COLUMN device_id TEXT;
ALTER TABLE workers ADD COLUMN owner TEXT;
ALTER TABLE tasks ADD COLUMN credit_iters INTEGER;
CREATE INDEX IF NOT EXISTS idx_workers_device ON workers(device_id);
```

旧 worker 的 `device_id IS NULL` 在查询时按 `'wkr:' || id` 处理。旧的已完成 task 若 `credit_iters IS NULL`，贡献查询当场从存储的 `result_json` 求和，不改写历史数据。

Worker 的派生状态（API 输出用）：

```
disconnected = 1                          → "left"
now - last_heartbeat > TG_OFFLINE_AFTER_S → "offline"
paused = 1                                → "paused"
否则                                       → "online"
```

---

## 6. HTTP API

通用规则：

- 除 §6.16 的 CSV 响应外，请求/响应都是 JSON。错误格式为 FastAPI 默认的 `{"detail": ...}`。
- **Worker 鉴权**：`/api/workers/{id}/*`（register 除外）和 `/api/tasks/*` 需要 header `Authorization: Bearer <token>`，并且 token 必须属于该 worker → 否则 401。
- 状态码：200 成功；204 无任务；401 鉴权失败；404 不存在；409 lease 过期或重复提交；422 参数/结果不合法；429 active job 过多；503 Planner 不可用。

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

约束：`name` 1–40 字符；`worker_type` ∈ {browser, native}；`slots` 1–8；`client_info` ≤ 200 字符，可省略。`has_gpu` 只做展示，MVP 不据此调度。记事件 `worker_joined`。

- `device_id` 选填，非 null 时必须匹配 `^dev_[a-z0-9]{8,32}$`，否则 422。缺省或显式 `null` 时，服务端使用 `"wkr:" + worker_id`，兼容旧客户端；每次注册仍生成独立的 `worker_id` 和 token，响应不变。
- `owner` 选填，去首尾空白后须为 1–40 字符；空字符串、纯空白、缺省或 `null` 都按未填写处理；超长返回 422。
- `worker_joined` 不新增事件类型；有 owner 时 message 包含 owner，例如 `Browser-1658 joined (browser, 1 slot, owner hyang2)`。

### 6.2 `POST /api/workers/{id}/heartbeat`

request `{}`；response `{"ok": true, "paused": false, "server_time": "...Z"}`。更新 `last_heartbeat`；如果 `offline_notified=1`，把它重置为 0 并记 `worker_back`。

### 6.3 `POST /api/workers/{id}/lease`

request `{}`。

- 先执行 `reap(now)`。
- worker 已 paused 或 left、当前持有 lease 数 ≥ `slots`、或没有匹配的 task → **204**（无 body）。
- 否则 200：

```json
{"task_id": 42, "job_id": "job_1a2b3c", "task_type": "mandelbrot_v1",
 "lease_token": "<opaque>", "lease_expires_at": "2026-11-07T17:20:45.123Z",
 "input": { ...4.2 中的 input... }}
```

领取必须原子化（条件 UPDATE，见 §7）。每次 lease：`attempts += 1`，记 `task_leased`；如果是该 job 的第一次 lease，job 变为 `running`、写入 `started_at`，并记 `job_started`。

### 6.4 `POST /api/tasks/{task_id}/complete`

```json
// request
{"worker_id": "wkr_1a2b3c4d", "lease_token": "<opaque>", "result": [0, 3, 8000, ...], "runtime_ms": 812}
// 200
{"accepted": true}
```

- `status='leased' AND lease_token` 匹配才接受，否则 **409** `{"detail": "stale_lease"}`。这包括重复提交和旧 lease 的迟到结果。
- 结果验证失败 → **422**，task 回到 pending（或在超过次数后变为 failed）。
- 接受时：task 变为 `done`，写入 `done_by`、`runtime_ms`、`done_at`，`done_seq` = 本次 `task_done` 事件 id；**在同一个事务**内写入整数 `credit_iters = sum(result)`，`workers.tasks_done += 1`；如果该 job 的所有 task 都已 done → job 变为 `completed`，写入 `finished_at`，记 `job_completed`。
- 被拒（422）、失效 / 已重派 lease（409）、重复或其他 worker 的提交都不记功。重派 tile 只计给最终结果被接受的 `done_by`。既有“过期但尚未 reap”的接受规则见 §7；积分始终以实际接受结果为准。

### 6.5 `POST /api/tasks/{task_id}/fail`

request `{"worker_id": "...", "lease_token": "...", "error": "string ≤ 500"}` → 200 `{"ok": true}`（token 不匹配 → 409）。task 回到 pending，或在 `attempts ≥ MAX` 时变为 failed。记 `task_error`。

### 6.6 `POST /api/workers/{id}/pause` | `/resume` | `/disconnect`

request `{}` → `{"ok": true}`。

- pause：之后 lease 一律返回 204；**已在执行的 task 可以正常提交**。记 `worker_paused`。
- resume：记 `worker_resumed`。
- disconnect：`disconnected=1`；该 worker 持有的 leased task 立即回到 pending（`attempts` 不回退），每个 task 记一条 `lease_released`；再记 `worker_left`。

### 6.7 `POST /api/jobs`

```json
// request
{"task_type": "mandelbrot_v1", "params": { ...JobParams... }}
// 200
{"job_id": "job_1a2b3c", "status": "queued", "task_count": 192}
```

422 参数非法；429 active job（queued + running）≥ `TG_MAX_ACTIVE_JOBS`。在**同一个事务**里插入 job 和全部 task。记 `job_created`。

### 6.8 `GET /api/jobs`

最近 20 个 job，按创建时间倒序：

```json
{"jobs": [{"job_id": "job_1a2b3c", "status": "running", "task_count": 192,
           "done_count": 57, "created_at": "...Z", "params": {...}}]}
```

### 6.9 `GET /api/jobs/{job_id}?since=0`

先执行 `reap(now)`。`tiles` 只返回 `done_seq > since` 的已完成 tile（增量）；`leased` 总是返回当前全部 leased task。

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

`elapsed_ms`：job 结束时 = `finished_at - created_at`；未结束时 = `now - created_at`。job 不存在 → 404。

### 6.10 `GET /api/stats`

先执行 `reap(now)`。

```json
{"server_time": "...Z",
 "totals": {"online": 3, "paused": 0, "offline": 1, "left": 0,
            "active_jobs": 1, "tasks_done": 812, "slots_online": 4},
 "workers": [{"worker_id": "wkr_..", "name": "Browser-Harry", "worker_type": "browser",
              "status": "online", "slots": 1, "has_gpu": false, "tasks_done": 55,
              "last_heartbeat_age_s": 1.2, "current_task_ids": [43],
              "registered_at": "...Z"}]}
```

`workers` 包含所有 worker（left 的也包含），排序：online → paused → offline → left。

### 6.11 `GET /api/events?since=0&limit=100&job_id=`

- `since=0`：返回**最新的** `limit` 条事件，按 id 升序；
- `since>0`：返回 `id > since` 的事件，升序，最多 `limit` 条；
- `limit` ≤ 500；`job_id` 可选，用于过滤。

```json
{"events": [{"id": 101, "ts": "...Z", "type": "lease_expired", "job_id": "job_..",
             "task_id": 26, "worker_id": "wkr_..", "worker_name": "Browser-01",
             "message": "Task 26 lease expired (Browser-01) -> requeued"}],
 "next_since": 101}
```

### 6.12 `GET /api/presets`

返回 `backend/presets.json` 的内容（一个数组）；文件缺失时只返回内置的 `quick`。

### 6.13 `POST /api/planner`（D 实现，在 `planner.py` 里以 `router` 暴露）

```json
// request
{"prompt": "Render a detailed Mandelbrot ... keep each browser worker light."}   // ≤ 500 字符
// 200（不会创建 job，只给前端填表）
{"task_type": "mandelbrot_v1", "params": { ...通过 JobParams 校验... },
 "explanation": "...", "source": "llm"}
```

503 `{"detail": "planner_unavailable"}`：没有 key、超时（15 s）或 API 出错。
422 `{"detail": "planner_invalid_output"}`：LLM 输出连续 2 次未通过 JobParams 校验。
A 在 `main.py` 中挂载：`try: from planner import router as planner_router; app.include_router(planner_router)` / `except ImportError: pass`。

### 6.14 `GET /healthz` → `{"ok": true}`

### 6.15 `GET /api/contributions?since=&until=`

`since`、`until` 都选填，必须是带 `Z` 或 UTC offset 的 ISO 8601 datetime，例如 `2026-11-07T12:00:00Z` 或 `2026-11-07T08:00:00-04:00`。按 `tasks.done_at >= since`、`tasks.done_at <= until` **包含边界**过滤，统一按 UTC 比较。无时区、无效时间或 `since > until` 返回 422；省略即不限制该边界。

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

- 按 `COALESCE(workers.device_id, 'wkr:' || workers.id)` 合并注册会话；只汇总 `tasks.status='done'`，用 `tasks.done_by = workers.id` 关联。包含所有注册过的设备，`left` / `offline` 和零积分也保留；`totals.devices` 为这些设备 ID 的数量，不保证是不同物理机器。
- `display_name` 和 `owner` 使用该设备**最近一次注册**的值；时间相同则后插入的注册优先。最近注册未填 owner 时输出 `null`，不会继承旧值。
- `worker_types` 为全部注册历史中的去重、排序类型列表；`workers` 为注册次数，各会话只计一次，不能因 tile 关联而重复。
- `tiles_done` 为筛选范围内接受的 tile 数。先精确汇总整数迭代次数，再除以 1,000,000 得到 `verified_miter`。总计同理；不得逐 tile 先四舍五入。
- `share = round(本设备迭代次数 / 所有设备迭代次数, 4)`；总积分为 0 时各 share 为 0。按积分降序排列，同分按 `device_id` 升序；舍入后 share 合计可能略偏离 1。
- `compute_ms` 为范围内接受结果的 `runtime_ms` 之和。`online_s` 为各注册会话的 `last_heartbeat - registered_at` 之和，各会话只计一次；它不是去重墙钟时长。两者只作参考，不参与积分。
- `first_seen = min(registered_at)`，`last_seen = max(last_heartbeat)`。时间筛选只影响 `tiles_done`、`verified_miter`、`share`、`compute_ms` 及对应总计；注册元数据和 `online_s` 保持全部历史口径。
- 无设备时 `devices=[]`，总计的三个字段均为 0。

### 6.16 `GET /api/contributions.csv?since=&until=`

接受与 §6.15 相同的筛选、校验和排序；UTF-8 CSV，每台设备一行（没有额外总计行），除下述表格公式转义外，值与同范围 JSON 一致。表头固定：

```text
device_id,display_name,owner,worker_types,workers,tiles_done,verified_miter,share,compute_ms,online_s,first_seen,last_seen
```

`worker_types` 用 `;` 分隔，null owner 导出为空字段；逗号、引号、换行使用标准 CSV quoting。响应头 `Content-Disposition: attachment; filename="tartangrid_contributions.csv"`。

`display_name` / `owner` 去掉前导空白后若以 `= + - @` 开头，或原值以 tab / CR / LF 开头，CSV 在原值前加一个单引号，防止表格软件按公式执行。JSON 和数据库保留原值；计数字段仍为数字。mock 导出采用同样规则。

---

## 7. 调度语义（A 实现，其他人据此理解行为）

- **全局锁**：所有 DB 读写都在一个 `threading.Lock` 内完成；endpoint 用普通 `def`（不用 `async def`）。
- **原子 lease**（条件 UPDATE，SQLite ≥ 3.35 支持 `RETURNING`）：

```sql
UPDATE tasks SET status='leased', worker_id=:w, lease_token=:tok,
       lease_expires=:now + :ttl, attempts=attempts+1
WHERE id = (SELECT t.id FROM tasks t JOIN jobs j ON j.id = t.job_id
            WHERE t.status='pending' AND j.status IN ('queued','running')
              AND t.task_type IN (<worker 的 task_types>)
            ORDER BY j.created_at, t.idx LIMIT 1)
  AND status='pending'
RETURNING id, job_id, input_json, attempts;
```

- **complete**：`UPDATE ... SET status='done' ... WHERE id=:id AND status='leased' AND lease_token=:tok`，rowcount = 0 → 409。
- **reap(now)**（lazy，在 lease、`GET /api/stats`、`GET /api/jobs/{id}` 开头调用；不开后台线程）：
  1. `status='leased' AND lease_expires < now` 的 task：`attempts < MAX` → pending，记 `lease_expired`；否则 → failed，记 `task_failed`。两种情况都清空 `worker_id` 和 `lease_token`。
  2. 有 task failed 的 job → 状态变为 `failed`，写入 `finished_at`，记 `job_failed`。
  3. 新近 offline 的 worker（`offline_notified=0`、未 disconnect、心跳超时）→ `offline_notified=1`，记 `worker_offline`。
- 用完次数：`status='pending' AND attempts >= MAX` 的 task（例如第 3 次 lease 时 worker disconnect）在下次 reap 时判 `failed`，记 `task_failed`，不会被第 4 次 lease。
- 迟到结果：lease 已过期但还**没被 reap**，token 仍然匹配 → 接受（结果是有效的）。已被 reap 或已重新 lease → 409。
- 调度顺序：FIFO（先按 job 创建时间，再按 idx）。公平调度是 P2。

---

## 8. 事件类型（`events.type`）

`worker_joined` `worker_paused` `worker_resumed` `worker_left` `worker_offline` `worker_back`
`job_created` `job_started` `job_completed` `job_failed`
`task_leased` `task_done` `task_rejected` `task_error` `lease_expired` `lease_released` `task_failed`

`message` 是一句给人看的英文短句，例如 `"Task 26 leased to Native-Harry (attempt 2)"`。"重新分配"不单独设事件类型：重新 lease 就是一条新的 `task_leased`，`attempts > 1` 时 message 里写上 `(attempt N)`，前端据此高亮显示为 reassigned。

---

## 9. 浏览器贡献者接口（C 实现，B 的 `contribute.html` 调用）

`static/contributor.js` 定义全局类 `TartanContributor`：

```js
const c = new TartanContributor({ name: "Browser-Harry", slots: 1, owner: "hyang2" });
c.on("status", s => {});
//  s = { state: "idle"|"connecting"|"running"|"paused"|"stopped"|"error",
//        workerId, slots, tasksDone, currentTaskIds: [..], lastRuntimeMs, lastError }
c.on("task_done", e => {});   // e = { taskId, jobId, runtimeMs }
c.on("log", msg => {});       // msg: string
await c.start();    // register → 启动心跳 → 启动 slots 个 Web Worker
await c.pause();    // POST /pause + 通知所有 worker 在当前 task 完成后停止领取
await c.resume();   // POST /resume + 恢复领取
await c.stop();     // POST /disconnect + terminate 所有 Web Worker
```

页面隐藏时是否自动 pause 由 B 的页面决定（默认勾选，调用 `c.pause()` / `c.resume()`）。

浏览器用 `crypto.getRandomValues` 生成 `"dev_" + 16 位小写 hex`，保存在 localStorage 的 `tg_device_id`，刷新 / 重新注册沿用。localStorage 不可用时使用本次会话随机 ID；不得采集硬件指纹。`owner` 是选填构造参数，注册时连同 device_id 发送；页面负责用 try/catch 安全保存选填 owner 并解释用途。

Native 将随机 device_id 保存在 `~/.tartangrid/device_id`（Windows：`%USERPROFILE%\.tartangrid\device_id`），重启沿用；`--owner` 提供选填 owner。同机浏览器和 native 使用独立存储，因此通常显示为两个 device_id，可以通过相同 owner 人工对应，不能据此证明身份或物理设备数。

`static/mandelbrot_kernel.js` 定义全局函数 `computeTile(input) -> Int32Array`（输入为 §4.2 的 input），并在 Node 下 `module.exports = { computeTile }`。B 的 job 页"单机基线"按钮也会用到它。

---

## 10. 前端共享约定（B 实现 `common.js`）

`common.js` 定义全局 `TG`：

- `TG.api(path, {method, body})` → 解析后的 JSON，204 时返回 `null`；非 2xx 时抛出 `Error`，并带上 `status` 和 `detail`。
- `TG.workerColor(workerId)` → 稳定的 HSL 颜色字符串（对 id 做 hash）。
- `TG.colorize(n, maxIter)` → `[r, g, b]`；`n == maxIter` 为黑色。
- `TG.fmtAgo(seconds)`、`TG.fmtTime(iso)`。

视觉主题：CMU 红 `#C41230` 作为强调色；Browser 和 Native 节点用不同的 badge。

---

## 11. 里程碑（以 11:00 开工计；晚开工就整体平移）

| 时间 | 里程碑 | 负责 |
|---|---|---|
| 11:20 | 契约冻结，所有人 clone，`main` 上有空目录骨架 | A |
| 11:30 | 跨设备网络验证通过（tunnel URL 能从另一台设备打开） | D |
| 11:40 | **Stub API**：所有 endpoint 的响应形状正确，lease 返回真实可计算的 input | A |
| 11:40 | `mandelbrot.py` 中 `compute_pixel` 已提交（A 的验证需要） | C |
| 12:15 | **M1**：native worker 对真实后端完整跑完 `quick` job（不需要 UI） | A + C |
| 13:00 | **M2**：browser 和 native 同时跑，job 页 Canvas 逐块出图 | B + C |
| 14:00 | **M3**：kill 一个节点后 job 仍然完成，smoke test 全过。**硬性停损点：没到就停掉 AI/GPU** | 全员 |
| 15:00 | **M4**：New Job 表单、Pause、Dashboard、事件流、实测耗时 | B + A |
| 16:00 | **M5**：AI Planner（或降级可用）、demo 参数定稿 | D |
| 16:30 | 功能冻结；彩排 2 遍；录备用视频 | 全员 |

---

## 12. Git 约定

- 分支：`a/backend`、`b/frontend`、`c/workers`、`d/integration`。每个里程碑提 PR 到 `main`，小步合并。
- 只改自己的文件；合并冲突由文件主人解决。
- commit 前跑自己的测试。

---

## 13. 全局验收（Definition of Done）

1. 至少 2 个真实浏览器节点和 1 个真实 native 节点同时在线，Dashboard 显示真实状态。
2. 一个 job 由至少两类真实节点共同完成，并拼成一张完整图（"按节点着色"可以看出每块是谁算的）。
3. 关闭一个浏览器标签页（或让 native worker 用 `--crash-after` 崩溃）→ 它手里的 tile 在 lease 过期后被别的节点完成。
4. Pause 后不再领取新任务。
5. 旧 lease 或重复的 complete 返回 409，不会被重复计数。
6. AI Planner 能生成通过校验的配置；不可用时表单照样能用。
7. 报告的 $T_{\text{single}}$、$T_{\text{distributed}}$ 和 speedup 全部是实测值。
