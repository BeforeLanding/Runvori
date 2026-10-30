# DEV-008 真实文件审批与验证匹配（第三切片）

日期：2026-10-28 实现与首次真实验收；2026-10-30 修复 `v` 并复验。需求关联：FR-07、AT-13、AT-14；路线图：M2 主流程。状态：**真实 `l/m/v/r` 全部通过**。锁定的 `0.164.0-alpha.11.1` 已从 npm 恢复到隔离目录并通过版本门；两次授权运行共 4 个模型回合（2026-10-28：允许、拒绝、验证；2026-10-30：验证），`r` 不产生模型回合，失败后均未自动重试。

## 范围

本切片解决真实 Codex 文件审批与验证结论在手机侧是否可靠的问题：

- **真实文件 item 的绝对路径**：真实 Codex 的 `fileChange` item 用工作区内绝对路径命名文件；产品原先无条件拒绝绝对路径，导致真实审批永远 `insufficient`，无法显示差异或允许。现在在写入审批与产物前，按该 run 的登记工作区根归一化为相对 POSIX 路径；工作区外、UNC/设备路径、转义、根目录本身和无法解析的路径保持原样并记为 `unsafe_change_path`。不做旧数据回填。
- **`item/updated`**：真实文件改动可能在 `item/updated` 才带路径与差异。文件 item 的 started/updated/completed 都折叠进审批；command/agent 的 updated 不当作完成证据。
- **审批页后到刷新**：审批请求先到、item 后到时，时间线版本推进但已打开的审批页原先不会刷新。现在按 timeline 的 `approval_version/status/details_status` 触发一次受控刷新，带在途/去重保护，不覆盖更新版本。
- **差异门槛**：没有可显示 diff 的文件审批保持 `insufficient`，只能拒绝；不满足 `move_path` 安全解析时同样不能允许。
- **Windows 包装验证匹配**：新增有限的包装等价：只接受裸命令、ERR-007/ERR-016 观察到的三种完整 pwsh 形状（`-Command '…'`、`-NoProfile -Command '…'`、`-Command "…"`），以及 ERR-038 的真实执行数组所示系统 Windows PowerShell 形态（`C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -Command <safe-exact>`）。Codex 随附 pwsh 必须位于其运行时绝对路径；系统 PowerShell 仅接受完整绝对路径、无附加参数、内层无 shell 元字符且与要求完全一致。附加参数、其他 shell、未引用、危险转义和未知形状一律不匹配，验证保持非通过。
- **重启读回**：设备验收在同一端口、同一 data_dir、同一证书/密钥上重启 Host，手机重新打开捕获的 run，比对命令文本与产物内容的 SHA-256；Host 侧同时复核命令与产物哈希。
- **真实场景**：`l` 文件审批允许、`m` 文件审批拒绝、`v` 验证结论；独立提示词、独立断言；`n` 合成审批后到 + 绝对路径 + `item/updated`；`r` 重启读回；`p` 一次性配对引导（无模型回合）。

## 真实夹具与提示词

真实文件审批使用独立只读工作区与提交过的单行文件 `approval-target.txt`：

- 基线：`RUNVORI_APPROVAL_BEFORE\n`；
- `l` 允许目标：`RUNVORI_APPROVAL_AFTER\n`；
- `m` 拒绝目标：`RUNVORI_APPROVAL_DENIED\n`。

提示词分别要求只用 `apply_patch` 做一次修改并在需要时请求审批；允许场景不要求重试，拒绝场景明确不重试。`v` 使用独立验证提示词，只运行一次 `python --version`。它们都不含 `[file-change]`、`[expire-request]`、`[interrupt]` 等合成标记；合成模式也不能运行 `l/m/v`，真实模式不能运行 `d/f/h/i/j/k/n`。

