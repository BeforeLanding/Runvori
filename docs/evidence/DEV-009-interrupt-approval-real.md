# DEV-009 真实中断与审批失效验收结果

日期：2026-10-30。状态：**两个真实专项均通过**，但由两次授权运行组成：首次 `ri/ra` 共用 2 个模型回合，`ri` 通过、`ra` 因验收分类器误判失败；随后单独授权 1 个回合复跑 `ra` 并通过。所有运行均未自动重试。

关联：FR-04、FR-05；AT-06、AT-07。覆盖边界见文末“非声明”。

## 1. 运行与环境

- Codex：`codex-cli 0.164.0-alpha.11.1`，隔离 `CODEX_HOME`，真实 Codex app-server。
- Host：真实 `HostRuntime`，Windows 11，回环 HTTPS。
- 设备：`emulator-5554`，API 37 x86_64；真实 APK 对真实 Host，不使用合成上游。
- 工作区：一次性 git fixture，审批策略 `on-request`。
- 模型回合：首次运行 2 个（`ri` 1、`ra` 1）；复跑 `ra` 1 个。合计 3 个，均为显式授权，失败即停、无自动重试。
- 原始命令、会话内容与保留目录只在本机 `.dev`，不提交；本文件只引用脱敏报告中的状态、计数、布尔值和不透明 ID。

## 2. 首次运行：`ri` 通过，`ra` 验收判失败

报告：[DEV-009-first-run.json](DEV-009-first-run.json)（保持首次运行的 `failed` 总体状态，不追溯改写）。

### 2.1 `ri`：执行中真实中断（通过）

- 真实命令输出 `RUNVORI_SLEEP_STARTED` 后，手机才从时间线请求中断；未用固定睡眠猜时序。
- 同一 run 终态为 `interrupted`，中断操作 `confirmed/run_interrupted`，活动槽已释放。
- 手机场景通过：终态后恢复输入区，真实开始输出仍在时间线。
- 未出现自然完成竞争。

### 2.2 `ra`：回合终止导致审批失效（手机通过，Host 分类器误判）

- 手机场景通过：审批页保持打开，用产品中断入口结束回合；旧审批为 `expired`，允许/拒绝不可用；绕过 UI 的 `allow_once` 被 `approval_expired` 拒绝；缓存审批操作数为 0。
- Host 事实：同一 run 为 `interrupted`，中断操作 `confirmed/run_interrupted`，活动槽释放，`approval_operations=0`。
- 事件顺序：先记录回合终止导致的 `expired`（`reason=turn_terminal`）；随后真实 Codex 关闭上游请求，把当前 `resolution_kind` 改写为 `request_closed`。
- 首次分类器只接受最终 `turn_terminal`，因此报 `approval_not_invalidated`，编排器以 `real_scenario_failed:ra` 停止。这是验收器对真实上游关闭顺序的错误假设，不是失效路径失败。

## 3. `ra` 复跑（通过）

报告：[DEV-009-ra-rerun.json](DEV-009-ra-rerun.json)。1 个模型回合，`observed_runs=1`，与预期一致。

- 手机场景通过：审批页中断、二次确认、同一 run `run.interrupted`、审批 `expired`、允许/拒绝不可用、绕过 UI 的旧决定被 `approval_expired` 拒绝、缓存审批操作 0。
- Host 证据：`run_status=interrupted`，`interrupt_status=confirmed`，`interrupt_outcome=run_interrupted`，活动槽为空，`pending_approvals=0`，`approval_status=expired`，`approval_operations=0`。
- 失效证据：`approval_turn_terminal_observed=true`；`approval_late_closure_observed=true`；最终 `approval_resolution_kind=request_closed`。即回合终止先使审批失效，之后上游关闭请求只改写了当前原因，没有恢复可操作性。
- 报告总体 `status=passed`。

## 4. 覆盖结论

| 路径 | 证据 |
|---|---|
| AT-07 运行中真实中断 | 首次运行 `ri`：真实开始信号后中断，持久终态、操作 outcome、活动槽释放、手机显示一致 |
| AT-06 等待审批时回合终止导致失效 | 复跑 `ra`：审批不可操作、旧决定被 Host 拒绝、零审批操作 |
| AT-07 等待审批时中断 | 复跑 `ra`：回合 `interrupted`、中断操作确认、活动槽释放 |

失败历史保留：[DEV-009-first-run.json](DEV-009-first-run.json) 仍是 `failed`；修复说明与复跑结果分开记录，不把首次失败改写成通过。

## 5. 非声明

- 通过只覆盖“运行中真实中断”和“等待审批时回合终止导致审批失效”两条路径。
- **不**代表 AT-07 全部完成：`starting` 中断、真正的并发完成竞争、RPC 失败、确认丢失、重复中断的服务端计数仍需分别验证。
- 不证明真机可达性、200% 字体、TalkBack、长列表性能或连续十次演示。
- 解析语义、上游副作用归因与进程树安全仍以既有探针和 Host 证据为边界。
