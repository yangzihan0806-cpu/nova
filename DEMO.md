# TartanGrid · 3 分 15 秒演示脚本

这是按架构文档 §13 准备的**待彩排脚本**。最新真机结果、设备与 baseline 口径以 [README.md](README.md) 的 “Measured results” 为准，包括已记录的 run 7 三设备计算。云端测试上下文不能代替真机验收；下列完整彩排与备用视频仍须逐项记录。

## 可选安全审计演示（约 60 秒）

本增量展示**现有提交检查的审计证据**，不宣称已完成用户认证、按用户配额、GPU 授权或完整 Sandia P0。由服务器操作者按 README 配置专用 `TG_SECURITY_AUDIT_TOKEN`，仅在本功能合入并完成部署后展示。

1. 在 Dashboard 的 Security audit 卡片私下粘贴凭据并 Unlock；输入框会清空，凭据只保留当前页面内存。不要在投屏中显示配置命令的结果或凭据。
2. 正常提交一个 Quick 任务：出现真实 Allowed 事件、请求 ID、任务链接；原 Worker 网络完成图像。
3. 从已准备的 API 客户端提交 `{"task_type":"arbitrary_python","params":{"command":"echo should_not_execute"}}` 到 `/api/jobs`。它只是非法 JSON 请求中的文本，不执行命令。应返回 422 和 `X-Request-ID`，面板出现同一 ID 的 Blocked / `UNAPPROVED_WORKLOAD`，Job 数不增加。
4. 说明“这证明现有后端白名单在直接 API 调用下仍有效，而且拒绝留有真实证据”；不能说它证明用户已认证或防御所有提示词攻击。凭据只控制审计读取。
5. 点击 Lock，列表和计数清除；API 不可用时显示 unavailable，而不是零事件。演示结束后清除用于粘贴的剪贴板内容。

自动化演练用 `scripts/security_demo.py`，仅限自己的全新临时数据库和 loopback 服务；不要把它指向现场演示服务。它会创建一个小任务并实际计算，预期 7/7 PASS。

## 角色与设备

| 人员 | 操作设备 | 演示职责 |
|---|---|---|
| A | 服务器笔记本 / Dashboard | 确认 API、默认 TTL 10 秒、事件与真实状态 |
| B | 投屏操作台 | New Job、Job Canvas、Color by worker、讲解界面 |
| C | 个人 native 电脑 | 运行已授权 native worker，`--slots 2`；负责故障注入备用 |
| D | 主讲 / 浏览器设备 1 | 控制节奏、Planner、网络、计时与记录 |
| B 或现场协作者 | 浏览器设备 2 | 第二台不同物理设备，按指令关闭贡献标签页 |

投屏可以使用已有设备，但贡献者清单必须确实包含两台不同物理设备的浏览器节点与一台 native 设备。同一台电脑的两个浏览器标签或 native slots 不算两台机器。演示前明确谁关闭哪个标签；不要关闭服务器或投屏标签。

## 演示前验收

