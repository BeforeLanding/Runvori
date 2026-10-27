# DEV-005 产品 Host 启动与崩溃重启闭环

日期：2026-10-26。需求关联：FR-02、FR-04、FR-05、FR-09、NFR-01；路线图：M1 Host 生命周期；验收参考：AT-16。项目仍处 M0，本文不表示 AT-16 已全部通过。

## 行为

### 事件库 v13：epoch 身份状态

- 新增 `upstream_epochs(process_epoch, state, recorded_at)`，`state` 为 `unlaunched`、`recorded` 或 `legacy_unknown`；`upstream_processes` 继续保存 PID＋创建时间。
- 每次持锁打开库时对账当前 epoch：无状态行且 epoch 为 0 视为首启；epoch > 0 且已有身份行写 `recorded`；epoch > 0 且无身份行写 `legacy_unknown`。出现未来 epoch 状态、状态与身份行不一致、或 epoch 0 却存在上游历史时，以 `upstream_epoch_state_invalid` 拒绝打开。
- 统一恢复的门禁是全量的：epoch 0 无历史、`unlaunched` 无身份行才放行；`recorded` 必须按 PID＋创建时间探测为 `absent`／`exited`／`replaced`；`legacy_unknown` 返回 `legacy_upstream_unverified`；其余一律拒绝。缺记录不再等同于已隔离。
- 恢复成功在同一事务为新 epoch 写 `unlaunched`；`record_upstream_process` 在同一事务把 `unlaunched` 改为 `recorded`，同 epoch 重试只有旧进程可放行才覆盖。
- 旧库策略为失败关闭：`legacy_unknown` 不自动回填、不推进 epoch、不打开写门禁、不创建配对库。文档规定的迁移是停止旧 Host、归档旧数据目录、新建 Host 并重新配对；不提供人工“已隔离”声明。

### 产品 Host 启动入口

新增 `python -m runvori.host --data-dir <目录> [--codex codex]`，其 `HostRuntime` 固定顺序为：

1. 取得 Windows 单实例锁并打开/迁移数据库；
2. 对账 epoch 状态，`legacy_unknown` 或状态损坏在创建配对库、邮箱或上游前拒绝；
3. 在持锁事务中统一恢复（`host_restart`），写门禁此时打开；
4. 创建命令邮箱及审批、thread 启动、回合启动、中断队列；
5. 组合 `HostDeviceAccess`，取得权威 `host_id` 并初始化配对库；
6. 最后启动唯一受管上游，记录启动器身份并初始化。

`start()`、`run()`、`stop()` 必须由同一线程执行；外部线程只能使用邮箱与有界队列。正常运行停止使用 `host_shutdown` 原因，先关闭邮箱，再隔离上游、统一恢复并关闭库与锁。隔离无法确认时不关库、不放锁，下一次启动继续失败关闭。

上游连接故障由事件泵隔离并恢复（`upstream_exit`），旧回合标记 interrupted，Host 不重拉上游、不自动重跑；进程以退出码 3 结束，等待外部监督重启。启动拒绝为退出码 2 并输出稳定 code，正常停止为 0。

## 验证

`python scripts/check.py`：16 份契约通过；336 项测试运行，333 通过、3 项跳过（2 项本环境链接创建限制，1 项未启用真实 Codex 测试类）。`scripts/smoke_package.ps1` 的离线 wheel 构建、隔离安装、CLI 契约校验及含 `runvori.host`／`process_identity` 的模块导入通过。新增/改写覆盖：

- v13 迁移：v12 升级不产生回填；表 CHECK 拒绝非法状态与 epoch 0 状态行。
- 对账与门禁：首启、`unlaunched`、`recorded` 探测放行/拒绝、`legacy_unknown` 稳定拒绝、状态与身份行不一致拒绝、epoch 0 历史拒绝、`host_shutdown` 原因。
- HostRuntime：首次启动组合设备访问；干净停止后旧设备对象拒绝、锁释放；第二实例争锁；启动失败清理；上游故障结束进程；跨线程调用拒绝；CLI 入口的拒绝与上游恢复退出码。
- 真实进程崩溃：`host/tests/host_peer.py` 在子进程运行真实 `HostRuntime`，在五个切点 `TerminateProcess` 杀掉 Host：

