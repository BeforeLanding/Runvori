# DEV-005 单库审批决定事务切片

日期：2026-10-25。需求关联：FR-02、FR-04、FR-09；路线图：M1 审批与操作持久化。项目仍处 M0，本记录只证明本地存储行为。

## 实现与边界

- `host.db` schema v5 增加 `approval_operations`。操作键由 actor 与 `request_id` 组成；摘要覆盖协议版本、方法、路由、审批 ID、请求号、预期版本及决定。新回合与审批操作在同一 actor 下不能复用请求号。
- `record_approval_decision` 只接受已绑定当前上游 turn、仍占用活动槽且处于 `pending` 的审批。`allow_once` / `deny` 分别映射到本地上游值 `accept` / `decline`；决定、操作、`approval.updated`、`operation.updated`、快照和会话版本在一个事务中提交。派发必须在该事务返回后由后续路径处理。
- 同一请求重试返回原操作；同 key 不同内容返回 `idempotency_conflict`。不同 key 对同一决定返回已有操作，相反决定返回 `decision_conflict`。操作可按 actor 与请求号跨数据库重开查询。旧版本和失效 epoch 分别拒绝。
- 旧 `ApprovalJournal.record_decision` 仍是独立探针路径；它没有写入新操作表。共享库中若由该路径提前写入决定，单库方法拒绝 `decision_state_untracked`，避免把没有操作证据的决定误报为已受理。

## 验证

执行 `python scripts/check.py`：16 份契约通过；138 项测试运行，133 通过、5 项因本环境链接创建或回环连接限制跳过。新增测试覆盖响应丢失后重开重试、不同请求号的同决定复用、反向决定/摘要冲突、跨类型请求号冲突、actor 范围、旧版本和失效 epoch，以及第二条事件插入失败时决定、操作、事件和快照全部回滚。

仍未实现审批的单库派发领取、上游确认/请求结束、异常恢复或正式 Host API。当前代码不能向上游发送真实审批决定，也没有 Android 业务入口；旧双库探针数据仍不自动迁移。
