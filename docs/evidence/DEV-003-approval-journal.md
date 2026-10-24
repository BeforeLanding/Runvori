# DEV-003：审批派发持久边界验证

日期：2026-10-24，Asia/Shanghai。对应 FR-04、FR-09 与 AT-05、AT-16 的部分技术前提；这是独立 SQLite 验证切片，不是 Host 或 Android 端到端验收。

## 实现和边界

- `ApprovalJournal` 使用 SQLite WAL 与 `synchronous=FULL`。审批请求按 `(epoch, upstream_request_id)` 隔离；决定和客户端 `request_id` 先入库，再由单个事务把 `decision_recorded` 领取为 `dispatching`。领取成功只给当前进程一次发送机会，重复领取被拒绝。
- 数据库重开后，调用方必须确认旧受管进程已停止或隔离，才可开启新 epoch。旧 `pending/decision_recorded` 变为 `expired`，旧 `dispatching` 变为 `unknown`，决定仍保留，均不自动派发。
- 同一客户端请求标识重复提交返回原决定；相反决定、跨审批复用标识及过期版本被拒绝。两个数据库连接同时提交相反决定时，只有一个成功。
- 上游请求结束单独记录为 `resolution_kind=request_closed`，不填充文件结果。对 `unknown` 的目标文件只读核对仅记录当时的 `absent/matches/different/unsafe_path` 快照，不改变 `unknown`，也不重新授予派发机会。该快照不能证明改动来源，读取与记录之间也不构成原子文件快照。

## 验证

`python scripts/check.py`：14 份 Codex schema 完整性通过，47 项测试通过。新增 6 项 SQLite 行为测试覆盖数据库重开、领取后崩溃、HTTP 确认丢失时的幂等查询、冲突决定、双连接竞争、上游 ID 跨 epoch 复用以及未知结果的文件核对。测试使用仓库 `.dev` 内临时数据库，不读取真实账号、不启动模型。

## 尚未验证

本切片未连接 `StdioClient`、Host API、事件/快照事务、进程隔离确认、活动回合槽或真实设备。`old_process_isolated` 由调用方声明，本切片无法自行证明进程树已停止。尚未在真实 Codex 审批派发中使用数据库，也未在物理断电或不可靠文件系统上验证持久性。M0 退出与 AT-05、AT-16 产品验收仍未完成。
