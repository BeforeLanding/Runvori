# DEV-005 新回合请求幂等切片

日期：2026-10-25。需求关联：FR-02、FR-08；验收前提：AT-02、AT-08。此为 M1 所需的独立 SQLite 行为切片，仓库仍处 M0。

## 实现

- 事件库 schema 从 v2 升级为 v3，加入按 `(actor_id, request_id)` 唯一的 `run_operations`。原会话、事件、回合和活动槽保留；未知版本拒绝打开。
- `reserve_run_request` 在同一事务中写入操作记录、预留全局活动槽、创建 `starting` 回合、追加 `run.created` 事件并更新快照。只有提交后才返回 `accepted`；该状态仅代表 Host 本地持久受理。
- 摘要覆盖协议版本、设备/本地 actor、方法、路由及会话 ID、请求号、预期会话版本和输入。`run_id` 是服务端生成的资源标识，不参与请求摘要；同一请求重试即使调用方重新生成 `run_id`，仍返回原操作。相同键不同请求返回 `idempotency_conflict`。输入按 UTF-8 字节数限制为 16 KiB。
- `get_run_operation` 只按同一 actor 的请求号查询，不能跨 actor 读取原操作。本阶段仅持久记录 `accepted`；后续单次派发领取与 `unknown` 转换见 [记录](DEV-005-run-dispatch.md)，上游 turn 确认仍未实现。

## 验证

执行 `python scripts/check.py`：16 份契约通过；110 项测试运行，105 通过、5 项因本环境链接创建或回环连接限制跳过。新增 6 项行为测试覆盖响应丢失后跨重开重试、请求内容或路径冲突、双连接相同请求竞争、事件插入失败回滚、UTF-8 输入上限和 v2 数据升级。执行 `./scripts/smoke_package.ps1`：离线 wheel 构建、隔离安装、仓库外 CLI 契约检查及新增事件模块导入均通过。

尚未接入正式 HTTP、设备认证、Codex 派发或 Host 重启后的操作结果核对。当前表仅涵盖新回合请求，跨审批、中断等操作的全局请求号唯一性仍需统一操作表。不能因为返回 `accepted` 就显示回合已经启动、审批已经生效或执行已成功。