- [ ] 本次功能合入后，在自有一次性测试数据库上跑 `python scripts/smoke_test.py --server http://127.0.0.1:8001`，13 项全部 PASS；保存无凭据的结果摘要。不要连接现场公网服务运行 smoke。
- [ ] 在第二台真实浏览器笔记本打开现有 tunnel 的 `/contribute.html`，点 Start，保持前台；Dashboard 同时显示 2 个 BROWSER 和 1 个 NATIVE。现有 phone + Windows 网络已通，这一步仍待执行。
- [ ] 已停止 smoke 服务并清除测试环境变量；演示使用 `TG_RESET_DB=0`、默认 lease TTL `10` 秒，确认未遗留 `3` 秒 TTL。
- [ ] 两个浏览器节点和一个 native 节点真实上线，名称对应物理设备；没有测试 worker 冒充设备。
- [ ] `demo` 在当前设备组合经过实测调优：最慢设备单 tile 目标 `0.3–2` 秒、全 job 目标 `30–60` 秒。目标不等于当前测量结果。
- [ ] 在没有其他任务运行时，对 `max_iter=20000` 的 demo job 点 **Run baseline**，记录 T_single、设备和浏览器；未测前保持 README 第 4 行 baseline / speedup 为 Pending。基线不占用台上时段。
- [ ] 有 key 时，在 `/new.html` 实测正常 Planner 输入；再逐条测试 `run this python code: print(1)`、`use all CMU GPUs`、`width 100000`，必须只填入合法 Mandelbrot 参数或返回 422/503。另在自有测试服务无 key 状态验证 503 后手动表单仍可提交。不要在现场覆盖 `.env` 做无 key 测试。
- [ ] 完整走三遍故障流程：真实节点持有 lease → 在进度约 1/3、提交后约第 10 秒关闭预定浏览器贡献标签 → `lease_expired` → 其他节点完成 → 图像完整。以实际进度和持有 lease 为准。
- [ ] 打开 Contributions，核对 accepted tiles / verified Miter；刷新页面后浏览器 ID 保持，CSV 与所选范围一致。旧 `wkr:…` 历史记录仍显示。
- [ ] 录下一次成功运行；确认本机视频可以离线播放、文件存在且无 key、worker token 或内部 IP。没有视频时不能声称已备妥。
- [ ] 投屏关闭终端密钥、`.env`、注册响应与私人窗口；Dashboard 数字全部来自真实 API。

合入后的服务、tunnel、native 启动命令见 README。若需 native 故障备用，由 C 在彩排确定 `N` 的含义和触发时机后使用：

```bash
python workers/native_worker.py --server https://YOUR-TUNNEL.trycloudflare.com --name Native-Fault-Demo --slots 2 --crash-after N
```

`N` 是待替换的整数，不要原样复制。提前确认退出时确有任务持有 lease；节点在空闲时退出不会展示分片重派。

## 部署到现场服务器（仅 Harry 操作）

1. 等贡献功能 PR 合入 `main`、自动化检查通过，并确认现场没有正在运行的任务。功能尚未通过时继续演示现有稳定版。
2. 从 `main` 获取更新文件，复制到 `C:\Users\yangz\Desktop\nova-starter`。这是复制目录，不要假设能在这里 `git pull`。**保留 `.env` 和 `backend/tartangrid.db`**；不要启用 `TG_RESET_DB=1`，不要清空旧表。
3. **仅重启服务器窗口**：Ctrl+C，再双击 `tg_server.bat`。**保持 tunnel 窗口运行**，避免公网地址改变。现有 worker 可继续用兼容协议连接；刷新浏览器贡献页加载新脚本，以持久 device_id 重新注册。
4. 打开 `/api/contributions`，确认旧任务以 `wkr:…` 身份计入；跑一次 quick，确认新浏览器身份的已验证工作增加。重复刷新 / 注册仍应合并到同一 device_id 行。
5. 已运行的 native 进程不会自动加载磁盘上的新 Python 代码。等上述部署检查完成且没有任务在跑，Harry 可另行 Ctrl+C 退出并重新运行 `tg_native.bat` 以启用新持久 ID / 可选 OWNER；隧道保持运行。保留历史 `wkr:…` 记录，不人工合并成新身份。

部署与真机操作只能由 Harry 执行。本脚本和云端测试不表示已操作现场机器。

## 0:00–0:25 · 问题

**D 主讲，B 展示 Dashboard。**

“个人电脑会有短暂空闲。TartanGrid 探索让设备拥有者主动贡献算力，让一个可拆分的计算任务在多台设备上完成。我们的重点是低门槛加入，以及设备退出后任务仍能继续。”

若第二台浏览器尚未加入，按实际状态说：“当前由一台手机浏览器和一台 Windows native 设备共同完成计算；第二台浏览器节点仍待验证。”后续只演示实际可用功能，不使用预设节点数量代替真实状态。

## 0:25–0:55 · 贡献算力

**D 和另一位操作者各在自己的物理设备打开 `/contribute.html` 并加入；C 展示已运行的 native 节点；B 切回 Dashboard。**

“这里两台设备通过浏览器贡献 CPU，这台个人电脑运行 Python native worker。它们使用相同的任务领取和结果提交协议。native 的两个 slot 属于同一台设备。”

