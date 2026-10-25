# DEV-005 单库审批请求结束切片

日期：2026-10-25。需求关联：FR-02、FR-04、FR-09；路线图：M1 审批状态与上游证据。项目仍处 M0，本记录只证明本地存储行为。

## 实现与边界

- `record_approval_request_closed` 核对审批 ID、epoch、上游请求标识和已绑定上游 turn；允许匹配的结束证据在回合已终态后到达。它在单一事务里更新审批、关联操作、`approval.updated`/`operation.updated`、会话快照和版本。重复同一关闭证据返回原状态，不追加事件；错误请求身份被拒绝。
- `pending` 结束后为 `expired`；`decision_recorded` 结束后为 `expired`，未领取操作记 `failed/request_closed_before_dispatch`。`dispatching` 或同 epoch 的 `unknown` 在匹配结束证据后为 `resolved`，操作记 `confirmed/request_closed`。这里 `request_closed` 只确认上游请求结束，不能证明允许决定被采用、命令已执行或文件副作用存在。
- 已失效或已解决审批从快照 `open_approval_ids` 移除。隔离后进入新 epoch 的旧 `unknown` 不接受未持久化的迟到关闭声称；未来若要消费该证据，必须先建立可验证的持久来源和归因。

## 验证

执行 `python scripts/check.py`：16 份契约通过；149 项测试运行，144 通过、5 项因本环境链接创建或回环连接限制跳过。新增测试覆盖回合终态后的 pending 关闭、未领取决定结束、已领取与同代次 unknown 结束、错误上游请求标识、重复通知不新增事件，以及第二条事件写入失败时审批、操作和快照共同回滚。

仍未接入真实 app-server `serverRequest/resolved` 通知映射、正式 Host API 或命令/文件副作用核对；当前调用方提供的上游身份需由未来适配层验证。
