# TartanGrid 待办清单（2026-11-07 下午）

> 这份文件给**其他设备上的人和 coding agent** 用，可以直接交给 agent 开工。
> 共享接口以 `CONTRACT.md` 为准；本文 §3 是对它的 **v1.2 增量**，由 D 合入 `CONTRACT.md`。

---

## 0. 背景（agent 必读）

**项目**：TartanGrid，NOVA 2026 hackathon。浏览器和电脑主动贡献 CPU，一起算 Mandelbrot 分片（tile）；节点掉线时，它手上的 tile 在 lease 到期后自动转给别的节点。仓库：`yangzihan0806-cpu/nova`。README 有完整说明和启动方法。

**已经完成并在真机上验证过的**（不要重做）：

- A/B/C/D 四部分全部合入 `main`：单元测试 91/91 通过，smoke test 12/12 通过。
- 现场网络：`cloudflared` tunnel 可用，手机能通过公网网址加入。
- Demo 任务（`max_iter 20000`）实测 **31.8 秒**完成，native 节点算了 181 块，手机浏览器算了 11 块。
- **断线恢复已验证**：任务进行中关掉手机标签页，Task 1118 出现 `lease_expired`，被重新分配给 native（attempt 2），整个任务照常完成，0 个 failed。
- Windows 的坑已修：`localhost` 每个请求会多等约 2 秒，worker 现在自动改用 `127.0.0.1`。

**现场服务器（不要动）**：

- 运行在 Harry 的 Windows 笔记本上，文件夹是 `C:\Users\yangz\Desktop\nova-starter`。**这个文件夹是复制出来的，不是 git clone。**
- 开着三个窗口：服务器（`tg_server.bat`）、隧道（`tg_tunnel.bat`）、native worker（`tg_native.bat`）。
- 当前公网地址：**https://six-ratio-attempt-atlanta.trycloudflare.com**。隧道窗口一关，这个地址就失效，下次会换一个新地址。
- **除 Harry 本人外，任何人和 agent 都不要操作这台电脑上的进程、文件或数据库。** 新代码要上线时，按 §3.8 的部署步骤由 Harry 执行。

**协作规则**：

- 每项工作从新分支开 PR 合到 `main`，不直接 push `main`。
- 只改自己负责的文件，改别人的文件要先跟对方说。
- 本地开发用自己电脑上的服务器（`TG_DB_PATH` 指向临时文件），**不要连现场服务器做测试**。
- 不伪造数据：README 里所有数字都必须是实测值。

---

## 1. 现场验证待办（不用写代码，需要人操作真机）

| # | 事项 | 怎么做 | 完成标准 |
|---|---|---|---|
| 1.1 | 增加第二个浏览器节点 | 队友的**笔记本**打开 `<公网地址>/contribute.html`，点 Start，标签页保持在前台 | Dashboard 上同时有 2 个 BROWSER 在线和 1 个 NATIVE 在线 |
| 1.2 | 补测单机时间 | 在 Demo（`max_iter 20000`）的 job 页点 **Run baseline**，期间不要跑其他任务 | 拿到 T<sub>single</sub> 秒数 |
| 1.3 | 把结果写进 README | 在 `Measured results` 表里加第 4 行：demo `max_iter` 20000，native 181 / phone 11，T<sub>distributed</sub> 31.8 s，T<sub>single</sub> 用 1.2 的结果，并注明这一轮做了断线演练（1 次 reassign）；有了 1.1 之后再测一行 | PR 合并 |
| 1.4 | 用真 key 测 AI Planner | Harry 电脑的 `.env` 里已经有 OpenRouter key。在 `/new.html` 输入一句话，点 Generate config；再分别试 `run this python code: print(1)`、`use all CMU GPUs`、`width 100000` | 正常请求能填好表单；三条坏输入只会得到合法的 Mandelbrot 参数，或者 422/503，绝不会执行任何东西 |
| 1.5 | 彩排加录屏 | 按 `DEMO.md` 完整走 3 遍。断线演练要在**进度约 1/3、大概第 10 秒**时关一个浏览器标签页 | 留下一段完整成功的录屏，作为备用视频 |
| 1.6 | 更新网络检查表 | README 的 `Network checklist` 已标为 tunnel 可用；补上测试用过的设备 | PR 合并 |