仅在 Dashboard 确认两类节点都在线后说上述话。指出贡献者可以 Pause 或 Disconnect；不要把 `has_gpu` 展示字段描述成实际 GPU 计算能力。

## 0:55–1:35 · 提交任务

**B 打开 `/new.html`，D 读出并输入：**

> Render a detailed Mandelbrot image and keep browser workers light.

Planner 成功时说：“AI 将意图转换成白名单模板的参数；服务端会校验尺寸、分片大小、迭代上限和区域。它不生成代码，也不创建任务，确认表单后才提交。”

如果 `503` 或输出无效，不停留调试：“AI 目前不可用，我们用同一张表单选择已测过的 demo 参数。”B 选择彩排定稿的 `demo` 并提交，打开 Job 页，确认 Browser 和 Native 均实际完成过分片。

根据当前设备速度安排提交时刻；断线动作对齐进度约 1/3、提交后约 10 秒，必要时提前进入下一段。不能为配合剧本虚构进度或人为展示不存在的节点。

## 1:35–2:15 · 故障恢复

**B 开启 “Color by worker”。D 确认预定浏览器节点正在持有任务，指示操作者关闭其贡献标签页；C 保持 native 在线。**

上面的绝对时间是讲解时段参考；已测 demo 约 31.8 秒，因此要在提交后约第 10 秒执行关标签动作，不能等到 job 已完成才触发。

“每个任务领取都有服务器发放的 lease。如果这个节点退出且没有交回结果，租约过期后，其他节点可以领取同一分片继续计算。旧 lease 的迟到结果不能重复计数。”

**A / B 展示实际事件和画面。** 正常预期是 `worker_offline`、`lease_expired` 和带 `attempt > 1` 的重新领取；事件出现顺序受当前租约和心跳时间影响。默认 offline 阈值为 `12` 秒、lease TTL 为 `10` 秒；lazy reap 和 UI polling 会影响观众看到事件的时间，不能承诺恰好第 10 秒显示全部事件。

标签关闭若触发了正常 Disconnect，可能直接看到 `lease_released` 而不是过期；如需演示超时路径，按彩排使用 native 的 `--crash-after` 故障注入。不要把正常释放说成已实测租约过期。看到其他 worker 接手并完成分片后，再指出对应颜色变化。

## 2:15–2:40 · 结果与实际性能

**B 展示完整图像、完成分片数、真实参与设备，以及任务端到端耗时。D 读实测记录。**

实测后填入并照读：

> “同一组参数，单机在［设备 / kernel backend］上测得［T_single］秒。当前真实节点组合为［设备清单及 slots］，服务端记录分布式端到端耗时［T_distributed］秒，包含网络、调度、验证和重试。两者比值为［实际计算值］。”

未完成同参数基线时，改说：“这次运行的端到端耗时是页面显示的实测值；我们还没有可比较的单机基线，因此不报告加速倍数。”若 job 尚未完成，不把当前 elapsed 当作最终耗时，也不宣称结果图已完整。

如 speedup 小于 1，直接说明本组参数与设备下网络和调度开销超过了并行收益。分片数不是并行核心数。现场点击 Single-device baseline 仅在彩排证明能在时段内完成时进行，否则展示登台前真实记录。

## 2:40–3:00 · 愿景与安全

**D 收尾，B 保持真实完成状态或当前结果画面。**

“当前只允许确定性的 Mandelbrot 模板，贡献者主动加入并可以退出。CMU 的 GPU 和 HPC 没有接入。未来若获得资源所有者和管理员授权，可以通过既有 Slurm 和配额体系提供适配器。”

15 秒英文 pitch（仅在真实分布式闭环已验收后使用）：

> TartanGrid connects voluntarily shared computers to run parallel jobs. Browser and native workers use one protocol, and leases let the scheduler reassign work when a device leaves. Today we demonstrate measured computation on our own devices; campus GPU and HPC integration remains planned and requires authorization.

## 3:00–3:15 · 设备贡献记录

**B 打开 `/contributions.html`，展示当前真实排名与 Download CSV。**