| 切点 | 杀掉前证据 | 重启后断言 |
|---|---|---|
| starting | 事件循环已领取 `turn/start`，fake peer 已解析请求 | run interrupted；run 操作 `unknown/dispatch_unconfirmed`；无 `run.started` |
| running | 邮箱快照 `active_run.status=running` | run interrupted；操作保持 `confirmed/turn_started`；turn 绑定保留 |
| waiting_approval | 快照 `waiting_approval` 且 `open_approval_ids` 非空 | run interrupted；审批 `expired`、不可再用；快照清空 |
| approval_dispatched | 审批决定经邮箱受理并派发，fake peer 收到答复但不发请求结束 | run interrupted；审批 `unknown`、操作 `unknown/dispatch_unconfirmed`；审批 ID 保留；无新操作 |
| interrupting | 邮箱已受理中断，fake peer 已解析 `turn/interrupt` | run interrupted；中断操作 `unknown/dispatch_unconfirmed`；无伪造 `turn.completed` |

每个用例还验证：

- 崩溃后 `journal_meta.epoch` 不变；重启后相对推进，旧 epoch `recorded`，新 epoch 先 `unlaunched` 后 `recorded`；活动槽清空；
- `run.interrupted` 的 reason 为 `host_restart`；`run.created` 仅一次，重启与新 Host 启动都不自动重跑；
- 新上游 PID/创建时间与旧记录不同；
- 记录的 launcher、fake app-server、其后代 leaf 三个真实 PID 在 Host 被杀后被探测为 `absent/exited/replaced`，未使用 mock PID；
- 重启 Host 正常停止（`host_shutdown`）后，再启动第三个 Host 成功，证明闭环与锁释放；
- 上游故障用例：运行中的回合被标 interrupted、reason `upstream_exit`，Host 返回 `upstream_recovered` 后退出，新 Host 可启动。

五切点定向崩溃测试（含审批决定派发）连续运行 3 次全部通过；全量检查结果见 progress.md。

## 限制

- 本文的崩溃用例上游是合成 JSONL peer，不是真实 Codex app-server；Job 隔离与进程树结论只覆盖当前测试进程树。真实 Codex 的五个切点、版本门、审批决定入口和真实进程树见 [真实 Codex 五切点](DEV-005-host-runtime-restart-real-codex.md)；该证据仍未关闭首发 Host OS 等 AT-16 条件。
- “审批决定已派发后崩溃”切点现已通过产品命令邮箱入口触发；本文的合成用例与 [真实 Codex 五切点](DEV-005-host-runtime-restart-real-codex.md) 分别覆盖，不把底层派发测试当作产品验收。
- 产品 CLI 的版本门与真实二进制启动已在真实 Codex 证据中验证；`codex` 仍不在 PATH，真实入口通过 `--codex` 绝对路径或 `RUNVORI_CODEX` 指定。
- 上游正常退出时，若仍有后代进程继承 stdio 管道，Host 的 EOF 检测会被推迟；本次测试替身曾因此挂起，见 [ERR-027](../ERRORS.md)。真实 Codex 是否派生此类后代尚未验证，Job 只在 Host 死亡时兜底。
- 旧数据目录按策略不可自动继续；归档重建会更换 `host_id` 并需要重新配对，本轮不实现数据迁移工具。
- 本文的合成崩溃测试通过数据库层直接播种 session、workspace 和 `session_threads`，不覆盖真实 `thread/start` 创建路径；真实 Codex 证据已通过产品 `thread/start` 创建 thread 并开始首回合，见 [真实 Codex 五切点](DEV-005-host-runtime-restart-real-codex.md)。
- 每次正常停止都会推进 epoch 并写 `unlaunched`；这是一次 Host 启动对应一个 process epoch 的约定，不是异常。
- 尚无 HTTP/TLS、Android 联调、本地管理入口或进程内上游重拉；审批决定入口只在 Host 命令邮箱，审批详情读取尚未产品化。