允许后由验收编排器直接读取工作区文件：`l` 必须等于 AFTER 字节，`m` 必须仍等于 BEFORE 字节；报告只包含布尔、大小和 SHA-256，不含文件正文。手机断言审批差异包含 BEFORE/AFTER 或 DENIED，并在结果页分别打开 `approval_proposal`（`proposed`）与 `upstream_report`（`reported`）。`v` 断言回合 `completed`、验证 `passed`、`python` 项退出码 0。

## 合成设备验收

命令（Windows，API 37 x86_64 模拟器，APK 与 instrumentation APK 已安装）：

```text
ANDROID_HOME=<Android SDK> python scripts/accept_device.py \
  --serial emulator-5554 --report .dev/dev008-third-synthetic.json
```

结果：`status: passed`，14 个场景全绿，`expected_model_turns=0`；脱敏报告见 [DEV-008-third-slice.json](DEV-008-third-slice.json)。

| 场景 | 结果 | 说明 |
|---|---|---|
| `n` | passed | 审批请求先到（`insufficient`、不可允许）；在审批页内等到 item/updated 的绝对路径差异后变为 `complete`、可允许；结果页同时读取审批提案与上游报告，路径均为相对 `probe-artifact.txt`。 |
| `r` | passed | Host 在同一端口、同一 data_dir、同一证书上重启，epoch 前进；手机重新打开捕获的 run，命令与产物 SHA-256/大小/正文哈希一致；Host 侧复核 `run_same/command_same/artifact_same=true`。 |
| `j/k` | passed | 重启后的文件审批允许/拒绝、结果页产物来源回归。 |
| 其余 `s/a/b/c/d/f/h/g/i/e` | passed | 配对、时间线、过期审批、不足详情、中断、重同步、操作查询、结果页与撤销回归。 |
| `l/m/v` | 单独运行 | 真实模式专用；0.164 恢复后 `l/m` 通过、`v` 结论失败，见下文“真实运行结果”。 |

单独运行 `--only n` 时自动执行配对引导 `p`，`p` 与 `n` 均通过，见 [DEV-008-third-bootstrap.json](DEV-008-third-bootstrap.json)。这证明“没有 `b` 时的一次性配对引导”可用；`p` 不被计为业务场景或模型回合。生命周期专项又新增 8 个合成场景，全量合成场景合计 22 个，见 [生命周期证据](DEV-008-lifecycle-recovery.md)。

## 真实运行结果

锁定版本通过 npm 安装到隔离目录 `.dev/codex-0.164`，未改动全局 Codex，也未做 0.165 适配。实际二进制为 `codex-cli 0.164.0-alpha.11.1`，隔离 `CODEX_HOME` 已登录；真实模式 `--dry-run` 返回 `status: ready`、`expected_model_turns=3`。执行：

```text
RUNVORI_REAL_HOST_TESTS=1 \
RUNVORI_REAL_CODEX=<.dev/codex-0.164/.../codex.exe> \
RUNVORI_REAL_CODEX_HOME=<.dev/codex-home> \
python scripts/accept_device.py --serial emulator-5554 --real \
  --only l,m,v,r --report .dev/dev008-third-real.json
```

结果：`observed_runs=3`，与授权回合数严格一致；失败后停止，未重试。脱敏报告见 [DEV-008-third-real.json](DEV-008-third-real.json)。

| 场景 | 结果 | 证据 |
|---|---|---|
| `l` 文件审批允许 | passed | 手机完整显示 BEFORE/AFTER 差异并允许；回合 `completed`；Host 侧 `approval-target.txt` 为 23 字节，SHA-256 `6be7e1e0…`，与 AFTER 字节一致；结果页区分并读取 `approval_proposal` 与 `upstream_report`。 |
| `m` 文件审批拒绝 | passed | 手机显示 DENIED 提案并拒绝；回合 `completed`；Host 侧文件仍为 24 字节，SHA-256 `ecba3ce1…`，与 BEFORE 一致；拒绝提案仍可查看。 |
| `v` 验证结论 | failed（已修复） | 回合 `completed`，实际命令退出码 0、输出 `Python 3.12.10`；`/results` 验证结论不是 `passed`。脱敏分类只有 `unrecognized`、长度 92；见 [ERR-038](../ERRORS.md)。 |
| `r` 重启读回 | 未运行（已补验） | `v` 失败后编排停止；临时数据目录已清理。 |

