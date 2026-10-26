# DEV-005 已领取启动的中断意图

日期：2026-10-26。需求关联：FR-05、FR-09；路线图：M0 上游中断映射与 M1 Host 操作状态机。项目仍处 M0。

## 行为

- `turn/start` 发送权已领取，回合仍为 `starting` 且没有上游 turn 绑定时，中断请求原子记录 `accepted` 操作、`interrupting` 状态、事件和快照。绑定之前不能领取 `turn/interrupt` 发送权。
- 有效 `turn/start` 响应绑定真实 thread/turn，启动操作记 `confirmed`，回合仍为 `interrupting`；当前连接所有者随后可按真实绑定一次发送中断。终态先到则结算未派发中断，不发送迟到请求。
- 启动响应丢失或连接失效时，隔离恢复将已领取但未确认的启动保留为 `unknown`，未派发中断记 `failed/not_dispatched`；不猜测 turn 身份，也不重发启动或中断。

## 验证与限制

`python scripts/check.py`：16 份契约通过；246 项测试运行，241 通过、5 项因本环境链接创建或回环连接限制跳过。新增行为测试覆盖启动 RPC 返回前受理中断、绑定后一次派发、终态先于派发时的结算及启动未绑定时的隔离恢复。

`.\scripts\smoke_package.ps1`：离线 wheel 构建、隔离安装、已安装 CLI 的 16 份契约核对与模块导入通过。

尚未以真实 Codex 验证该并发时序；产品 API、Host 崩溃后的旧进程隔离证明及移动端交互仍未完成。
