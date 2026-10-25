# DEV-005 单库审批派发领取切片

日期：2026-10-25。需求关联：FR-02、FR-04、FR-09；路线图：M1 审批与操作持久化。项目仍处 M0，本记录只证明本地存储行为。

## 实现与边界

- `claim_approval_dispatch` 只接受当前 epoch、仍占用活动槽且绑定同代次上游 turn 的 `decision_recorded` 审批和 `accepted` 操作。它在单事务中把两者推进为 `dispatching`，各追加 `approval.updated` 与 `operation.updated`，更新快照和会话版本。只有提交返回后，调用方才可尝试一次上游发送；第二次领取返回 `dispatch_unavailable`。
- `mark_approval_dispatch_unknown` 在发送结果不确定时，以同样的单库事务把已领取的审批和操作推进为 `unknown`，操作结果记 `dispatch_unconfirmed`。失败写入不会撤销既有领取，调用方不得因此重发上游答复。
- 上游请求内容和真实派发不在本切片中；当前 `ApprovalJournal.start_epoch_after_isolation` 仍是旧独立探针方法，不会同步审批操作和事件。产品启动必须改用统一恢复事务，不能把该探针方法当作单库恢复入口。

## 验证

执行 `python scripts/check.py`：16 份契约通过；142 项测试运行，137 通过、5 项因本环境链接创建或回环连接限制跳过。新增测试覆盖第一次领取、跨数据库重开后拒绝第二次领取、模糊发送转 `unknown`、旧 epoch 拒绝，以及领取与未知记录在第二条事件插入失败时的完整回滚。未知记录写入失败后，持久状态仍为 `dispatching`，也不可重新领取。

仍未接入真实 app-server 答复、上游确认/请求结束、Host 新 epoch 统一恢复或正式 API。因此这里的 `dispatching` 只表示已取得一次发送资格，不能推断审批已被上游处理或命令已执行。
