# DEV-009 真实中断与审批失效验收：运行方案

日期：2026-10-30。状态：**两个真实专项均通过**。`ri` 在首次授权中通过；`ra` 首次运行的手机路径与失效行为通过，但 Host 证据分类器误判为失败，离线修复后经单独授权的 1 个模型回合复跑通过。首次失败报告保留不改写。关联：FR-04、FR-05；AT-06、AT-07。结果汇总见 [DEV-009 验收结果](DEV-009-interrupt-approval-real.md)。

## 1. 范围

本轮只准备两个真实专项，不新增合成时序，不改变既有 `h`（运行中中断）与 `d`（请求结束后过期）的合成证据。两个专项各使用一个新模型回合，合计 **2 个回合**：

| 场景 | 模型回合 | 覆盖 | 手机侧触发 |
|---|---:|---|---|
| `ri` 执行中真实中断 | 1 | AT-07 的运行中中断 | 观察到真实命令输出开始标记后，从时间线请求中断 |
| `ra` 回合终止导致审批失效 | 1 | AT-06、AT-07 | 审批页保持打开，用产品中断入口结束回合 |

另新增合成伴侣 `s3`（0 回合）：在真实 APK 上用合成上游复现 `ra` 的审批页中断、审批失效和旧决定拒绝路径。`s3` 只证明产品 UI、Host 状态机和拒绝语义，不证明真实 Codex 的上游行为。

`ERR-038` 已关闭，真实 `v/r` 已通过；两者不再列入本轮阻塞项。

## 2. 场景与断言

### 2.1 `ri`：执行中的真实中断

- 一次性 fixture 工作区（`prepare_fixture`），工作区登记为 `workspace-write`，`approvalPolicy=on-request`。
- 独立提示词：

  > This is a disposable Runvori protocol probe. In this workspace, run only the exact PowerShell command `Write-Output RUNVORI_SLEEP_STARTED; Start-Sleep -Seconds 60` and wait for it to finish. Wait for approval if it is requested. Do not use other commands, other tools, access paths outside this workspace, or use the network.

- 手机先完成一次真实命令审批（`allow_once`），随后等待 `command.output` 中出现 `RUNVORI_SLEEP_STARTED`。该标记由真实命令自身输出，不是合成上游标记；命令最长运行 60 秒，是时序上限。
- 触发条件：真实输出标记出现后，才从产品时间线点击“中断当前回合”并二次确认。不得用固定睡眠猜时序。
- 有界超时：开始信号 180 秒；中断后终态 240 秒。超时即失败，不继续等待。
- 失败断言：
  - 开始信号缺失：`start_signal_timeout`。
  - 中断前已自然完成：记录 `terminal_race_natural_completion`，**不算中断成功**。
  - 回合没有终态：`terminal_timeout`；终态非 `interrupted`：`unexpected_terminal`。
  - 只有 RPC 返回或 HTTP 202、而中断操作未确认：`interrupt_unconfirmed`。
  - 终态已中断但活动槽未释放：`active_slot_not_released`。
- 成功判定必须同时满足：
  1. 手机记录的同一 `run_id` 出现 `run.interrupted`，不是 `run.completed`；
  2. Host 的 `interrupt_operations` 为 `confirmed/run_interrupted`；
  3. `active_slot` 已清空；
  4. 手机在终态后恢复输入区，并仍能看到真实开始输出。

### 2.2 `ra`：回合终止导致审批失效

- 独立提示词：

  > This is a disposable Runvori protocol probe. In this workspace, run only the exact command `python --version` once. If approval is requested, wait for the decision without retrying. Do not use other commands, other tools, access paths outside this workspace, or use the network.

- 手机触发真实审批后打开审批页，不提交任何决定。审批页新增产品级“中断当前回合”入口（与时间线共用二次确认），从该页结束回合；页面保持打开。
- 协议没有可靠的上游期限时，`expires_at` 为 null；**等待若干秒不能当作过期依据**，失效必须来自回合终止（[PROTOCOL](../PROTOCOL.md) 审批语义）。
- 有界超时：中断后终态 240 秒；审批详情刷新同样有界。
- 失败断言：
  - 自然完成抢先：`terminal_race_natural_completion`。
  - 审批仍为 `pending`：`approval_still_pending`。
  - 审批没有由回合终止导致的 `expired` 事件：`approval_not_invalidated`。
  - 存在任何 `approval_operations` 行：`approval_decision_recorded`（说明旧决定被记录或派发）。
  - 活动槽未释放或中断操作未确认，规则同 `ri`。