首次失败的原因不是命令执行失败，而是验证匹配没有识别 Host 保存的包装串。后续在没有模型回合的情况下用 app-server `thread/read` 取回该真实 thread 的 item 原文：92 字节的 `"C:\\windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command 'python --version'`（可执行文件加双引号、路径反斜杠成对转义、内层单引号）。修复后获授权执行了一次真实 `v,r`（1 个模型回合）：

```text
RUNVORI_REAL_HOST_TESTS=1 \
RUNVORI_REAL_CODEX=<.dev/codex-0.164/.../codex.exe> \
RUNVORI_REAL_CODEX_HOME=<.dev/codex-home> \
python scripts/accept_device.py --serial emulator-5554 --real \
  --only v,r --keep --report .dev/dev008-vr-real-20261030.json
```

结果：`status: passed`、`observed_runs=1`（与授权回合数一致）。脱敏报告见 [DEV-008-vr-real.json](DEV-008-vr-real.json)。

| 场景 | 结果 | 证据 |
|---|---|---|
| `v` 验证结论 | passed | 手机在审批页允许一次命令；回合 `completed`，`verification.summary=passed`，`python` 项退出码 0。报告的命令分类为 `system_powershell_wrapper`、长度 92、`inner_exact=true`，`command_match_asserted=true`。 |
| `r` 重启读回 | passed | Host 在同一端口、同一 data_dir、同一证书上重启，epoch 前进；手机按捕获的 run 读回，命令 SHA-256、产物 SHA-256 与大小一致；Host 侧复核 `run_same/command_same/artifact_same=true`（命令 `445adfcd…`，产物 `6a5d9f64…`，16 字节）。 |

授权运行使用 `--keep` 保留数据目录；`scripts/inspect_kept_commands.py` 只在本地 `.dev` 内核对 Host `run_commands.command` 与上述 92 字节原文逐字节一致。该原文、cwd 与保留目录都不进入报告或提交证据。

## 离线状态矩阵

| 状态 | 覆盖 |
|---|---|
| 绝对路径在登记工作区内 | `test_absolute_workspace_paths_are_normalized_for_review`、`test_item_before_approval_with_absolute_path_is_normalized`：路径与 move_path 变为相对，审批 `complete`。 |
| 工作区外/UNC/转义 | `test_paths_outside_the_workspace_stay_unsafe_after_normalization`：保持 `insufficient` + `unsafe_change_path`。 |
| 缺失差异/不安全 move_path | `test_missing_diff_and_unsafe_move_path_block_allow`：`diff_missing`/`unsafe_change_path`，禁止允许。 |
| 审批前或后到 | `test_absolute_item_update_is_folded_and_artifacts_are_relative`：`item/updated` 折叠；`approval_proposal` 与 `upstream_report` 路径一致且相对。 |
| 新 epoch 不继承旧 item | `test_item_summary_from_an_old_epoch_is_not_reused`：旧 summary 的 `process_epoch` 不匹配，新审批保持 `insufficient`，新 item 到达后才完整。 |
| `item/updated` 投影 | `test_item_updated_projects_file_changes_only`：文件 updated 折叠；command updated 明确忽略。 |
| 包装匹配正向 | `test_observed_wrapper_shapes_match`、`test_escaped_double_quotes_match`、`test_observed_system_powershell_unquoted_wrapper_matches`、`test_known_wrapper_passes_but_unknown_wrapper_never_does`：三种 pwsh 形状与未加引号的系统 PowerShell 形态通过。 |
| 真实序列化正向 | `test_observed_quoted_system_powershell_wrapper_matches`、`test_observed_app_server_wrapper_passes_verification`：使用真实 92 字节原文，断言分类、长度与验证 `passed`；成对/单反斜杠与更多重复分隔符解析为同一固定路径。 |
| 序列化边界反向 | `test_quoted_system_powershell_boundaries_are_rejected`、`test_unobserved_wrapper_variation_does_not_pass_verification`：`-NoProfile`、附加参数、双引号内层、尾随内容、`..`、其他盘符/可执行文件均拒绝，验证不通过。 |
| 包装匹配反向 | `test_extra_commands_flags_and_shells_are_rejected`、`test_system_powershell_boundaries_are_rejected`、`test_other_executables_and_paths_are_rejected`、`test_quotes_and_expansion_that_change_execution_are_rejected`：附加命令、其他 shell/路径、危险展开均拒绝。 |
| Host 重启后产物字节/哈希 | `test_host_runtime_restart_keeps_artifact_bytes_and_hashes`：同一 data_dir 启动真实 `HostRuntime` 后产物 ID、大小、SHA-256 与分块字节一致。 |

