# DEV-005 中断请求持久边界

日期：2026-10-25。需求关联：FR-05、FR-09；路线图：M0 上游中断语义与 M1 Host 操作状态机。项目仍处 M0。

## 行为

- 事件库 v10 新增 `interrupt_operations`；旧库升级创建空表并保留现有回合。已绑定上游 turn 的 `running / waiting_approval` 回合可在一个事务中记录请求摘要、操作、事件及 `interrupting` 快照。
- 同请求重放原操作，同回合新请求返回已有操作，冲突摘要拒绝；已终态回合直接返回终态。`starting` 且未绑定 turn 的中断明确拒绝。
- `accepted` 操作只在匹配活动槽与上游 epoch 时一次领取为 `dispatching`；失败后的 `unknown` 写入须匹配原派发 epoch。回合终态同事务给出实际最终状态，不能将其归因于中断成功；隔离恢复使未派发失败、已领取未知，原未知不追加重复操作事件。

## 验证与限制

`python scripts/check.py`：16 份契约通过；233 项测试运行，228 通过、5 项因本环境链接创建或回环连接限制跳过。定向测试覆盖幂等与冲突、领取和 epoch、终态先后顺序、隔离恢复、写入故障回滚及 v9 升级。

`.\scripts\smoke_package.ps1`：离线 wheel 构建、隔离安装、已安装 CLI 的 16 份契约核对及模块导入通过。

本轮只建立持久边界，没有向上游发送 `turn/interrupt`，也没有产品 API。`starting` 回合、中断 RPC 明确错误与终态的竞争、真实 Codex 时序仍需后续验证。