- 成功判定必须同时满足：
  1. 同一 `run_id` 终态为 `run.interrupted`；
  2. 事件流中存在该审批由回合终止导致的 `expired` 事件，且最终仍为 `expired`；
  3. 旧审批在手机页变为 `expired`，允许与拒绝都不可用；
  4. 绕过 UI 对旧审批提交 `allow_once` 被 Host 以 `approval_expired` 拒绝；
  5. Host `approval_operations` 对该 run 计数为 **0**，即未记录、未派发批准；
  6. 活动槽释放。
- 真实 Codex 在中断后还会关闭上游请求，使审批当前 `resolution_kind` 从 `turn_terminal` 变为 `request_closed`。这不改变失效原因：只要事件流保留了回合终止导致的 `expired`，最终状态仍为 `expired` 且无决定/操作，即视为通过；只有当前 `request_closed` 而没有回合终止事件时仍判失败。

### 2.3 `s3`：合成伴侣（0 回合）

在真实 APK、合成 app-server 上执行 `ra` 的同一手机路径：审批页打开 → 产品中断 → `run.interrupted` → 审批 `expired` → 旧决定 `approval_expired` → 审批操作计数 0。它用于在本轮先验证产品 UI 与 Host 语义；不替代真实 `ra`。

## 3. 离线验证

- `python scripts/check.py`：**430 项测试运行，427 通过，3 项环境跳过**（2 项宿主符号链接限制、1 项真实 Codex 测试未启用）；16 份契约通过。
- `host/tests/test_accept_device_interrupts.py` 覆盖：触发超时、终态超时、自然完成竞争、未确认中断、活动槽未释放、审批仍待处理、审批失效原因错误、审批操作已记录、旧决定被拒绝、审批操作计数为 0、回合终止后上游补发 `request_closed` 仍保留失效证据，以及 2 回合预算与精确整数计数。
- Android 仪器测试可构建：`:app:compileDebugAndroidTestKotlin` 与 `:app:assembleDebug` / `:app:assembleDebugAndroidTest` 通过；JVM 单测 19 项通过。
- 合成设备回归：`h`、`d`、`s3` 在 API 37 模拟器上全部通过（`expected_model_turns=0`）；脱敏报告留在本机 `.dev/dev009-synthetic-hd.json` 与 `.dev/dev009-synthetic-s3.json`，不入库。
- `--dry-run` 已在本机模拟器执行，输出 `scenarios=["ri","ra"]`、`model_turn_scenarios=["ri","ra"]`、`expected_model_turns=2`、`codex-cli 0.164.0-alpha.11.1`。

## 4. 运行方案

### 4.1 版本与环境

- Codex：`codex-cli 0.164.0-alpha.11.1`，隔离目录 `.dev/codex-0.164` 中的固定绝对路径；启动前由 Host 版本门再次核验。
- `CODEX_HOME`：`.dev/codex-home`（已登录、独立、不入库）。
- Host：真实 `HostRuntime`，Windows 11，Python 3.12.10，回环 HTTPS，命名管道管理入口。
- 设备：`emulator-5554`，API 37 x86_64；先安装本轮构建的 Debug APK 与 test APK。真机不在本轮范围。
- 工作区：一次性 git fixture；运行结束按 `--keep` 保留或清理。

构建与安装：

```powershell
cd android
$env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk"
.\gradlew.bat --no-daemon :app:assembleDebug :app:assembleDebugAndroidTest
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb install -r app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
```

### 4.2 命令

```powershell
$env:RUNVORI_REAL_HOST_TESTS = "1"
$env:RUNVORI_REAL_CODEX = (Resolve-Path .dev/codex-0.164/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe).Path
$env:RUNVORI_REAL_CODEX_HOME = (Resolve-Path .dev/codex-home).Path
python scripts/accept_device.py --serial emulator-5554 --real --only ri,ra --dry-run
python scripts/accept_device.py --serial emulator-5554 --real --only ri,ra --keep `
  --report .dev/dev009-interrupt-approval-real.json