全量离线检查：16 份契约通过，**411 项测试运行、3 项环境跳过、0 失败**；Android JVM 单测 19 项通过。

## 未完成与限制

- **真实 `v` 的原文依赖本机线程历史**：定位所用的 `thread/read` 需要隔离 `CODEX_HOME` 中保留的会话；报告和提交证据只保存分类与长度，原文核对必须在 `.dev` 内完成。真实 `v` 只复验了固定 `python --version`，其他包装/命令形状仍需原文证据。
- **真实文件审批已通过的部分**：`l/m` 的完整差异、允许/拒绝、Host 文件字节与结果页双来源已通过；仍需在真机上复验 HTTPS、字体与长差异。
- **差异缺失的门槛**：没有 diff 的文件审批只能拒绝；这是有意的保守行为，可能影响真实上游只在部分操作提供 diff 的情况。
- **`item/updated` 的 command/agent 内容未投影**：本切片只把文件 updated 当作审批证据；命令完成仍依赖 `item/completed`。
- **epoch 标记不是 schema 迁移**：`approval_items` 的 summary 内写入 `process_epoch`，旧行不匹配时忽略；没有 v18 迁移，也不回填旧数据。
- **长路径/设备路径前缀**（如 `\\?\C:\…`）按不可证明的工作区路径拒绝；真实 Codex 若使用该前缀会保持 `insufficient`。
- **重启读回只覆盖 `devsess0` 的命令输出产物**：文件差异产物的重启哈希由离线分块/重开测试覆盖，手机侧重启读回选的是最新命令输出 run。
- 未覆盖真机 HTTPS、200% 字体、TalkBack、长列表性能与连续十次演示；旋转/杀进程恢复已由 [生命周期专项](DEV-008-lifecycle-recovery.md) 在模拟器覆盖，但仍需真机复验。

## 关联

- [DEV-008 第二切片：受控产物与差异](DEV-008-artifacts.md)
- [DEV-008 第一切片：命令详情与验证结果](DEV-008-results.md)
- [DEV-007 真实设备验收](DEV-007-device-acceptance-real.md)
- [协议与状态](../PROTOCOL.md)
- [ERR-035](../ERRORS.md)：真实文件 item 绝对路径被误判为不安全
- [ERR-036](../ERRORS.md)：重启后 item/timeline 绑定未按 epoch 限定
- [ERR-037](../ERRORS.md)：审批 item 后到时审批页不刷新
- [ERR-038](../ERRORS.md)：真实验证使用系统 Windows PowerShell 包装，首次结论未通过（已取得原文、修复并复验）
- [真实 v/r 脱敏报告](DEV-008-vr-real.json)
- [生命周期恢复专项](DEV-008-lifecycle-recovery.md)