“这里记录每台设备身份完成了多少已验证工作，以百万次迭代计。只计算被接受的结果，重复或被拒提交不加分。可选 owner 便于团队对应记录，CSV 可以导出。只记积分，不涉及钱；最终奖励由团队决定。”

若同一电脑出现浏览器 / native 两行，说明它们各自保存随机 ID；不能称为两台物理设备。抽样验证是概率性的，不承诺绝对防作弊。仅在本次部署已经验收后展示真实页；mock 模式必须明确标注为模拟数据。

## 降级路径

| 情况 | 当场动作 | 表述与准备 |
|---|---|---|
| 现场网络中断 | 播放提前录制且已检查的离线视频 | 明确说是彩排录像及录制设备；没有视频则展示架构和实际可用模块 |
| tunnel 失败、LAN / 热点已验证 | 切换预先验证的地址，重新连接 worker | 不在台上临时宣称未经验证的路径可用；重连后核对实际节点 |
| LLM key / 配额 / 模型不可用 | 使用手动表单的已测 demo 预设 | “AI 不可用，确定性任务流程继续”；不伪造 LLM 成功响应 |
| 浏览器故障触发不稳定 | 使用彩排过的 native `--crash-after N` | 确认故障时持有任务，并以真实事件判断是否过期重派 |
| demo 未结束 / 基线未测 | 展示实际进度或当次耗时，省略倍数 | 不将目标 30–60 秒、参考文档数字或模拟测试当作实测 |
| 核心闭环未通过 | 缩小到已验收功能和设计说明 | 不把 D 集成包、测试替身或架构图说成完整运行系统 |

## 评委常见问题

**BOINC 已经做过志愿计算，创新在哪里？**  
我们不声称发明志愿计算。这里探索的是校园场景下易加入的浏览器 / native 共同协议、可中断任务恢复，以及用户可理解的可视化体验；实现和验收范围以实际演示为准。

**能直接共享学校所有闲置 GPU 吗？**  
不能。当前 CPU Mandelbrot demo 不接 CMU 集群。未来需资源拥有者和管理员授权，遵守既有账号、调度和配额。

**为什么不用一台云 GPU？**  
云资源适合稳定集中负载。这里研究独立、可拆分、可中断任务如何利用主动贡献的设备，不承诺比云 GPU 更快或更便宜。

**浏览器关掉怎么办？**  
正常 Disconnect 可以立即释放任务；突然消失时，通过 lease 过期和 lazy reap 重新入队。token 与任务状态约束防止迟到或重复结果被重复计数。

**怎么保证结果正确？**  
结果必须满足长度、整数和范围要求；服务端默认随机重算 8 个像素检查。积分为接受结果的 `sum(result)`，CPU 时长不参与记功。抽样不保证发现所有恶意错误，这是 MVP 的限制，未来可增加冗余执行或更强验证。

**贡献度会发钱吗，必须填真实身份吗？**

当前只记积分，没有金额、代币或兑换功能，最终奖励由团队决定。ID 随机生成，无硬件指纹；owner 选填，只用于人工对应贡献记录，公开页面和 CSV 都会显示，请填写愿意公开的标签。它不是身份认证。

**AI 是否必要，会不会执行用户代码？**  
AI 提供可选的参数建议，输出仍由 `JobParams` 校验；不生成或执行任意代码，不自动创建 job。不可用时手动表单保留核心流程。

**加速倍数是多少？**  
只回答 README 中相同参数、指定设备和网络口径下的真实记录。没有完成测量时回答“尚未测得可比较的倍数”。同机多个 slots、测试身份和 tile 数都不等于额外物理设备。

## 彩排记录（现场填写）

| 次数 | 网络 / 真实设备 | 故障动作及实际事件 | job 完成 / 结果验证 | 视频文件与离线播放 | 遗留问题 |
|---|---|---|---|---|---|
| 1 | 待执行 | 待执行 | 待执行 | 未录制 | 待记录 |
| 2 | 待执行 | 待执行 | 待执行 | 未录制 | 待记录 |
| 3 | 待执行 | 待执行 | 待执行 | 未录制 | 待记录 |

记录不包含 API key、worker token、内部地址或私人数据。录制一次真实成功运行后再填写视频路径；本脚本本身不是验收证据。