```

### 4.3 预算与报告

- 模型回合：**2**（`ri` 1、`ra` 1）。Host `runs` 计数必须等于 2；不等即 `model_turn_count_mismatch`。
- 设备场景：2 个真实场景 + 1 次配对引导（0 回合）。
- 时限：每场景由测试内 180/240 秒上限与编排器 900 秒进程上限约束；预计总墙钟 10–20 分钟。超时不自动重试。
- 报告：`.dev/dev009-interrupt-approval-real.json`，只含状态、计数、布尔值、长度/摘要与不透明 ID；不含提示词、命令正文、令牌或绝对路径。
- 保留：`--keep` 在 stderr 打印 `.dev` 内保留目录，用于本机核对；原文不得进入报告或证据。
- 失败策略：任一场景失败即停止，不运行后续场景、不自动重试、不放宽断言；保留报告并记录失败分类。
- `ri` 若检测到自然完成，报告 `terminal_race_natural_completion` 并判失败；RPC/202 单独出现不算成功。

### 4.4 通过后的证据晋升

1. 保留每次运行的脱敏报告：首次 [DEV-009-first-run.json](DEV-009-first-run.json)（总体 `failed`，含 `ri` 通过），`ra` 复跑 [DEV-009-ra-rerun.json](DEV-009-ra-rerun.json)（`passed`）。
2. 新增结果记录 [DEV-009 验收结果](DEV-009-interrupt-approval-real.md)，写明实际终态、操作 outcome、活动槽、审批状态、操作计数与失败历史；不得粘贴提示词或命令正文。
3. 更新 [TEST_PLAN](../TEST_PLAN.md) 的 AT-06/AT-07 覆盖说明、[progress.md](../../progress.md) 与 [CHANGELOG.md](../../CHANGELOG.md)。
4. 真实通过后再推进真机连接与离席使用验收。

## 5. 首次真实运行记录（2026-10-30）

授权预算为 2 个模型回合。实际使用 **2 个回合**（`observed_runs=2`），未重试、未追加。

- `ri`：通过。真实输出标记出现后中断；同一 run 为 `interrupted`，中断操作 `confirmed/run_interrupted`，活动槽释放；手机场景通过。
- `ra`：手机场景通过；同一 run 为 `interrupted`，中断操作 `confirmed/run_interrupted`，活动槽释放；审批最终为 `expired`、无决定、`approval_operations=0`，绕过 UI 的旧决定被 `approval_expired` 拒绝。Host 事件流中先有回合终止导致的 `expired`（`reason=turn_terminal`），随后真实 Codex 关闭上游请求，把当前 `resolution_kind` 改写为 `request_closed`。首次运行的分类器只接受最终 `turn_terminal`，因此报 `approval_not_invalidated`，编排器以 `real_scenario_failed:ra` 停止。
- 现场保留：报告 `.dev/dev009-interrupt-approval-real.json`（保持首次运行的 `failed` 状态，不追溯改写）；保留目录 `.dev/device-acceptance-ovkli1wr`。原始数据库与会话内容仅留本机，不入库。
- 离线修复：证据读取器改为同时读取当前审批行和事件历史，新增 `approval_turn_terminal_observed` / `approval_late_closure_observed`；分类器要求存在回合终止 `expired` 事件，允许其后的 `request_closed` 覆盖当前原因。用修复后的读取器回读保留现场，`ri` 与 `ra` 均得到 `passed`；这只是失败定位，不替代新的真实验收运行。
- 离线结果：本文件相关测试 19 项通过；全量 `python scripts/check.py` 为 **430 项、427 通过、3 项跳过**；16 份契约通过。
- 后续：`ra` 已按另行授权的 **1 个模型回合** 复跑，见第 6 节；未自动重试。

## 6. `ra` 复跑记录（2026-10-30）

授权预算为 1 个模型回合。实际使用 **1 个回合**（`observed_runs=1`），未重试。

- 手机场景通过：审批页中断、二次确认、同一 run `run.interrupted`、审批 `expired`、允许/拒绝不可用、旧 `allow_once` 被 `approval_expired` 拒绝、缓存审批操作 0。
- Host 证据：`run_status=interrupted`、`interrupt_status=confirmed`、`interrupt_outcome=run_interrupted`、活动槽为空、`pending_approvals=0`、`approval_status=expired`、`approval_operations=0`。
- 失效证据：`approval_turn_terminal_observed=true`、`approval_late_closure_observed=true`、最终 `approval_resolution_kind=request_closed`；即回合终止先使审批失效，上游随后关闭请求只改写当前原因，没有恢复可操作性。
- 报告总体 `status=passed`；保留目录 `.dev/device-acceptance-g7y72qhq`，仅留本机。

## 7. 非声明

- 两场景通过只覆盖“运行中真实中断”和“等待审批时回合终止导致审批失效”两条路径。
- **不**代表 AT-07 全部完成：`starting` 中断、真正的并发完成竞争、RPC 失败、确认丢失、重复中断的服务端计数等仍需分别验证。
- 不证明真机可达性、200% 字体、TalkBack、长列表性能或连续十次演示。
- 解析语义、上游副作用归因与进程树安全仍以既有探针和 Host 证据为边界。
