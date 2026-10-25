# DEV-005 上游 thread 一次创建切片

日期：2026-10-25。需求关联：FR-02、FR-08、FR-09；路线图：M0 兼容映射与 M1 Host 生命周期。项目仍处 M0。

## 行为

- 共享 Host 在统一恢复并取得当前 epoch 后，为尚无回合及 thread 绑定的本地会话记录唯一创建意图。事件库 schema v8 保存 session、actor/request ID、固定参数摘要及 `accepted/dispatching/confirmed/unknown` 状态；相同请求可重放结果，冲突请求拒绝。
- 一次领取提交后才调用 `thread/start`。参数为受信任本地绝对目录 `cwd`、`approvalPolicy=on-request`、`approvalsReviewer=user`、`sandbox=read-only|workspace-write`、`ephemeral=false`。路径中的符号链接或 junction 拒绝。
- 有效响应中的 thread ID 与 session/thread 绑定在一个事务中确认。发送、响应或绑定失败时关闭连接并记未知；已领取未确认的创建在隔离后统一恢复中记未知，不自动重发。旧直接绑定入口不能覆盖未确认创建。

## 验证与限制

`python scripts/check.py`：16 份契约通过；191 项测试运行，186 通过、5 项因本环境链接创建或回环连接限制跳过。合成 stdio peer 与 SQLite 测试覆盖成功创建、跨重开重放、上游已创建但响应丢失、领取与绑定故障、无效响应、本地路径拒绝、恢复时未知状态及恢复事务失败回滚。合成 marker 只模拟上游已创建事实，不证明真实 Codex 的创建行为。

`scripts/smoke_package.ps1`：离线 wheel 构建、隔离安装、已安装 CLI 的 16 份契约核对及新增模块导入通过。

尚未接入产品 Host 的工作区登记、受管进程所有权和创建调度，也未对当前锁定 CLI 做真实 `thread/start` 联调。未知创建需要人工核实上游身份后制定可信补记流程。
