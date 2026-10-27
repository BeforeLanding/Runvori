# DEV-005 旧受管上游进程的持久身份与启动隔离门

日期：2026-10-26。需求关联：FR-09、NFR-01；路线图：M1 Host 生命周期与 AT-16 前提。项目仍处 M0。

后续更新（同日）：事件库 v13 已将“无身份记录”改为显式 `legacy_unknown` 并失败关闭，取代本文第 11 条描述的 v12 行为。本文保留为 v12 切片的历史证据，当前策略见 [Host 启动与崩溃重启闭环](DEV-005-host-runtime-restart.md)。

## 行为

- 启动受管上游时，在目标进程被放行之前把启动器的 `pid` 与 Windows 进程创建时间（`GetProcessTimes`）写入权威库 `host.db` 的 `upstream_processes` 表（事件库 v12，以 `process_epoch` 为主键）。写入发生在 Job 归属之后、单字节放行之前，因此任何已产生副作用的进程都已有持久身份。
- `recover_host_after_isolation` 不再接受调用方传入的 `old_process_isolated` 布尔值。它从数据库读取上一 epoch 的记录并按 PID＋创建时间核实：`running` 与 `inaccessible` 一律抛 `upstream_isolation_unconfirmed`，不进入恢复事务、不推进 epoch、不打开写门禁。
- 判定集合：`absent`（进程不存在）、`exited`（同创建时间且退出码不是 `STILL_ACTIVE`）、`replaced`（PID 相同但创建时间不同，即 PID 复用）为可放行；`running`、`inaccessible` 拒绝。查询失败不降级为「不存在」。
- 同一 epoch 内重试启动（例如 `initialize` 失败后重试）只有在旧记录已可放行时才覆盖，避免两个进程同时归属一个 epoch。
- v12 之前的 epoch 没有记录，升级不回填。v12 当时这类库按旧行为恢复；**v13 起已改为 `legacy_unknown` 失败关闭**，不再自动恢复，见 [后续策略](DEV-005-host-runtime-restart.md)。

## 验证与限制

`python scripts/check.py`：16 份契约通过；294 项测试运行，292 通过、2 项因本环境链接创建或回环连接限制跳过。连续 4 次运行均通过。新增/改写测试：

- 进程身份：捕获值与内核 `GetProcessTimes` 一致、活进程 `running`、退出后不再是 `running`、PID 复用判为 `replaced`、非法身份拒绝。
- 门禁：恢复前写门禁关闭；记录的进程存活时恢复被拒且 epoch 不变；进程退出后恢复成功并打开写门禁；同 epoch 覆盖被拒/被允许；非本 epoch 归属与非法参数拒绝。
- 跨进程崩溃：真实子进程持锁启动 fake app-server 后被杀，重开库恢复成功（Job 已杀整棵树）；另一用例让子进程记录一个 Job 外的活进程后被杀，重开库恢复被拒，清理前写门禁保持关闭。
- 启动链：`launch_owned_upstream` 写入的 pid 与 `client.process.pid` 一致；`initialize` 失败后同 epoch 重启用新 pid 覆盖旧记录。
- 迁移：v11 升级到 v12 不产生回填行，CHECK 约束拒绝非法 pid/created_at/epoch。

限制：该记录只证明**启动器**（执行 `bootstrap.py` 的进程）的身份，不证明其后代进程树已停止；进程树由 kill-on-close Job 约束，本切片不替代 Job。恢复前的核实结果不持久化，只用于当次门禁。v12 之前的 epoch 无记录，v12 当时无法据此拒绝对应旧进程；v13 起这类库按 `legacy_unknown` 失败关闭，见 [后续策略](DEV-005-host-runtime-restart.md)。`EventJournal.recover_run_after_isolation` 与 `ApprovalJournal.start_epoch_after_isolation` 仍保留探针专用的布尔参数，产品门禁只在 `LockedHostJournals`；两个入口不构成产品恢复路径。产品启动编排已由 [HostRuntime](DEV-005-host-runtime-restart.md) 接入；HTTP/TLS 与本地管理入口仍开放。