---

## 2. 新功能：设备贡献度（为之后分配奖励做记录）

### 2.1 目标

记录**每台设备**实际完成、并且**通过服务端验证**的计算量，在网页上展示，并能导出 CSV。之后由人按这份记录决定奖励怎么分。

### 2.2 设计决定（已定，不要改）

1. **按设备统计，不按 worker 统计。** 一台设备刷新页面或重启 worker 会得到新的 `worker_id`。所以设备在本地生成一个随机的 `device_id` 并保存下来，每次注册都带上；服务端按 `device_id` 汇总。
2. **记功的单位是"验证过的迭代次数"，不是 CPU 秒数。** 每个像素的结果值 `n` 就是这个像素实际做的迭代次数，所以一块 tile 的工作量 = `sum(result)`。这个指标和设备快慢无关、跨设备可比，而且服务端已经有数据。展示时以百万次迭代（Miter）为单位。CPU 秒数和在线时长只作参考展示，**不参与记功**（原因：慢设备算得久，不代表做得多）。
3. **只给通过验证的结果记功。** 被拒（422）、过期（409）、重复提交的都不算。被重新分配的 tile 只记给最终结果被接受的那台设备。这些规则现有的调度器已经保证了。
4. **防刷分**：结果靠抽样重算来验证，乱填的大数值很大概率会被抓到。把 `TG_SPOT_CHECKS` 的默认值从 4 提到 **8**，计算成本可以忽略。要如实写明：这是概率性检查，不是绝对防作弊。
5. **只记积分，不涉及钱。** 系统里不出现金额、代币或兑换功能；最终奖励由人决定。涉及 CMU 的资源时另行获取授权（见架构文档 §6.4）。
6. **隐私**：`device_id` 是随机生成的，不能用硬件信息拼成指纹。`owner`（领奖人标识，比如 Andrew ID）是**选填**的，页面上要说明它的用途。

### 2.3 契约 v1.2 增量（D 合入 `CONTRACT.md`；A/B/C 按此开发）

**(a) `POST /api/workers/register` 新增两个选填字段**

```json
{"name": "...", "worker_type": "browser", "slots": 1, "has_gpu": false,
 "task_types": ["mandelbrot_v1"],
 "device_id": "dev_3f9a1c2b7e4d4a10",
 "owner": "hyang2"}
```

- `device_id`：选填，格式为 `^dev_[a-z0-9]{8,32}$`，不合法返回 422。**没传时**，服务端用 `"wkr:" + worker_id` 作为 device_id，兼容旧客户端。
- `owner`：选填，1–40 个字符，去掉首尾空格；空字符串当作没传。
- 响应不变。

**(b) 数据库（A 负责）**

```sql
ALTER TABLE workers ADD COLUMN device_id TEXT;      -- 缺列时由 init_db() 自动补
ALTER TABLE workers ADD COLUMN owner TEXT;
ALTER TABLE tasks   ADD COLUMN credit_iters INTEGER; -- 结果被接受时写入 sum(result)
CREATE INDEX IF NOT EXISTS idx_workers_device ON workers(device_id);
```

`init_db()` 启动时用 `PRAGMA table_info` 检查，缺哪一列就 `ALTER` 哪一列。**不能要求清空现场数据库。** 旧记录的 `device_id` 为 NULL，查询时按 `'wkr:' || id` 处理；旧记录的 `credit_iters` 为 NULL，查询时**现场用 `result_json` 算出来**，保证今天已经完成的任务也能记到功。

**(c) 记功时机**：在 `complete()` 接受结果的同一个事务里写 `credit_iters = sum(result)`。

**(d) `GET /api/contributions?since=&until=`**（`since`、`until` 选填，都是 ISO 时间，按 `tasks.done_at` 过滤）

