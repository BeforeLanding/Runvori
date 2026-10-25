# DEV-005 上游审批结束身份映射切片

日期：2026-10-25。需求关联：FR-02、FR-04、FR-09；路线图：M0 兼容映射与 M1 审批状态。项目仍处 M0，本记录只证明适配和本地持久化行为。

## 依据与实现

- 已保存的真实脱敏形状显示 `serverRequest/resolved` 带 `params.threadId`（字符串）和 `params.requestId`（整数），见 [DEV-003 命令拒绝](DEV-003-command-declined.json) 与 [待审批中断](DEV-003-pending-interrupted.json)。这些样本来自 CLI `0.163.0-alpha.11.2`；当前固定的 `0.164.0-alpha.11.1` 尚须重新核对完整字段语义。通知只代表请求结束，不代表允许决定生效或命令成功。
- `upstream_request_key` 将整数和字符串 JSON-RPC ID 分别编码为 `int:` 与 `str:` 命名空间，并限制标识大小；审批请求落库与结束通知必须使用相同编码。布尔值、空值、过大标识和错误通知形状拒绝。
- `record_resolved_notification` 从原始通知取出 thread 与带类型的请求键；共享库再以 thread、epoch 和已绑定上游回合查找审批 ID，调用既有请求结束事务。错误 thread、epoch 或请求键不改状态；重复通知返回原记录，不追加事件。

## 验证与限制

执行 `python scripts/check.py`：16 份契约通过；155 项测试运行，150 通过、5 项因本环境链接创建或回环连接限制跳过。新增测试覆盖整数/字符串同号隔离、错误通知拒绝，并用合成 JSONL peer 的 `StdioClient.next_event` 走到单库关闭事务；错误 thread/epoch、重复通知和两类请求 ID 的独立审批均有行为断言。

执行 `scripts/smoke_package.ps1`：离线 wheel 构建、隔离安装、已安装 CLI 的 16 份契约核对和新增适配模块导入通过。

尚无产品事件循环自动调用该适配器；审批请求的上游身份、item 内容和权限范围仍须由未来 Codex 适配层验证。合成 peer 测试不能代替当前锁定 CLI 的真实联调。
