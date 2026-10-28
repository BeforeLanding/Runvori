# DEV-008 受控产物与差异（第二切片）

日期：2026-10-27，2026-10-28 复跑。需求关联：FR-07、AT-13、AT-14；路线图：M2 主流程。状态：**合成设备验收通过，离线分块/重启/Git 来源测试通过**。本轮没有发起真实模型回合。

## 范围

本切片让结果页从“看到命令元数据”推进到“打开实际输出和差异”：

- 事件库 v17 新增 `run_artifacts` 与 `artifact_chunks`。
- 命令输出在时间线写入的同一 SQLite 事务中复制**实际持久化**的字节；时间线预算丢弃的字节只记入 `dropped_bytes`，不会从上游未保存内容补回。
- 文件差异保留两种独立来源：“审批提案”（`approval_proposal`）和“上游报告完成”（`upstream_report`）；首次读取产物时额外生成“Git 工作区快照”（`git_worktree_snapshot`）。
- 新增 `GET /v1/runs/{run_id}/artifacts` 与 `GET /v1/artifacts/{artifact_id}/content?offset=...&limit=...`。客户端只提交产物 ID，不能提交本地路径。
- Android 结果页增加产物清单，新增产物详情子页；内容按 `next_offset` 分块读取，空、截断、二进制、未跟踪文件和不可用内容分别显示。

## 产物模型

| 字段 | 含义 |
|---|---|
| `artifact_id` | `art_` + 32 位十六进制，由运行、类型、来源、item/approval 与相对路径确定性生成 |
| `kind` | `command_output` 或 `file_diff` |
| `source` | `command_execution` / `approval_proposal` / `upstream_report` / `git_worktree_snapshot` |
| `attribution` | `not_applicable` / `proposed` / `reported` / `unknown` |
| `path` / `move_path` | 相对路径；不返回本地工作区绝对路径 |
| `operation` | `add / delete / update / move / copy / type_change` |
| `representation` | `text / binary / unknown` |
| `size_bytes` / `sha256` | 已保存内容的字节数与 SHA-256；内容不可用时哈希为 null |
| `truncated` / `dropped_bytes` | 内容是否不完整及已知丢弃量 |
| `content_available` / `reason` | 内容是否可读及稳定原因码 |

命令输出只复制时间线实际持久化的字节。单回合时间线预算为 2 MiB；产物自身也有 2 MiB 上限。超过上限只保存前缀并继续标记 `truncated`，不会把丢失内容当作完整。

文件差异的来源含义：

- **审批提案**：审批 review 中展示给用户的 `changes[].diff`，`attribution=proposed`。它只代表请求时提出的改动。
- **上游报告完成**：`fileChange` item 的 `changes[]`，可能在 `item/started` 时为 `inProgress`，在 `item/completed` 时更新为 `completed`/`declined`；`attribution=reported`。它不与审批提案互相覆盖。
- **Git 工作区快照**：首次读取某回合产物时执行 `git status` 和逐文件 `git diff HEAD`，`attribution=unknown`。它可能包含回合前已有改动；未跟踪文件只记录路径和 `untracked_content_not_captured`，二进制只保留 `binary_metadata_only`，最多 16 个条目。它不是回合前基线，不能把内容归因给 Agent。

## 内容读取与安全

- 内容端点返回 `application/octet-stream`，使用 `X-Artifact-Size / Offset / Next-Offset / SHA256 / Representation / Truncated / Dropped-Bytes` 响应头。
- 客户端必须使用 `next_offset` 推进；单次上限 256 KiB，默认 64 KiB。偏移非法是 `416 invalid_range`，未知 ID 是 `404 artifact_not_found`，不可用内容是 `410 artifact_unavailable`。
- 产物 ID 不是路径；服务端不接受客户端提交文件系统路径。Git 命令使用固定参数和 5 秒超时，只读取登记工作区。
- 未授权、撤销设备与越界偏移在 API/设备层被拒绝；合成设备 `e` 继续验证撤销后所有读取失败。

## 合成设备验收

命令（Windows，API 37 x86_64 模拟器，APK 与 instrumentation APK 已安装）：

```text
ANDROID_HOME=<Android SDK> python scripts/accept_device.py \
  --serial emulator-5554 --report .dev/dev008-artifacts-synthetic.json
```

结果：`status: passed`，12 个场景全绿；`expected_model_turns=0`，上游为合成 app-server，不访问模型。脱敏报告见 [DEV-008-artifacts.json](DEV-008-artifacts.json)。

