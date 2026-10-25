# DEV-005 审批隔离后 epoch 恢复切片

日期：2026-10-25。需求关联：FR-02、FR-04、FR-09；路线图：M1 审批状态与 Host 恢复。项目仍处 M0，本记录只证明本地存储行为。

## 实现与边界

- `LockedHostJournals.recover_approvals_after_isolation` 要求可信调用方确认旧受管进程已隔离。它持有 Host 单实例锁，在一个 SQLite 事务里处理所有 `pending`、`decision_recorded`、`dispatching` 审批，写对应事件/快照及操作结果，最后推进 epoch。无隔离证明则拒绝，库不变化。
- `pending` 变 `expired`；已有决定但未领取的审批变 `expired`，操作记 `failed/not_dispatched`；已领取的审批和操作变 `unknown/dispatch_unconfirmed`。已失效审批从 `open_approval_ids` 移除，`unknown` 保留以供核对。没有任何路径重发上游答复，也不将未知结果推断为已处理或执行成功。
- 该事务只处理审批 epoch。旧活动回合仍占槽，必须随后调用旧回合恢复路径以记录 `run.interrupted` 并释放槽；在这两步和真实进程隔离完成之前，产品启动编排应保持写入口关闭。旧独立 `ApprovalJournal.start_epoch_after_isolation` 不会同步单库操作/事件，不能作为产品单库恢复入口。

## 验证

执行 `python scripts/check.py`：16 份契约通过；145 项测试运行，140 通过、5 项因本环境链接创建或回环连接限制跳过。新增测试覆盖空库启动、未隔离拒绝、三种审批状态及两种操作结果、跨重开持久性、恢复后不能重新领取、与旧回合中断的顺序配合，以及处理多个审批时后续事件插入失败使全部审批、操作、事件、快照和 epoch 回滚。

仍未接入真实进程隔离、上游请求结束/确认、正式 Host API 或完整启动编排。当前方法的 `old_process_isolated=True` 是可信调用方提供的前提，不由此切片核对进程身份。