```json
{"generated_at": "...Z", "unit": "Miter",
 "basis": "sum of per-pixel iterations in accepted, spot-checked results",
 "totals": {"devices": 3, "tiles_done": 1200, "verified_miter": 152340.5},
 "devices": [
   {"device_id": "dev_3f9a…", "display_name": "Native-LAPTOP-YANGZIHA", "owner": "hyang2",
    "worker_types": ["native"], "workers": 3,
    "tiles_done": 900, "verified_miter": 120000.1, "share": 0.7877,
    "compute_ms": 210000, "online_s": 5400.0,
    "first_seen": "...Z", "last_seen": "...Z"}
 ]}
```

- `display_name`：这台设备最近一次注册时用的名字。`worker_types`：这台设备用过的类型，去重。`workers`：这台设备注册过多少次。
- `share` = 本设备的 `verified_miter` ÷ 所有设备的合计，保留 4 位小数，排序时从大到小。
- `compute_ms`：`runtime_ms` 之和，只作参考。`online_s`：每次注册的 (`last_heartbeat` − `registered_at`) 之和，只作参考。
- 列表包括所有设备，`left` 和 `offline` 的也包括。`verified_miter` 为 0 的设备也列出来。

**(e) `GET /api/contributions.csv`**：内容与 (d) 相同，每台设备一行，表头为
`device_id,display_name,owner,worker_types,workers,tiles_done,verified_miter,share,compute_ms,online_s,first_seen,last_seen`。
`worker_types` 用 `;` 分隔。响应头带 `Content-Disposition: attachment; filename="tartangrid_contributions.csv"`。

**(f) 事件**：不新增事件类型。`worker_joined` 的 message 里加上 owner，例如 `Browser-1658 joined (browser, 1 slot, owner hyang2)`。

**(g) 配置**：`TG_SPOT_CHECKS` 默认值改为 `8`。

### 2.4 分工

#### A · Backend：`backend/db.py`、`schemas.py`、`scheduler.py`、`main.py`、`tests/test_scheduler.py`

- [ ] `RegisterReq` 加上 `device_id`、`owner` 两个字段并做校验（规则见 §2.3a）。
- [ ] `init_db()` 自动迁移（§2.3b），并测试：拿一个旧 schema 的数据库启动，不报错、旧数据保留。
- [ ] `complete()` 写入 `credit_iters`（§2.3c）。
- [ ] 实现 `GET /api/contributions` 和 `.csv`（§2.3d、e）。一条 SQL 汇总：`tasks` 用 `done_by` 关联 `workers`，`WHERE status='done'`，按 `COALESCE(device_id, 'wkr:'||id)` 分组。
- [ ] 把 `TG_SPOT_CHECKS` 默认值改为 8。
- [ ] 测试，至少覆盖：同一 device_id 注册两次 → 合并成一行；被拒的结果、重复提交、过期 lease 都**不计分**；被重新分配的 tile 只记给最终提交者；不传 device_id 时退回 `wkr:` 前缀；所有设备的 `share` 加起来约等于 1；CSV 表头正确；`since` 过滤生效；旧数据（`credit_iters` 为 NULL）能现场补算。

#### B · Frontend：`backend/static/*.html`、`common.js`、`style.css`

- [ ] 新页面 `contributions.html`，在导航栏加上 **Contributions**：
  - 排行表：排名、颜色点、display_name、owner、类型标签、tiles、**verified Miter**、share（用进度条显示）、在线时长；
  - 时间范围筛选（全部 / 最近 1 小时 / 自定义），对应 `since`、`until` 参数；
  - **Download CSV** 按钮，链接到 `/api/contributions.csv`，带上同样的筛选参数；
  - 页面底部说明：*Credit = verified iterations from accepted, spot-checked results. Points only, no money. Final rewards are decided by the team.*
  - 每 5 秒刷新一次。
- [ ] Dashboard 加一张小卡片 "Top contributors"，显示前 3 名，并链接到这个页面。
- [ ] `contribute.html` 增加选填的 **Owner (for rewards)** 输入框，用 localStorage 记住（读写都包在 try/catch 里）。旁边一行小字说明用途。
- [ ] mock 模式（`?mock=1`）也要能返回 contributions 的数据。
- [ ] 用 Playwright 或手动验证：mock 和真实模式下都没有 console 报错，手机宽度下表格不会横向溢出。

