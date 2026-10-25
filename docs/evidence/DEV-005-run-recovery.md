# DEV-005 旧回合隔离后恢复事务

日期：2026-10-25。需求关联：FR-09、FR-02；验收前提：AT-16。当前仍处 M0，本记录只验证数据库事务语义。

## 实现与边界

- `recover_run_after_isolation` 必须由可信 Host 调用方明确给出 `old_process_isolated=True`、旧进程代次、目标 `run_id` 与预期回合版本；未隔离时直接拒绝，活动槽保持占用。已领取操作的代次不匹配也拒绝。此方法自身不能核对或终止 OS 进程。
- 隔离条件满足后，在同一 SQLite 事务中处理操作与回合：未领取的 `accepted` 请求记为 `failed / not_dispatched`；已领取的 `dispatching` 请求记为 `unknown / dispatch_unconfirmed`；已有 `unknown` 保留不确定性，已有 `confirmed` 保留上游 turn 曾启动的事实。回合追加带 `host_restart` 或 `upstream_exit` 原因的 `run.interrupted`，更新快照，并仅按匹配 `run_id` 释放活动槽。
- 重复同原因恢复返回原终态事件，不追加事件；不同原因、旧回合或错误版本不能清除后续活动回合。操作变更事件与中断事件任一写入失败都回滚整次恢复。不会重发原请求或自动启动新回合。

## 验证

执行 `python scripts/check.py`：16 份契约通过；128 项测试运行，123 通过、5 项因本环境链接创建或回环连接限制跳过。新增 6 项行为测试覆盖未隔离拒绝、未领取失败和幂等、已领取未知及代次拒绝、未知/已确认事实保留、终态写入失败回滚和旧回合不能释放新槽。

尚未接入 Host 单实例锁、受管进程身份核对、Windows Job 的真实恢复编排、审批失效、Codex 实际进程退出或 Android 显示。`old_process_isolated` 是调用方声明，不能将本测试视为已经证明旧进程安全停止；产品集成必须先实现并验证这一前提。
