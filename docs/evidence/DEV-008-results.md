# DEV-008 命令详情与验证结果（第一切片）

日期：2026-10-27。需求关联：FR-07、AT-13、AT-14；路线图：M2 主流程。状态：**合成设备验收通过，离线状态与重启持久化通过**。本轮没有发起真实模型回合。

## 范围

本切片把“回合结束”与“验证结论”从时间线上的文本提示，推进为可查询的 Host 资源和独立手机页面：

- 事件库 v16 新增 `run_commands`、`run_verifications`，并为 `workspaces` 增加 `verification_json`。
- 工作区可由受信本地入口声明必需验证命令；每个回合创建时快照当时的要求，之后修改工作区不改写旧回合的含义。
- 新增 `GET /v1/runs/{run_id}`、`GET /v1/runs/{run_id}/results`、`GET /v1/runs/{run_id}/commands?cursor=...&limit=...`。
- Android 新增“回合结果”页：回合状态与验证结论分卡显示，命令列表显示 cwd、状态、退出码和输出完整性，并支持命令分页。
- 验证判定复用 `runvori.codex.verification` 的 `RequiredCommandEvidence` / `RequiredVerificationSet`，没有另写一套优先级或“全部通过”逻辑。

## 命令证据

每条 `commandExecution` item 在写入时间线事件的同一 SQLite 事务里更新 `run_commands`：

- 持久保存命令原文（≤8 KiB，超限置 `command_truncated`）、cwd、上游开始/完成毫秒时间、状态、退出码、持续时间、开始/完成事件 seq。
- 输出完整度分为 `pending / complete / truncated / empty`，另记实际保存字节数与被预算丢弃的字节数。输出被截断不会把退出码改写成 0，也不会让验证自动失败；它只把“输出是否完整”标成 `truncated`。
- 命令完成事件在聚合输出写入之后落库，因此完成时已能判断输出完整度。

## 验证语义

`run_verifications` 在 run 创建时从工作区配置快照，包含 `requirement_key / command / cwd`。读取结果时按持久证据重放给现有验证器：

- 必需项为空：`verification.configured=false`、`summary=not_run`。手机显示“未配置必需验证项”，不显示“通过”。
- 已配置但回合结束仍无执行证据：该项 `not_run`，汇总按协议优先级计算。
- 命令完成但退出码缺失/类型不对：该项 `unknown`，不把 null 当 0。
- 有已确认非零退出码：该项 `failed`；回合本身仍可为 `completed`。
- 唯一匹配的开始与完成证据、cwd 匹配、退出码为 0：`passed`。
- 命令文本被截断：不能匹配任何要求；即使审批已允许，也只是 `unknown`，不会误报通过。
- 同一 item 充当多个要求、重复开始/完成、时间倒置或身份不匹配：`unknown`。

产品适配的适用范围：现有验证器按**精确命令文本 + 解析后的 cwd** 匹配。Windows Codex 可能把命令包在 PowerShell 启动器里；本轮没有把这种包装规范化为“等价命令”。因此真实 Windows 运行若出现包装，会保守地落到 `unknown`/`not_run`，而不是误判通过。这是首切片的有意限制，不是已完成的能力。

## 合成设备验收

命令（Windows，已启动 API 37 x86_64 模拟器，APK 与 instrumentation APK 已安装）：

```text
ANDROID_HOME=<Android SDK> python scripts/accept_device.py \
  --serial emulator-5554 --report .dev/dev008-synthetic.json
```

结果：`status: passed`，10 个场景全绿；`expected_model_turns=0`，上游为合成 app-server，不访问模型。脱敏报告见 [DEV-008-results.json](DEV-008-results.json)。

新增/受影响场景：

| 场景 | 结果 | 说明 |
|---|---|---|
| `b` | passed | 合成命令审批与完成后，手机打开结果页，断言回合 `completed`、验证 `passed`、命令证据存在。 |
| `i` | passed | 工作区验证配置切换为 `exit 7` 后新回合：审批命令为 `exit 7`，回合仍 `completed`，结果页验证汇总 `failed`、该项退出码 7、输出 `complete`，命令列表包含同一证据。 |
| 其余 `s/a/c/d/f/h/g/e` | passed | 回归既有配对、时间线、过期审批、不足详情、中断、重同步、操作查询与撤销链路。 |

`i` 场景不读取时间线文本猜测结果，而是通过产品自己的 `GET /v1/runs/{run_id}/results` 与 `/commands` 取数并渲染结果页。

## 离线状态矩阵

| 状态 | 覆盖 |
|---|---|
| 回合 `completed` + 验证 `failed` | `test_completed_run_can_carry_failed_verification_and_survive_reopen` |
| 未配置必需项 | `test_unconfigured_workspace_is_explicit_and_command_survives_reopen` |
| 已配置但 `not_run` | `test_not_run_missing_exit_and_truncated_output_are_distinct` 的 `never` 项 |
| 退出码缺失 → `unknown` | 同上的 `missing` 项；`exit_code_present=false`、`exit_code=null` |
| 输出截断 | 同上的 `long` 项；`output_state=truncated`、`output_dropped_bytes>0`，退出码 0 仍使该项 `passed` |
| 命令分页 | `test_command_pages_are_stable_and_do_not_repeat_items`，两页无重复、游标可继续 |
| Host 重启后查询 | `test_host_runtime_start_keeps_results_queryable`：先写入已完成结果，关闭后在同一数据目录启动真实 `HostRuntime`，结果仍为 `completed/passed` |

全量离线检查：16 份契约通过，**381 项测试运行、3 项环境跳过、0 失败**；Android JVM 单测 19 项通过。

## 未完成与限制

- **产物/差异仍未实现**：`/artifacts`、产物字节端点、文件改动时间线不在本切片。
- **验证配置只有本地入口**：没有手机端或远端修改必需命令的接口；这是有意的信任边界。
- **未发 `verification.updated` 事件**：结果在读取时按持久命令证据计算；时间线仍只显示命令与回合事件。
- **未跑真实 Codex**：本轮只验证合成上游与真实 APK；真实 Windows 命令包装、真实退出码与真实验证配置需要另一次显式授权。
- **结果页未覆盖**：旋转/杀进程/大字体/TalkBack、长命令列表性能、产物子页。
- 结果页读取命令第一页后按需分页；尚未做“从时间线锚点跳转到某条命令”的联动。

## 关联

- [协议与状态](../PROTOCOL.md)
- [架构](../ARCHITECTURE.md)
- [测试计划](../TEST_PLAN.md)
- [DEV-007 真实设备验收](DEV-007-device-acceptance-real.md)
- [ERR-032](../ERRORS.md)：合成验收把成功的验证配置步骤误标为失败（已修复）