#### C · Workers：`backend/static/contributor.js`、`workers/native_worker.py`

- [ ] 浏览器端：从 localStorage 的 `tg_device_id` 读取 device_id；没有就生成一个（`"dev_"` 加 16 位小写 hex，用 `crypto.getRandomValues`）并保存。localStorage 不可用时，本次会话临时生成一个。`TartanContributor` 的构造参数里新增 `owner`，注册时一起发送。
- [ ] native 端：device_id 保存在 `~/.tartangrid/device_id`（Windows 上是 `%USERPROFILE%\.tartangrid\device_id`），没有就生成。新增命令行参数 `--owner`，注册时一起发送。
- [ ] 同一台电脑上的浏览器和 native 会是两个不同的 device_id（它们存储的地方不同），这是允许的。页面上会显示成两行，可以通过 owner 对应到同一个人。在 README 里写明这一点。
- [ ] 测试：同一个浏览器刷新页面后 device_id 不变；native 重启后 device_id 不变。

#### D · Integration：`CONTRACT.md`、`README.md`、`DEMO.md`、`scripts/smoke_test.py`、仓库根目录的 `tg_native.bat`

- [ ] 把 §2.3 写进 `CONTRACT.md`，版本升到 **v1.2**，并在群里通知大家。
- [ ] smoke test 新增一项：两个假 worker **用同一个 device_id**、一个用不同的 device_id 跑完 quick 任务，检查 `/api/contributions` 里的 `tiles_done` 合计 = 48，`verified_miter` 合计等于本地用 `compute_tile` 算出的结果，同一 device_id 只出现一行。
- [ ] `tg_native.bat` 支持设置 owner：在文件开头加一行 `set "OWNER="`，非空时传给 `--owner`。
- [ ] README 新增 "Contribution tracking" 一节，讲清楚记功规则、隐私说明、只记积分不涉及钱，以及如何导出 CSV。
- [ ] DEMO.md 在结尾加 15 秒：打开 Contributions 页，展示"每台设备做了多少已验证的工作"。

### 2.5 部署到现场服务器（Harry 执行，等功能 PR 都合并之后）

1. 先确认现场**没有任务在跑**。
2. 把 `main` 上更新过的文件复制到 `C:\Users\yangz\Desktop\nova-starter`（可以交给 Harry 当前会话的 agent 来做）。**不要覆盖 `.env`，不要删除 `backend/tartangrid.db`。**
3. **只重启服务器窗口**：在服务器窗口按 Ctrl+C，再双击 `tg_server.bat`。**隧道窗口不要动**，这样公网地址不变。native 和浏览器会自动重连；浏览器那边建议刷新贡献页，让它带着新的 device_id 重新注册。
4. 验证：打开 `/api/contributions`，今天早上那些旧任务也应该有记录（显示为 `wkr:` 开头的设备）；再跑一次 quick 任务，确认新的记录按 device_id 正确合并。

### 2.6 这次不做

真实身份认证或 CMU SSO、发钱或代币、按设备型号加权、双节点重复计算验证、自动兑换奖励。这些写进 README 的 roadmap 就行。

---

## 3. 时间线（16:30 冻结功能）

| 时间 | 里程碑 |
|---|---|
| 13:40 | D 合入 CONTRACT v1.2；A、B、C 开始开发 |
| 14:30 | A 的 PR 合并（迁移、记功、两个 API、测试） |
| 14:45 | B、C 的 PR 合并 |
| 15:15 | D 的 smoke test 全部通过，README 和 DEMO 更新完成 |
| 15:30 | Harry 部署到现场服务器（§2.5），再跑一次完整的 Demo 和断线演练 |
| 16:00 | 完成 1.1–1.5：两个浏览器 + 一个 native，测完 baseline，测完 AI，录好视频 |
| 16:30 | 冻结功能，开始彩排 |

**停损线**：15:30 时如果贡献度功能还没完全通过测试，**就不部署到现场服务器**。演示继续用现在这个稳定版本，贡献度功能只在 README 和 roadmap 里介绍。