2026-10-28 在当前工作区复跑同一命令：重新构建并安装 debug 与 instrumentation APK，Host 从源码加载（包含 2026-10-27 22:06 对 `run_results.py` 的最后修改），12 个场景全绿。随后代码审查发现产物详情的旧分块竞态（[ERR-034](../ERRORS.md)），加入代际校验后再次重建 APK 并复跑，12 个场景仍全绿。脱敏 JSON 为最终一次结果；两次报告除监听端口外一致，最终一轮耗时约 156 秒（08:58 首轮约 82 秒，最终轮包含重新安装 APK 后的冷启动）。

新增/受影响场景：

| 场景 | 结果 | 说明 |
|---|---|---|
| `b` | passed | 命令审批通过后，从结果页打开命令输出产物，断言内容来自持久化产物。 |
| `j` | passed | 完整文件审批 `details_status=complete`，可允许；允许后结果页同时出现审批提案与上游报告，打开差异并断言 `RUNVORI_ARTIFACT_OK`。 |
| `k` | passed | 完整文件审批被拒绝后，审批提案差异仍可查看，回合正常结束。 |
| `i` | passed | 回合 `completed` + 验证 `failed` 仍成立，回归第一切片。 |
| 其余 `s/a/c/d/f/h/g/e` | passed | 回归配对、时间线、过期审批、不足详情、中断、重同步、操作查询与撤销。 |

运行过程中发现并修复了一个真实客户端竞态：Host 202 回执到达后立刻清除“决定在途”标记，在审批状态刷新前按钮会短暂重新可用。修复后回执不再清除标记，只有审批离开 `pending` 才恢复，重新打开审批详情可重新判断。记录见 [ERR-033](../ERRORS.md)。

## 离线状态矩阵

| 状态 | 覆盖 |
|---|---|
| 命令输出分块无遗漏/重复 | `test_command_output_is_chunked_stable_and_hashed`，按 4096 字节循环读取并比较完整内容与偏移 |
| Host 重启后哈希/内容一致 | 同一测试关闭并重开 `LockedHostJournals` 后再次读取 |
| 空输出 | `test_empty_output_is_available_and_explicit`，`size_bytes=0`、空 SHA-256、可读空内容 |
| 输出截断/丢弃量 | `test_truncated_output_keeps_dropped_bytes_visible`，保存字节 + 丢弃字节等于尝试写入总量 |
| 审批提案 + 上游报告 | `test_file_review_and_upstream_item_are_distinct_sources`，两种来源、路径、操作与归因 |
| Git 快照归因 | `test_git_worktree_snapshot_is_attributed_unknown`，改动文件可读、`attribution=unknown`；未跟踪文件仅元信息 |
| 非法 ID / 越界偏移 | `test_invalid_artifact_id_and_range_are_rejected` |
| API 鉴权/分块 | `test_api_handlers` 覆盖产物列表、内容读取与非法范围 |

全量离线检查：16 份契约通过，**388 项测试运行、3 项环境跳过、0 失败**；Android JVM 单测 19 项通过。

## 未完成与限制

- **真实 Codex 文件审批 `complete` 路径未跑**：本轮 `j/k` 使用合成上游；真实文件审批、真实重命名/二进制/大差异仍需单独授权验收。
- **Git 快照不是回合前基线**：首次读取时生成，可能包含已有改动，固定 `attribution=unknown`；未跟踪文件没有正文，最多 16 个条目。
- **文件改动仍没有时间线事件**：差异只从结果页产物进入；`artifact.created` / `artifact.unavailable` 事件未实现。
- **命令输出上限为 2 MiB**：与单回合时间线预算一致；更长的上游输出继续记为截断。
- **Windows 命令包装未规范化**：验证匹配仍按精确命令文本 + cwd，真实 Codex 可能落到 `unknown/not_run`。
- **快速返回/重开产物没有自动化用例**：旧分块竞态由代码审查发现并以代际校验修复（[ERR-034](../ERRORS.md)），设备验收只覆盖顺序打开产物；旋转、杀进程等恢复路径仍待专项验收。
- 未覆盖真机产物下载、并发分块、旋转/杀进程/大字体/TalkBack、结果页长列表性能。

## 关联

- [第一切片证据](DEV-008-results.md)
- [协议与状态](../PROTOCOL.md)
- [架构](../ARCHITECTURE.md)
- [测试计划](../TEST_PLAN.md)
- [ERR-033](../ERRORS.md)
- [ERR-034](../ERRORS.md)
