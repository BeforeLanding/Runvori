# 变更记录

采用开发批次记录；正式发布前不把开发批次称为已发布版本。

## Unreleased · CI 与 Android UI 拆分 · 2026-10-31

- 新增 GitHub Actions [CI](.github/workflows/ci.yml)：Windows runner 执行 `python scripts/check.py`（契约＋Host 离线测试＋文档一致性），Ubuntu runner 执行 `:app:testDebugUnitTest :app:assembleDebug :app:assembleDebugAndroidTest`，失败时上传单测报告。不启动模拟器、不调用 Codex。
- `ui/RunvoriApp.kt`（1183 行）按屏幕拆为 `RunvoriApp.kt`（外壳、横幅、中断确认）、`PairingScreen.kt`、`SessionsScreen.kt`、`TimelineScreen.kt`、`ApprovalScreen.kt`、`ResultsScreen.kt`、`ArtifactScreen.kt`、`StatusText.kt`、`Tag.kt`、`Actions.kt`；只把跨文件调用的 `private` 改为 `internal`，用脚本逐行核对原文件 54–1184 行的非空代码行在新文件中一一对应、无遗漏无新增。
- `ui/RunvoriViewModel.kt`（1502 → 1308 行）只做删除：`AppState`/`Screen` 等状态类型移到 `AppState.kt`，不读写状态的错误码文案函数移到 `ViewModelText.kt`；同步逻辑未改动。时间线状态容器留到阅读模型一起设计，本轮不改 `AppState` 结构。
- 验证：Android JVM 单测 19 项通过，Debug 与 AndroidTest 构建通过；合成设备验收 两次全套运行均为 22 通过、`t` 失败；以拆分前 `main` 构建对照复现同一失败，单独运行 `s,t` 通过，判定为既有问题并记为 ERR-041（开放），不是本次引入；`python scripts/check.py` 16 份契约、446 项测试运行（443 通过、3 跳过）。

## Unreleased · 文档结算与一致性护栏 · 2026-10-31

- **M0 结算**：新增 [ADR-017](docs/DECISIONS.md)，以已有证据关闭 M0；首版只承诺 Windows 11 x64 Host、Python 3.12 标准库与 `codex-cli 0.164.0-alpha.11.1`。待决策清单改为状态表：Q-01/02/03/04/05/07 已决定（剩余边界列为已知限制或移入 M4），Q-06 性能基线移入 M3，Q-08 仍开放。
- **状态单一来源**：`progress.md` 重写为一屏快照（82 KB → 约 5 KB），新增里程碑状态表；`docs/ROADMAP.md` 删去随里程碑追加的 DEV/Q 进展段落，每个里程碑首行改为与该表一致的“状态：”；`docs/README.md` 的状态段改为指向 progress.md。新增按文件名生成的 [证据索引](docs/evidence/README.md)。旧原文冻结在 `docs/history/2026-10-30-*.md`，只修正相对链接。
- **规则分层**：`AGENTS.md` 从 56 条压到 27 条通用不变量；单区域细则原样下沉到新增的 [ENGINEERING_RULES.md](docs/ENGINEERING_RULES.md)，按 Host 上游与进程、存储与结果、安全授权、Android、测试验收、本地环境六节组织。`agent.md`、`docs/ERRORS.md`、`docs/README.md` 的规则来源说明同步。
- **技术漂移订正**：PRD、ARCHITECTURE、CONTRIBUTING、README 不再把 FastAPI 写成现状，TEST_PLAN、ROADMAP、AGENTS 不再把 Room 写成现状（实际为标准库 HTTPS 与 `SQLiteOpenHelper`）；ARCHITECTURE 删除不存在的 `host/src/runvori/domain/`，改写为状态机/活动槽与验证规则的实际位置。定位文档只加 2026-10-31 注记，正文不动。README 首段改为当前事实并注明稳定阅读与真机尚未开始。
- **护栏**：新增 `host/tests/test_docs_consistency.py`（8 项），核对 ROADMAP 与 progress 的里程碑状态一致、ROADMAP 不含进展日志、progress.md ≤ 12 KB、AGENTS.md ≤ 30 条且指向细则文件、构建未依赖的组件（从 `pyproject.toml` 与 `build.gradle.kts` 推导）在活文档里只能以“未采用/替代方案”出现、文档引用的源码路径存在、相对链接可解析。先在结算前的文档上运行：`FAILED (failures=18, errors=2)`（FastAPI 现状描述 7 处、里程碑状态缺失 6 处、`domain/` 路径、progress 82706 字节、AGENTS 56 条、ROADMAP 进展日志各 1 处；2 个 error 为细则文件尚不存在）；修订后 8 项通过。链接检查另以人为改坏一个链接确认会变红。
- 验证：`python scripts/check.py` 16 份契约、446 项测试运行（443 通过、3 跳过）。未改 Host/Android 代码，Android JVM 单测本轮未重跑。

## Unreleased · DEV-010 真机地址配置与零模型基线 · 2026-10-30

- `scripts/accept_device.py` 将设备 serial 与 Host 端点分开：新增 `--host-address`（LAN/VPN IP 或 DNS 名）、`--listen-address`（显式本地绑定）和 `--host-port`（可选固定端口）；模拟器保留 `10.0.2.2` + 回环路径，物理设备必须显式给出可达地址。证书按端点写 IP/DNS SAN，客户端证书链、主机名和 SPKI 固定指纹校验均保持启用，没有关闭或降级分支。
- 新增 `--profile device-baseline` 零模型入口：合成上游 14 场景，`expected_model_turns=0`，并在运行前安装/核对 App APK 与 instrumentation APK 的 SHA-256、大小和版本，要求测试包已安装。物理设备报告只写 serial 摘要、设备型号/API/ABI 与端点 digest，不写原始地址。
- Android 离线缓存改为真实只读投影：Host 不可达时显示缓存会话与时间线，隐藏发送区，`startRun` 编程入口也拒绝写入；刷新/轮询持续重连，恢复后从缓存游标补拉。`openSession`、审批和结果入口在离线时不再以 `ConnectionNotReady` 逃出协程。
- 新增设备场景 `off`（离线缓存只读）、`rec`（Host 同端口同数据目录重启后游标恢复）、`fs`（系统字体 200% 下关键页面可滚动/可操作）；编排器在启动/驱动回合的场景前检查活动槽为空，并记录 Host 侧 `runs`/`approval_operations` 是否变化。
- 模拟器基线最终 `status=passed`：14 场景全绿、`expected_model_turns=0`、离线期间 run/决定计数不变、Host 重启 epoch 前进且端口相同、字体设置 2.0 后恢复。中间三次失败均为验收器/场景时序问题（`SelectionContainer` 滚动、文件审批无 command、进程恢复后过早点击），修复后重跑；失败报告保留在本机 `.dev`，未改写。
- **实际手机基线未执行**：本轮 `adb devices` 只有模拟器，因此没有真机证据；LAN/VPN/DNS 路由、防火墙、真实断网和 TalkBack 仍待验收。见 [DEV-010 证据](docs/evidence/DEV-010-device-baseline.md) 与 [脱敏报告](docs/evidence/DEV-010-device-baseline.json)。

## Unreleased · DEV-009 ra 复跑通过 · 2026-10-30

- 按单独授权复跑 `ra`，实际 1 个模型回合（`observed_runs=1`），未重试。总体 `status=passed`：手机场景通过，同一 run `interrupted`、中断操作 `confirmed/run_interrupted`、活动槽释放；审批 `expired`、允许/拒绝不可用、旧 `allow_once` 被 `approval_expired` 拒绝、`pending_approvals=0`、`approval_operations=0`。
- 修复后的分类器从事件历史确认回合终止失效（`approval_turn_terminal_observed=true`），并允许真实 Codex 随后关闭请求、把当前 `resolution_kind` 改写为 `request_closed`（`approval_late_closure_observed=true`）；最终审批仍为 `expired`，失效原因不因当前行被覆盖而丢失。
- 脱敏报告晋升为 [DEV-009-ra-rerun.json](docs/evidence/DEV-009-ra-rerun.json)；首次运行报告保留为 [DEV-009-first-run.json](docs/evidence/DEV-009-first-run.json)（总体 `failed`，不改写）；汇总见 [DEV-009 验收结果](docs/evidence/DEV-009-interrupt-approval-real.md)。原始数据库与保留目录仅留本机 `.dev`（`.dev/device-acceptance-g7y72qhq`）。
- 至此真实 `ri`（运行中中断）与 `ra`（等待审批时回合终止导致失效）均通过；下一项转向真机 HTTPS 连接与离席使用验收。仍不视为 AT-07 全部边界完成。

## Unreleased · DEV-009 首次真实 ri/ra 运行与证据分类修复 · 2026-10-30

- 按显式授权执行真实 `ri/ra`，实际使用 2 个模型回合（`ri` 1、`ra` 1），未重试、未追加。`ri` 通过：真实输出开始标记后中断，同一 run 为 `interrupted`，中断操作 `confirmed/run_interrupted`，活动槽释放，手机场景通过。
- `ra` 手机侧通过：旧审批 `expired`、允许/拒绝不可用、绕过 UI 的 `allow_once` 被 `approval_expired` 拒绝、缓存审批操作 0；Host 侧 run `interrupted`、中断操作 `confirmed/run_interrupted`、活动槽释放、`approval_operations=0`。真实 Codex 在回合终止使审批失效后又关闭上游请求，把当前 `resolution_kind` 从 `turn_terminal` 改写为 `request_closed`；首次分类器只接受最终 `turn_terminal`，因此判 `approval_not_invalidated` 并以 `real_scenario_failed:ra` 停止。
- 失败现场保留：报告 `.dev/dev009-interrupt-approval-real.json`（保持首次 `failed`，不追溯改写），晋升副本 [DEV-009-first-run.json](docs/evidence/DEV-009-first-run.json)，保留目录 `.dev/device-acceptance-ovkli1wr`；原始数据库与会话内容仅留本机。未自动重试。
- 修复证据分类：`interrupt_evidence` 同时读取审批当前行和事件历史，新增 `approval_turn_terminal_observed` / `approval_late_closure_observed`；`classify_approval_interrupt` 要求存在回合终止导致的 `expired` 事件，允许其后的 `request_closed` 覆盖当前原因，仍要求最终 `expired`、无待处理审批、`approval_operations=0`。回读保留现场，`ri/ra` 均得到 `passed`；这只用于定位，不替代复跑。
- 新增 2 项测试覆盖“回合终止后上游补发关闭”的合成顺序与分类；离线专项 19 项、全量 430 项测试（427 通过、3 项环境跳过）、16 份契约通过。`ra` 已按另行授权复跑通过，见上一条。见 [DEV-009 运行方案与首次运行记录](docs/evidence/DEV-009-interrupt-approval-plan.md)。

## Unreleased · DEV-009 真实中断与审批失效验收准备 · 2026-10-30

- 设备验收新增两个真实专用场景，各 1 个模型回合：`ri` 在执行中的真实命令输出 `RUNVORI_SLEEP_STARTED` 后由手机请求中断（命令上限 60 秒，开始信号超时 180 秒、终态超时 240 秒）；`ra` 保持审批页打开，通过产品中断入口结束回合并验证旧审批失效。本节记录运行前准备；首次真实运行结果见上一条。
- `ri` 的成功只认同一 `run_id` 的 `run.interrupted`、中断操作 `confirmed/run_interrupted` 与活动槽释放；自然完成记 `terminal_race_natural_completion`，RPC 返回或 HTTP 202 单独不算成功。`ra` 要求回合终止导致的审批 `expired` 可证实、绕过 UI 的决定被 `approval_expired` 拒绝、`approval_operations` 为 0；不以等待时长判定过期。首次真实运行暴露最终 `resolution_kind` 可能被上游关闭覆盖，分类已按事件历史修正。
- 审批页新增产品级“中断当前回合”入口（与时间线共用二次确认），补齐 UX 要求的“无法决定时仍可结束回合”。新增合成伴侣 `s3`（0 回合）在真实 APK + 合成上游上验证审批页中断、审批失效与旧决定拒绝；`h`、`d` 同时回归通过。
- 编排新增 Host 侧证据读取与分类：终态、中断操作 outcome、活动槽、审批状态/失效原因、审批操作计数、真实开始输出标记；失败即停止，不自动重试。`--dry-run --only ri,ra` 输出 `expected_model_turns=2`。
- 离线新增 17 项测试，覆盖触发超时、终态超时、终态竞争、未确认中断、活动槽未释放、审批仍待处理/失效原因错误/已记录决定、旧决定拒绝与回合计数。全量 428 项测试（425 通过、3 项环境跳过）、16 份契约、Android JVM 19 项通过；Android 仪器测试与 APK 构建通过。
- 运行方案：`.dev/dev009-interrupt-approval-real.json`（`--keep`，失败保留不重试）。见 [DEV-009 运行方案](docs/evidence/DEV-009-interrupt-approval-plan.md)。`ERR-038` 与真实 `v/r` 已从阻塞项移除。

## Unreleased · DEV-008 真实文件审批与验证匹配 · 2026-10-28

- 修复真实 Codex 文件 item 的绝对路径被无条件判为不安全：`approval_details.normalize_item_paths` 只把解析后位于该 run 登记工作区内的 path/move_path 转为相对 POSIX 路径；工作区外、UNC/设备前缀、`..`、根目录和无法解析的路径保持 `unsafe_change_path`；缺失 diff 记 `diff_missing`，两者都禁止 `allow_once`。不做旧审批回填。
- 文件 `item/updated` 与 `item/started`/`item/completed` 同等折叠；command/agent 的 updated 不当作完成。`record_approval_item`、`append_timeline_bound`、`item_has_text` 按 `process_epoch` 限定绑定，`approval_items` summary 带 `process_epoch`，防止 Host 重启后同号 thread/turn 命中旧 run。
- Android `TimelineItem.Approval` 保存 `approval_version`；审批页在 item 后到时按 version/status/detailsStatus 受控刷新，带在途/去重保护；审批页显示相对 `move_path`。
- 新增 `codex/command_match.py`：验证匹配只接受裸命令、ERR-007/ERR-016 观察到的三种完整 pwsh 包装，以及系统 Windows PowerShell 的完整形状；附加参数、其他 shell、危险展开和未知形状保持非通过。`run_results` 的验证与已批准命令映射共用该匹配。
- 设备验收新增 `l/m/v`（真实文件允许/拒绝/验证，独立提示词与只读 fixture）、`n`（合成审批先到 + 绝对路径 + `item/updated`）、`r`（Host 同端口/同数据目录/同证书重启，手机按捕获 run 与摘要读回）、`p`（一次性配对引导）。第三切片场景通过；加上生命周期专项后合成场景合计 22 个。`r` 只向设备传 run ID/摘要/大小，不传正文。
- 新增 Host 重启后产物字节/哈希一致性、绝对路径归一化、跨 epoch 隔离、`item/updated` 与包装正反例测试。411 项测试（408 通过、3 跳过）、Android JVM 19 项、16 份契约通过。
- 锁定 `0.164.0-alpha.11.1` 从 npm 恢复到隔离目录 `.dev/codex-0.164`，版本门与 `CODEX_HOME` 通过；按授权的 3 个真实回合执行 `l/m/v`。`l` 文件允许与 `m` 文件拒绝通过，Host 文件字节分别等于 AFTER/BEFORE；`v` 回合完成、命令退出 0，但验证结论因 Host 保存的包装串未识别而不是 `passed`（[ERR-038](docs/ERRORS.md)）。失败后停止，未重试；真实 `r` 未运行。
- 2026-10-30 修复 ERR-038：用隔离 `CODEX_HOME` 的 app-server `thread/read` 取回真实 `v` thread 的 item 原文（无模型回合），确认为 92 字节的 `"C:\\windows\\…\\powershell.exe" -Command 'python --version'`（双引号路径、反斜杠成对转义、单引号内层）；`command_match` 增加该精确形状，`..`、其他盘符/可执行文件、附加参数与未知形状仍拒绝。`--keep` 在 stderr 打印保留目录，新增仅限 `.dev` 的 `scripts/inspect_kept_commands.py` 核对 Host 原文。随后按再次授权的 1 个真实回合执行 `v,r`：`status=passed`、`observed_runs=1`、`command_match_asserted=true`，真实 `v` 结论为 `passed`，`r` 在同端口重启后 Host 与手机的 run/命令/产物摘要一致。报告见 [DEV-008-vr-real.json](docs/evidence/DEV-008-vr-real.json)，未重试。
- Android 生命周期恢复：`ViewModelProvider` 保留旋转状态；`NavigationRouteStore` 只持久化位置；打开审批时从 `operations` 缓存找回已提交请求，回执丢失只查询不重发；Host 重启/审批缺失时显示 stale 并移除决定按钮（[ERR-039](docs/ERRORS.md)）。
- 合成设备新增 `o/q/w/x/t/y/z/s2`：切后台、Activity 旋转、进程重建、回执丢失、Host 重启旧审批失效、系统 `am kill` 与 `force-stop` 分别记录、产物快速切换；全部通过，杀进程后 Host `runs`/`approval_operations` 计数不变。合成场景合计 22 个。见 [生命周期证据](docs/evidence/DEV-008-lifecycle-recovery.md)。

## Unreleased · DEV-008 受控产物与差异 · 2026-10-27

- 事件库 v17：新增 `run_artifacts` 与 `artifact_chunks`。命令输出在时间线同一事务内复制已持久化字节；时间线预算丢弃的字节只记 `dropped_bytes`，不从未保存内容补回。文件差异同时保存审批提案与上游报告完成两个来源，首次读取时另做 Git 工作区快照并固定 `attribution=unknown`。
- 新增 `GET /v1/runs/{run_id}/artifacts` 与 `GET /v1/artifacts/{artifact_id}/content?offset=...&limit=...`。内容端点返回 `application/octet-stream` 和 `X-Artifact-*` 元数据头；客户端只提交产物 ID，不接受本地路径。非法 ID、越界偏移和不可用产物分别返回 400/416/410。
- Android 结果页新增产物清单与详情子页：命令输出、审批提案、上游报告和 Git 快照分来源显示；分块按 `next_offset` 推进，空输出、截断、二进制、未跟踪文件和内容不可用都有明确文案。
- 合成设备验收扩到 12 个场景：`j` 完整文件审批允许并查看差异，`k` 拒绝后提案仍可查看，`b` 同时打开命令输出；全绿，`expected_model_turns=0`。
- 离线新增命令输出分块/哈希/重启一致性、截断丢弃量、审批双来源、Git `unknown` 归因、非法 ID/越界偏移测试。388 项测试（385 通过、3 跳过）、Android JVM 19 项、16 份契约通过。见 [DEV-008 第二切片证据](docs/evidence/DEV-008-artifacts.md)。
- 修复决定回执到达后、审批状态刷新前按钮短暂重新可用的竞态（[ERR-033](docs/ERRORS.md)）。未覆盖：真实 Codex 文件审批 `complete` 路径、真实大差异/二进制/重命名、回合前基线分离、`artifact.created` 事件与真机下载性能。
- 修复产物详情快速返回/重开时旧分块可能写入新页面的竞态（[ERR-034](docs/ERRORS.md)）：分块响应按请求代际和当前屏幕校验，过期响应丢弃。
- 2026-10-28 在当前工作区（含 22:06 的 Host 修改）重新构建 APK 并复跑 12 场景合成设备验收；修复 ERR-034 后再次复跑，两轮均全绿，脱敏报告已更新。

## Unreleased · DEV-008 命令详情与验证结果 · 2026-10-27

- 事件库 v16：新增 `run_commands`、`run_verifications`，`workspaces` 增加 `verification_json`。命令完成事件在聚合输出写入后落库；命令证据保存 cwd、上游起止时间、退出码、输出字节/丢弃字节与 `pending/complete/truncated/empty` 状态。必需验证命令在回合创建时快照，之后修改工作区不改写旧回合。
- 新增 `GET /v1/runs/{run_id}`、`/results` 与 `/commands`（命令 `item_id` 游标分页）。验证结果在读取时复用 `codex/verification.py` 的保守判定；`configured=false` 明确表示“未配置”，空配置不报通过，null 退出码不转 0，截断命令不匹配要求。
- Android 新增“回合结果”页：回合状态与验证结论分卡显示，验证项显示退出码是否存在、输出完整度和证据时间，命令列表支持分页。结果页从产品自己的 HTTPS API 取数，没有测试专用捷径。
- 合成设备验收新增 `i` 场景：`exit 7` 命令使回合 `completed`、验证 `failed`，结果页与命令列表同时显示退出码 7；`b` 同时断言验证 `passed`。完整十场景通过，`expected_model_turns=0`。
- 离线覆盖未配置、`not_run`、退出码缺失、输出截断、命令分页，以及同一数据目录启动真实 `HostRuntime` 后结果仍可查询。381 项测试（378 通过、3 跳过）、Android JVM 19 项、16 份契约通过。见 [DEV-008 证据](docs/evidence/DEV-008-results.md)。
- 第一切片时未覆盖产物/差异端点；第二切片已补，见上。仍未覆盖：`verification.updated` 事件、真实 Codex 命令包装的验证匹配、真机结果页与生命周期。合成编排的验证配置步骤曾把成功误标为 failed（[ERR-032](docs/ERRORS.md)，已修复）。

## Unreleased · DEV-007 Android 最小业务闭环 · 2026-10-27

- Host 新增 `GET /v1/sessions`（`session_id` 不透明游标、稳定分页，含标题、工作区别名、会话版本、快照序号、活动/最近回合与待审批计数）与 `GET /v1/runs/{run_id}/approvals`（按状态过滤，最新请求在前，空回合返回空列表）。
- 事件库 v15：新增 `run_timeline_budget`，把上游 item 生命周期投影为 `message.*` / `command.*` 时间线事件。写入按持久 `(thread_id, turn_id)` 绑定定位活动回合；只推进 `seq`，不改 `session_version`（否则持续输出会让待审批的期望版本立刻过期）；单回合文本 2 MiB、单块 8 KiB，耗尽后写 `text_omitted`/`dropped_bytes` 并追加一次 `timeline.truncated`，不静默丢弃。
- Android 新增 `data/`（Keystore AES-GCM 凭据信封、`X509ExtendedTrustManager`+SPKI 固定的 HTTPS、手工 HTTP/1.1、SQLite 缓存、会话同步）、`domain/`（配对解析、主机名匹配、严格 JSON、时间线投影）与 `ui/`（配对、会话列表、时间线、审批四屏 + ViewModel）。`allowBackup=false` 且备份/迁移规则排除凭据与缓存库。
- 新增 `scripts/accept_device.py` 与 instrumented `DeviceAcceptanceTest`（九个场景）：真实 APK → 固定指纹 HTTPS → 真实 `HostRuntime` → 合成 app-server，连续三轮全绿。Host 只监听 `127.0.0.1`，证书带 `IP:10.0.2.2` SAN。
- 真实 Codex 设备验收（同日，1 次模型回合）：真实 APK → 固定指纹 HTTPS → 真实 `HostRuntime` → 真实 app-server，跑通 `s/a/b/c/g/e`。真实模式使用独立场景集与 `ACCEPT_PROMPT`，不依赖合成标记或合成文本；`expected_model_turns=1`、`observed_runs=1`，未重试。`d/f/h`、真实文件审批与真实中断/过期仍未覆盖。见 [真实设备验收](docs/evidence/DEV-007-device-acceptance-real.md)。
- 删除 `DeviceRelay.kt`、`scripts/device_bridge.py`、`scripts/check_device_bridge.py` 及相关诊断脚本：实验证明模拟器 `10.0.2.2` 经自身 NAT 到达宿主机回环，**不需要** `adb reverse` 或防火墙放行。上一轮“需人工放行防火墙”的判断是错的，README/证据已更正。
- 修复四个产品缺陷：审批页期间存活轮询停摆；决定提交后从不回查操作状态（缓存永远停在 `accepted`）；TLS 握手在 worker 线程交错导致客户端 EOF（[ERR-029](docs/ERRORS.md)）；拒绝原因被随后的同步逻辑抹掉、横幅关闭只清一半（[ERR-030](docs/ERRORS.md)）。
- **DEV-007 状态：核心真实链路通过，剩余专项待验收。** 真实 Codex 设备验收仅覆盖 1 次核心闭环（见上条）；真实文件审批、真实中断/过期、旋转/杀进程/TalkBack/性能未覆盖（命令明细、结果页与产物已由后续 DEV-008 补齐）。
- 374 项测试运行（371 通过、3 项跳过）、Android JVM 单测 19 项通过、16 份契约通过。合成九场景见 [设备验收](docs/evidence/DEV-007-device-acceptance.md)，真实核心闭环见 [真实设备验收](docs/evidence/DEV-007-device-acceptance-real.md)。

## Unreleased · DEV-006 可通过 HTTPS 操作的最小 Host · 2026-10-27

- 事件库 v14：审批保存有界详情（命令、cwd、reason、item/时间；文件审批的路径与差异来自 item 事件）。详情缺失、截断、cwd 不在登记工作区内或类型非普通命令时 `details_status=insufficient`，`allow_once` 返回 `409 approval_details_insufficient`；v13 旧审批迁移为 `insufficient`，不人工回填。
- 新增标准库 HTTPS 服务（`runvori.api`）：TLS ≥1.2、SPKI 指纹、Bearer 令牌由服务端解析为 `device_id`、协议头先校验、配对交换限流（来源 5 次/分、全局 20 次/分，`Retry-After`）、16 并发与请求体/响应页上限。实现 `/v1/host`、快照、事件（含长轮询）、新回合、审批详情与决定、中断、操作查询；写请求 202 仅表示已持久受理。
- 新增 Windows 命名管道本地管理入口（`admin.key` + 固定命令白名单，全部经命令邮箱执行）与对应 CLI：`host-status / pairing-info / register-workspace / create-session / start-thread`；`revoke-device` 优先走运行中的 Host 并取消在途长轮询，Host 未运行时回落离线撤销并标注 `mode`。
- `HostRuntime` 增加 `--listen / --tls-cert / --tls-key / --host-name / --admin-pipe`；入口在上游启动后按“管道 → HTTPS”启动，任一失败整体回滚；事件循环结束和 `stop()` 都先停入口再关邮箱、隔离上游、关库。
- 新增 `scripts/probe_https_host.py` 独立 HTTPS 客户端闭环（15 步，含断线补拉、insufficient 禁止允许、撤销 401）与 `scripts/accept_https_host_real.py` 真实 Codex 验收；新增 `test_approval_details.py`、`test_api_handlers.py`、`test_https_host_flow.py`。
- 362 项测试运行（359 通过、3 项环境跳过），全量检查连续 3 次通过；16 份契约、离线 wheel 隔离安装与模块导入通过；真实 Codex `0.164.0-alpha.11.1` 显式运行 1 次通过。
- 限制见 [合成证据](docs/evidence/DEV-006-https-host.md) 与 [真实验收](docs/evidence/DEV-006-https-host-real.md)：会话列表、命令/产物明细、Android 联调未实现；AT-16 缺口未关闭。

## Unreleased · DEV-005 真实 Codex 与五切点验收 · 2026-10-26

- Host 启动前核验实际将启动的 Codex 绝对路径和精确版本；版本不符、不可执行、超时或输出异常在取得锁、打开数据库、创建配对库和推进 epoch 之前稳定拒绝。`--codex` 支持绝对路径，默认读取 `RUNVORI_CODEX`。
- 命令邮箱新增审批决定入口：决定经 SQLite 所有者持久化，只有 `accepted` 才进入一次派发队列；新增审批操作查询和 session thread 读取。`accepted` 不代表上游确认。
- 修复真实 Codex 新建 thread 在首回合前无法 `thread/resume`（`no rollout found`）：仅当前连接新建的 thread 跳过首次 resume，重启后仍必须成功 resume，见 [ERR-028](docs/ERRORS.md)。
- 在真实 HostRuntime 和真实 Codex 上完成 starting、running、等待审批、决定派发、中断五个崩溃切点；验证旧回合 interrupted、审批 expired/unknown、操作 unknown/dispatch_unconfirmed、旧审批不可重用、不自动重跑，以及 launcher、测试 relay、真实 Codex 及观测后代的 PID＋创建时间隔离。共 5 次真实模型回合；另 1 次失败联调在首回合前被 ERR-028 拦截，未进入模型回合。修复后每切点 1 次通过，未自动重试。
- 336 项测试运行（333 通过、3 项跳过：2 项环境限制、1 项真实测试未启用），16 份契约与离线 wheel 隔离安装通过。AT-16 仍未整体通过；限制见 [真实 Codex 五切点](docs/evidence/DEV-005-host-runtime-restart-real-codex.md)。

## Unreleased · DEV-005 产品 Host 启动与崩溃重启闭环 · 2026-10-26

- 事件库 v13 增加 `upstream_epochs`：每个 epoch 显式记录 `unlaunched`／`recorded`／`legacy_unknown`；恢复门禁不再把缺少身份记录当作已隔离，旧库失败关闭并按归档重建迁移。
- 新增 `HostRuntime` 与 `python -m runvori.host`，固定组合单实例锁、状态对账、统一恢复、写门禁、命令邮箱、设备访问和唯一上游；正常停止使用 `host_shutdown`，上游故障恢复后进程退出等待外部重启。
- 真实 Host 子进程在 starting、running、等待审批和中断切点完成崩溃重启测试，验证 launcher／上游／后代真实 PID 的 Job 隔离、旧回合 interrupted、审批失效和不自动重跑。
- 修复测试后代继承协议管道掩盖上游 EOF 的问题（[ERR-027](docs/ERRORS.md)）；后续真实 Codex 测试已观测到 `codex-code-mode-host.exe` 与 `conhost.exe` 后代，但正常退出 EOF 场景仍未覆盖。
- 317 项测试运行（315 通过、2 项环境限制跳过），16 份契约与离线 wheel 隔离安装通过。
- 验证和限制见 [记录](docs/evidence/DEV-005-host-runtime-restart.md)。

## Unreleased · DEV-005 上游进程身份与启动隔离门 · 2026-10-26

- 事件库 v12 在目标进程放行前持久记录启动器 PID＋创建时间；恢复入口删除调用方声明的 `old_process_isolated` 布尔值，改为按记录核实，存活或无法查询时拒绝恢复并保持写门禁关闭。
- 修复 TLS 回环探针发送后立即关闭导致的间歇丢包（同一轮验证中发现，见 [ERR-026](docs/ERRORS.md)）。
- 294 项测试运行（292 通过、2 项环境限制跳过），16 份契约与离线 wheel 安装通过。
- 验证和限制见 [记录](docs/evidence/DEV-005-upstream-process-identity.md)。

## Unreleased · DEV-005 内部事件等待取消 · 2026-10-26

- 内部事件等待支持可信调用方取消：唤醒等待线程，跳过已取消的排队读取，读取完成后仍检查取消状态。
- 259 项测试运行（254 通过、5 项环境限制跳过），16 份契约与离线 wheel 安装通过。
- 验证和限制见 [记录](docs/evidence/DEV-005-event-wait-cancel.md)。

## Unreleased · DEV-005 已提交事件等待通知 · 2026-10-26

- 内部邮箱用条件变量和变更代次提示增量读取；已提交写入及归属连接事件交接后唤醒等待线程，数据库复查仍决定结果，定时复查兜底未接入路径。
- 256 项测试运行（251 通过、5 项环境限制跳过），16 份契约与离线 wheel 安装通过。
- 验证和限制见 [记录](docs/evidence/DEV-005-event-notification.md)。

## Unreleased · DEV-005 内部增量事件等待 · 2026-10-26

- 命令邮箱新增请求线程上的有界增量事件等待；持续输出时返回已提交事件，空超时保留游标，SQLite 所有者线程继续处理其他工作。
- 255 项测试运行（250 通过、5 项环境限制跳过），16 份契约与离线 wheel 安装通过。
- 验证和限制见 [记录](docs/evidence/DEV-005-mailbox-event-wait.md)。

## Unreleased · DEV-005 快照最近事件同事务读取 · 2026-10-26

- 事件库新增单读事务的快照尾页，返回快照序号、最近事件及历史排他游标，并对快照和事件合计执行 256 KiB 上限；内部命令邮箱可读取。
- 253 项测试运行（248 通过、5 项环境限制跳过），16 份契约与离线 wheel 安装通过。
- 验证和限制见 [记录](docs/evidence/DEV-005-snapshot-tail.md)。

## Unreleased · DEV-005 命令邮箱读取侧 · 2026-10-26

- 内部命令邮箱新增单会话原始快照与增量/历史事件页读取，SQLite 所有者沿用现有读事务和游标边界；快照序号可用于补读后续事件。
- 251 项测试运行（246 通过、5 项环境限制跳过），16 份契约与离线 wheel 安装通过。
- 验证和限制见 [记录](docs/evidence/DEV-005-mailbox-reads.md)。

## Unreleased · DEV-005 Host 内部命令邮箱 · 2026-10-26

- 加入有界跨线程命令邮箱，SQLite 所有者串行执行回合启动、中断和操作查询，持久受理后唤醒既有派发队列；超时不伪装成未提交，队列满不伪装成已派发。
- 250 项测试运行（245 通过、5 项环境限制跳过），16 份契约与离线 wheel 安装通过。
- 验证和限制见 [记录](docs/evidence/DEV-005-command-mailbox.md)。

## Unreleased · DEV-005 已领取启动的中断意图 · 2026-10-26

- `turn/start` 已领取但 turn 尚未绑定时持久受理中断；启动响应确认后保持 `interrupting`，由当前连接所有者按真实绑定派发一次。响应丢失时隔离恢复，不推断中断已发出。
- 246 项测试运行（241 通过、5 项环境限制跳过），16 份契约与离线 wheel 安装通过。
- 验证和限制见 [记录](docs/evidence/DEV-005-claimed-start-interrupt.md)。

## Unreleased · DEV-005 未派发回合取消 · 2026-10-26

- `starting` 回合尚未领取 `turn/start` 发送权时，持久中断请求在同一事务中终结回合、确认取消并释放活动槽；已领取发送权的回合继续等待上游绑定或隔离恢复。
- 243 项测试运行（238 通过、5 项环境限制跳过），16 份契约与离线 wheel 安装通过。
- 验证和限制见 [记录](docs/evidence/DEV-005-unstarted-cancel.md)。

## Unreleased · DEV-005 中断请求归属派发 · 2026-10-25

- 已受理中断由当前连接所有者从持久操作和绑定读取 thread/turn，一次领取后调用 `turn/interrupt`；正常 RPC 响应等待独立终态，显式 RPC 错误继续消费连接，传输失效隔离恢复。
- 240 项测试运行（235 通过、5 项环境限制跳过），16 份契约与离线 wheel 安装通过。
- 验证和限制见 [记录](docs/evidence/DEV-005-owned-interrupt.md)。

## Unreleased · DEV-005 中断请求持久边界 · 2026-10-25

- 事件库 v10 新增中断操作表，已绑定回合的中断受理与状态、事件、快照同事务；按上游 epoch 一次领取派发权，终态和隔离恢复按证据结算，未知结果不自动重发。
- 233 项测试运行（228 通过、5 项环境限制跳过），16 份契约与离线 wheel 安装通过。
- 验证和限制见 [记录](docs/evidence/DEV-005-interrupt-journal.md)。

## Unreleased · DEV-005 已受理回合归属调度 · 2026-10-25

- 增加有界回合启动队列，在当前连接所有者线程核对持久受理操作与输入摘要，依次执行历史 thread 身份核对、一次 `turn/start` 和上游事件交接；不确定结果隔离恢复，旧队列不重发。
- 225 项测试运行（220 通过、5 项环境限制跳过），16 份契约与离线 wheel 安装通过。
- 验证和限制见 [记录](docs/evidence/DEV-005-owned-run-start.md)。

## Unreleased · DEV-005 本地 thread 创建归属调度 · 2026-10-25

- 增加只接受本地操作者标识的有界 `thread/start` 信号队列，由当前连接所有者串行执行已有一次创建适配器；重复已确认请求复用绑定，响应丢失时隔离并恢复。
- 219 项测试运行（214 通过、5 项环境限制跳过），16 份契约与离线 wheel 安装通过。
- 验证和限制见 [记录](docs/evidence/DEV-005-owned-thread-start.md)。

## Unreleased · DEV-005 归属连接审批串行派发 · 2026-10-25

- 增加只携带操作标识的有界审批队列；持续事件循环在同一 Host 线程读取持久决定、领取一次发送权并答复上游。重复队列项不重发，派发不确定时隔离并恢复旧连接。
- 215 项测试运行（210 通过、5 项环境限制跳过），16 份契约与离线 wheel 安装通过。
- 验证和限制见 [记录](docs/evidence/DEV-005-owned-approval-scheduling.md)。

## Unreleased · DEV-005 归属连接持续事件循环 · 2026-10-25

- 在持锁 Host 线程中持续轮询已有单步事件交接；空等待继续、停止信号退出、故障恢复后停止旧连接，并提供有界交接数供验证。
- 211 项测试运行（206 通过、5 项环境限制跳过），16 份契约与离线 wheel 安装通过。
- 验证和限制见 [记录](docs/evidence/DEV-005-owned-event-loop.md)。

## Unreleased · DEV-005 归属连接单步事件泵 · 2026-10-25

- 在当前 Host 归属连接上逐条读取上游消息并交给现有持久化映射；读取或落库失败时隔离连接并执行统一恢复，恢复事务失败保持写门控关闭。空等待不改变状态。
- 206 项测试运行（201 通过、5 项环境限制跳过），16 份契约与离线 wheel 安装通过。见 [记录](docs/evidence/DEV-005-owned-event-pump.md)。

## Unreleased · DEV-005 上游连接唯一归属 · 2026-10-25

- 持锁且已恢复的 Host 在当前 epoch 预留唯一连接位，再启动并初始化 Windows 上游；重复启动、伪造归属及过时代次拒绝。初始化失败在确认隔离后释放连接位，Host 关闭前隔离仍归属的连接；退出恢复只接受该归属对象。
- 201 项测试运行（196 通过、5 项环境限制跳过），16 份契约与离线 wheel 安装通过。见 [记录](docs/evidence/DEV-005-upstream-single-owner.md)。

## Unreleased · DEV-005 当前受管上游退出恢复 · 2026-10-25

- 当前 Host 持有的 Windows stdio 连接退出时，先关闭写门控，再关闭受管进程和 Job，核对父进程与句柄后读取活动回合并执行 `upstream_exit` 恢复；关闭失败保持写门控关闭。
- 198 项测试运行（193 通过、5 项环境限制跳过），16 份契约与离线 wheel 安装通过。见 [记录](docs/evidence/DEV-005-owned-upstream-exit.md)。

## Unreleased · DEV-005 本地工作区登记与创建归属 · 2026-10-25

- 事件库 schema v9 登记不可改写的本地工作区根目录与沙箱策略；新会话及工作区归属同事务提交。`thread/start` 从登记记录读取路径与策略，发送前复查目录，旧会话升级不猜测归属。
- 195 项测试运行（190 通过、5 项环境限制跳过），16 份契约与离线 wheel 安装通过。见 [记录](docs/evidence/DEV-005-local-workspace-registration.md)。

## Unreleased · DEV-005 上游 thread 一次创建 · 2026-10-25

- 事件库 schema v8 持久记录创建意图、调用方请求与固定参数摘要；一次领取后发送 `thread/start`，有效响应与不可改写 session/thread 绑定同事务提交。响应丢失、绑定失败和重启后的未确认创建保持未知且不自动重发。
- 191 项测试运行（186 通过、5 项环境限制跳过），16 份契约与离线 wheel 安装通过。见 [记录](docs/evidence/DEV-005-thread-start-once.md)。

## Unreleased · DEV-005 历史 thread 恢复身份核对 · 2026-10-25

- 持锁统一恢复后按 session/thread 绑定调用 `thread/resume` 并核对响应身份；失败关闭上游连接，不改绑。新回合派发须先在当前连接完成核对，失败不领取 `turn/start` 发送权。
- 184 项测试运行（179 通过、5 项环境限制跳过），16 份契约与离线 wheel 安装通过。见 [记录](docs/evidence/DEV-005-thread-resume-identity.md)。

## Unreleased · DEV-005 会话与上游 thread 持久归属 · 2026-10-25

- 事件库 schema v7 增加不可改写的 session/thread 绑定；同一 thread 不能归属两个会话，新回合派发从数据库取绑定，未绑定拒绝发送。旧库升级不猜测归属，补记时核对历史 turn。
- 180 项测试运行（175 通过、5 项环境限制跳过），16 份契约与离线 wheel 安装通过。见 [记录](docs/evidence/DEV-005-session-thread-binding.md)。

## Unreleased · DEV-005 新回合 stdio 一次派发 · 2026-10-25

- 对已受理新回合重放输入摘要校验，先领取发送权再调用 `turn/start`，识别响应后绑定 thread/turn；响应或绑定失败关闭上游并记未知，重复调用不重发。
- 175 项测试运行（170 通过、5 项环境限制跳过），16 份契约与离线 wheel 安装通过。见 [记录](docs/evidence/DEV-005-run-stdio-dispatch.md)。

## Unreleased · DEV-005 审批 stdio 一次派发 · 2026-10-25

- 从持久审批记录读取上游身份和决定，先领取再答复 stdio；写入成功仍等待独立结束证据，发送异常关闭连接并记未知，禁止重发。
- 171 项测试运行（166 通过、5 项环境限制跳过），16 份契约与离线 wheel 安装通过。见 [记录](docs/evidence/DEV-005-approval-stdio-dispatch.md)。

## Unreleased · DEV-005 上游单消息持久交接 · 2026-10-25

- 新增有界单消息入口，串接审批请求、请求结束和回合终态适配；未知控制请求拒绝，读取或持久化失败时关闭上游连接。修正非唯一键数据库错误被误报为重复请求的问题。
- 165 项测试运行（160 通过、5 项环境限制跳过），16 份契约与离线 wheel 安装通过。见 [记录](docs/evidence/DEV-005-event-handoff.md)。

## Unreleased · DEV-005 回合终态审批收尾 · 2026-10-25

- 上游回合终态事务同步处理未完成审批：未派发的失效，已领取未确认的转未知；多条审批、操作、回合终态、事件、快照及活动槽同事务提交。晚到的请求结束通知可补记证据，不重新启用审批。
- 161 项测试运行（156 通过、5 项环境限制跳过），16 份契约与离线 wheel 安装通过。见 [记录](docs/evidence/DEV-005-turn-terminal-approval-settlement.md)。

## Unreleased · DEV-005 上游回合终态身份映射 · 2026-10-25

- 新增 `turn/completed` 适配和持锁单库事务：按当前 epoch、thread、turn 定位回合，写真实终态并释放活动槽；重复通知幂等、冲突终态拒绝，事件失败回滚。
- 160 项测试运行（155 通过、5 项环境限制跳过），16 份契约与离线 wheel 安装通过。见 [记录](docs/evidence/DEV-005-upstream-turn-terminal.md)。

## Unreleased · DEV-005 上游审批请求身份映射 · 2026-10-25

- 新增命令与文件变更审批请求适配：类型化 JSON-RPC 请求号、thread/turn/epoch 绑定和完整参数摘要在单库事务中生成审批事件；相同请求重复到达返回原审批，身份相同但内容变化拒绝。
- 旧审批库自动增加可空摘要列，事件库 schema 升至 v6；157 项测试运行（152 通过、5 项环境限制跳过），16 份契约及离线 wheel 安装通过。见 [记录](docs/evidence/DEV-005-upstream-approval-request.md)。

## Unreleased · DEV-005 上游审批结束身份映射 · 2026-10-25

- 新增 `serverRequest/resolved` 适配切片：保留 JSON-RPC 整数/字符串请求号的类型，按 thread、epoch 与已绑定回合从单库查找审批，再记录请求结束；错误身份不改状态。
- 合成 stdio 事件到单库的联动、重复通知和错误形状覆盖；155 项测试运行（150 通过、5 项环境限制跳过），16 份契约通过。见 [记录](docs/evidence/DEV-005-upstream-approval-resolved.md)。

## Unreleased · DEV-005 共享库恢复前写门控 · 2026-10-25

- 共享审批库拒绝旧独立探针的写方法，事件库公开写方法在统一恢复成功前拒绝；恢复尝试失败后保持写入口关闭。共享库移除可单独推进审批 epoch 的公开入口，避免只恢复审批而留下旧活动槽。
- 跨重开响应重放、故障恢复重试、旧独立探针兼容和现有事务测试通过；152 项测试运行（147 通过、5 项环境限制跳过），16 份契约通过。见 [记录](docs/evidence/DEV-005-startup-write-gate.md)。

## Unreleased · DEV-005 持锁启动原子恢复切片 · 2026-10-25

- 新增单库启动恢复入口：在旧进程隔离前提下，按旧 epoch、活动回合 ID/版本核对，原子处理审批与操作、推进 epoch、中断旧回合并释放活动槽；任何一步失败全部回滚。
- 空库启动、错误版本、跨重开、回合中断事件失败及原有独立恢复行为均有测试；152 项测试运行（147 通过、5 项环境限制跳过），16 份契约通过。见 [记录](docs/evidence/DEV-005-host-atomic-recovery.md)。

## Unreleased · DEV-005 单库审批请求结束切片 · 2026-10-25

- 新增按 epoch、上游请求标识与回合绑定核对的关闭记录：待决定/未领取审批失效，已领取审批只记录请求结束；审批、操作、事件、快照同事务更新。请求结束与命令副作用保持不同结果语义。
- 覆盖回合终态后关闭、重复通知、错误身份、已领取与未知状态、第二事件失败回滚；149 项测试运行（144 通过、5 项环境限制跳过），16 份契约通过。见 [记录](docs/evidence/DEV-005-approval-request-closed.md)。

## Unreleased · DEV-005 审批隔离后 epoch 恢复切片 · 2026-10-25

- 持锁单库路径新增隔离后的审批 epoch 恢复事务：待决定和未领取的审批失效，未领取操作记未派发，已领取操作与审批记未知；逐项写事件和快照，最后同事务推进 epoch。
- 覆盖空库启动、未隔离拒绝、多审批中途写入失败回滚、跨重开及后续旧回合中断；145 项测试运行（140 通过、5 项环境限制跳过），16 份契约通过。见 [记录](docs/evidence/DEV-005-approval-epoch-recovery.md)。

## Unreleased · DEV-005 单库审批派发领取切片 · 2026-10-25

- 将已受理审批的领取与 `dispatching` 操作、两条事件、快照和版本一起提交；领取只成功一次。发送结果不确定时可持久记 `unknown`，并拒绝重新领取。
- 覆盖重开后重复领取、旧 epoch 拒绝及两种状态推进的第二事件失败回滚；142 项测试运行（137 通过、5 项环境限制跳过），16 份契约通过。见 [记录](docs/evidence/DEV-005-approval-dispatch.md)。

## Unreleased · DEV-005 单库审批决定事务切片 · 2026-10-25

- 事件库 schema 升至 v5，加入按 actor 和请求号查询的审批操作记录；审批决定、操作、`approval.updated`/`operation.updated` 与快照一次提交。相同请求跨重开返回原操作，冲突决定和跨类型请求号被拒绝。
- 覆盖响应丢失、版本与 epoch 失效、第二条事件失败时完整回滚；138 项测试运行（133 通过、5 项环境限制跳过），16 份契约通过。见 [记录](docs/evidence/DEV-005-approval-decision.md)。

## Unreleased · DEV-005 单库审批请求事务切片 · 2026-10-25

- 持锁启动改用单个 `host.db` 连接；旧双库探针文件存在时明确拒绝，避免静默丢失状态。
- 在同一事务中写审批请求、事件、回合和快照，故障注入验证回滚；134 项测试运行（129 通过、5 项环境限制跳过），16 份契约通过。见 [记录](docs/evidence/DEV-005-unified-store.md)。

## Unreleased · DEV-005 双库存储持锁启动切片 · 2026-10-25

- 新增一个 Windows 单实例锁下顺序打开审批与事件库的启动切片；争锁不打开数据库，第二库失败后关闭第一库并释放锁，进程被终止后重开不自动恢复旧状态。
- 131 项测试运行（126 通过、5 项环境限制跳过），16 份契约通过；两库跨库事务仍待产品集成处理，见 [记录](docs/evidence/DEV-005-locked-startup.md)。

## Unreleased · DEV-005 旧回合隔离后恢复事务 · 2026-10-25

- 新增按回合 ID、版本和旧进程代次核对的恢复事务；仅在调用方声明旧进程已隔离后记录中断并释放匹配活动槽，按派发阶段保留操作的失败/未知/已确认事实。
- 覆盖未隔离拒绝、重复恢复、错代次、回滚与旧回合隔离；128 项测试运行（123 通过、5 项环境限制跳过），16 份契约通过。见 [记录](docs/evidence/DEV-005-run-recovery.md)。

## Unreleased · DEV-005 上游回合确认持久切片 · 2026-10-25

- 事件库升级为 schema v4；将派发进程代次与上游 thread/turn 关联，匹配确认时原子记录 `run.started`、`operation.updated`、运行中回合和已确认操作。
- 覆盖迟到证据、重复通知、错代次、冲突归因和两事件回滚；122 项测试运行（117 通过、5 项环境限制跳过），16 份契约通过。见 [记录](docs/evidence/DEV-005-turn-confirmation.md)。

## Unreleased · DEV-005 新回合单次派发领取 · 2026-10-25

- 新增操作 `accepted → dispatching → unknown` 的事务边界，派发前先持久领取；未知结果不可重领且保留活动槽。
- 覆盖双连接领取竞争、跨重开、领取与未知标记的写入失败回滚及终态拒绝；115 项测试运行（110 通过、5 项环境限制跳过），16 份契约通过。见 [记录](docs/evidence/DEV-005-run-dispatch.md)。

## Unreleased · DEV-005 新回合请求幂等切片 · 2026-10-25

- 事件库升级为 schema v3，在同一事务中持久受理请求并预留活动槽；同一 actor 和请求号的相同请求返回原操作，不同内容拒绝。
- 覆盖响应丢失重试、并发重放、失败回滚、UTF-8 输入上限及 v2 数据升级；110 项测试运行（105 通过、5 项环境限制跳过），16 份契约通过。见 [记录](docs/evidence/DEV-005-run-idempotency.md)。

## Unreleased · DEV-005 全局活动槽事务切片 · 2026-10-25

- 事件库升级为 schema v2；事务内预留唯一活动回合并写创建事件和快照，终态事务只释放匹配的回合，重复终态幂等。
- 验证并发竞争、失败回滚、旧终态隔离和 v1 数据迁移；104 项测试运行（99 通过、5 项环境限制跳过），16 份契约通过。证据见 [记录](docs/evidence/DEV-005-active-slot.md)。

## Unreleased · DEV-005 事件与快照事务切片 · 2026-10-25

- 新增独立 SQLite 存储切片，将会话事件和快照以连续序号原子提交，提供有界增量及历史分页；并发、回滚和游标边界有行为测试。
- 当前仅是 M1 存储前提，M0 仍未退出，Host API、状态机与 Android 同步仍待实现；验证见 [记录](docs/evidence/DEV-005-event-journal.md)。

## Unreleased · M0/Q-05 HTTPS 配对联动探针 · 2026-10-24

- 新增仅限本机回环的 HTTPS 配对夹具：先验证证书链、主机名与本地 SPKI，再通过 POST body 兑换一次性秘密；错误指纹不会发出 HTTP 请求。
- 注入数据库已提交但 HTTP 响应丢失的切点，确认秘密重放失败，本地撤销后可重新配对；获准环境下 91 项测试运行（89 通过、2 项符号链接创建权限限制跳过），16 份契约通过。

## Unreleased · M0/Q-05 一次性配对记录 · 2026-10-24

- 新增独立 SQLite 配对切片：32 字节随机秘密和设备令牌只存摘要，5 分钟到期，事务内一次性消费与唯一有效设备；测试覆盖并发兑换、重放、过期及本地撤销。
- 获准环境下 90 项测试运行（88 通过、2 项符号链接创建权限限制跳过），16 份契约及当前 wheel 安装通过；API、限流和 Android 凭据存储仍待实现。

## Unreleased · M0/Q-05 回环 TLS 身份探针 · 2026-10-24

- 新增一次性证书与真实回环 TLS 握手探针：保持链和主机名校验后核对本地 SPKI 指纹；换公钥、错误主机名与未信任证书均拒绝发送应用字节。
- 获准环境下 86 项测试运行（84 通过、2 项符号链接创建权限限制跳过），16 份契约通过；产品配对、Android 专用信任与轮换尚未实现。

## Unreleased · M0/Q-04 持锁 SQLite 启动切片 · 2026-10-24

- 新增先获取 Windows 单实例锁再打开审批 SQLite 的最小启动切片；竞争失败不打开数据库，建库失败释放锁，进程异常退出后重开不自动推进审批 epoch。
- 完整离线检查运行 85 项（83 通过、2 项符号链接创建权限限制跳过）、16 份契约通过；当前 wheel 隔离安装包含新模块。

## Unreleased · M0/Q-04 Windows 单实例锁 · 2026-10-24

- 新增 Windows 文件句柄单实例锁切片，跨进程验证竞争失败、正常关闭和持锁进程异常退出后重取；当前 wheel 的隔离安装再次通过。
- 完整离线检查运行 82 项（80 通过、2 项符号链接创建权限限制跳过），16 份契约通过；产品 Host 启动集成仍待 M1。

## Unreleased · DEV-001/Q-04 Host 包复验 · 2026-10-24

- 新增 `scripts/smoke_package.ps1`，离线构建当前 Host 探针 wheel，在一次性虚拟环境安装并从仓库外执行已安装 CLI；16 份契约及新增模块导入通过。
- 明确旧 `pip --target` 检查不能证明命令入口可独立运行；改用真实虚拟环境作为包安装证据，仍不声明产品 Host 可部署。

## Unreleased · DEV-003 Windows junction 路径复验 · 2026-10-24

- 新增一次性 Windows junction 集成回归，验证 Git 产物读取和未知审批文件核对均拒绝指向工作区外的目录链接；80 项测试运行（78 通过、2 项符号链接创建权限限制跳过），16 份契约校验通过。

## Unreleased · DEV-004 Android 构建基线 · 2026-10-24

- 使用现有 API 37 AVD 的只读实例完成占位 APK 安装、冷启动、进程及前台 Activity 核对，保存屏幕证据；新增固定包名的 `scripts/smoke_android.ps1` 供复验。
- 增加单应用 Android Compose 占位工程、固定依赖版本及带校验和的 Gradle 8.13 wrapper；本机生成 Debug APK，核对包名、SDK 声明和文件哈希。
- 增加工具链、构建/安装步骤和限制证据；实机安装、业务客户端、最低 API 决策与干净环境复现仍待验证。

## Unreleased · DEV-003 传输切片 · 2026-10-23～24

- 增加固定二进制命令写入探针：真实单次命令审批与退出码 0 后，核对 2 KiB 文件字节、Git 二进制标记和元信息-only 产物；扩展已审查的双引号 PowerShell 包装精确匹配。
- 新增固定文件移动单次允许探针：完整补丁与路径白名单后真实审批一次，核对上游 item、源文件缺失、目标字节和 Git 状态；移动场景不再将无关 `probe-result.txt` 报为 `file_match=false`。
- 增加只拒绝的 Codex 文件移动审批探针：真实观察 `update`/`move_path` 文件改动项、单次文件审批与拒绝后原文件未变；修复干净 Git 工作区空状态误判。
- 增加 Git 重命名证据探针：区分已暂存移动的 `R100` 与未暂存移动的删除加未跟踪新增，保持未知归因并拒绝篡改的夹具。
- 收紧 SQLite 审批未知结果的文件核对：先检查状态，再限定工作区和相对路径，拒绝越界与链接路径，读取按预期长度有界；补对应回归。
- 增加本地 Git 差异产物探针：预先存在的 128 KiB 文本改动只报大小/哈希和截断，2 KiB 二进制改动只报元信息；扫描超过 4 MiB 时哈希不可用，来源和归因保守标注。73 项测试运行（72 通过、1 项符号链接环境限制跳过），16 份契约校验通过。
- 新增固定非零验证命令探针：真实 `exit 7` 获单次审批，命令项退出码精确为 7，回合 `completed` 与验证汇总 `failed` 同时成立；70 项测试和 16 份契约校验通过。
- 新增多项必需验证命令汇总和重复上游 item 检测；两条固定只读命令在真实回合中各自获一次审批、退出码 0，逐项和汇总为 `passed`。修正回合仍运行时已确认非零退出码未及时使汇总失败的问题；混合状态由行为测试覆盖。
- 新增单条预登记验证命令的结构化事件关联与保守状态判定；真实固定只读命令的开始/完成通知和退出码 0 支持 `passed`。新增两份 item 通知契约，61 项测试及 16 份 schema 校验通过。
- 增加脏工作区探针：预先修改的跟踪文件和未跟踪文件保持原样，仅对精确新增文件发送一次审批；真实回合、文件字节和 Git 状态均核对。普通 Git diff 遗漏未跟踪文件，差异来源须明确标注。
- 本机 Codex CLI 升级时版本门控先拦截真实探针；审查 14 份 schema 后锁定 `0.164.0-alpha.11.1`，重导出清单。新版本仅完成本轮脏工作区行为探针、隔离握手及 53 项离线测试，历史实测不自动继承。
- stdio 客户端可保留命令/文件审批请求，并由调用方显式发送一次性允许或拒绝；不提供会话级授权。
- 新增审批与回合响应不同先后顺序的合成测试；22 项离线测试和真实隔离握手通过。
- 新增显式启用的受限执行探针、可丢弃仓库准备入口及脱敏结构报告；先观察真实命令审批拒绝路径，随后补充只读命令单次允许路径。
- 收到上游 `serverRequest/resolved` 后使未回复请求失效；新增请求先结束的行为测试，当前 25 项离线测试通过。
- 增加只读命令单次允许白名单与实际决定记录；真实命令审批 `accept` 后对应 item 退出码 0，脱敏夹具已保存，当前 27 项测试通过。
- 增加固定写入场景；真实命令审批后目标文件 16 字节完全匹配、退出码 0，新增脱敏夹具，当前 28 项测试通过。
- 增加主动中断场景；固定命令输出标记后发送真实 `turn/interrupt`，观察到 `interrupted` 回合终态，新增脱敏夹具，该阶段 30 项测试通过。
- 增加待审批中断场景：固定只读命令审批保持未答复，真实中断终态后才收到对应 `serverRequest/resolved`；补终态后有界观察和非白名单请求失败关闭，保存脱敏夹具；14 份 schema 校验与 34 项测试通过。
- 增加审批后立即中断场景：固定长命令获单次允许后立即发 `turn/interrupt`，真实终态为 `interrupted`，命令输出和退出码未观察到；第二个审批请求失败关闭，保存脱敏夹具，36 项测试通过。
- 增加只读沙箱文件修改拒绝探针：两次真实运行均观察到独立文件修改审批，拒绝后目标文件不存在；按类型记录 `grantRoot`，保存脱敏夹具，37 项测试通过。
- 增加精确文件改动允许探针：关联 item 与审批请求，核对唯一目标、类型和完整内容后发送一次 `accept`；真实 item 与回合完成，目标文件 17 字节完全匹配，保存脱敏夹具，39 项测试通过。
- 增加审批答复后确认丢失的双切点合成 peer：模拟副作用发生前后都不发送请求结束通知，传输层均禁止再次答复；保存脱敏夹具，40 项测试通过。
- 增加真实文件修改审批答复后的受控进程切断探针：发送一次允许后关闭 app-server，未观察到 item 或回合终态，目标文件不存在，结果保持未知；保存脱敏夹具，41 项测试及 14 份 schema 校验通过。
- 增加独立 SQLite 审批派发验证切片：决定先记录、派发前领取，新 epoch 将旧已领取决定保留为未知且不重发；只读文件核对与请求结束分开记录。47 项测试及 14 份 schema 校验通过，Host 集成仍待 M1。
- 增加真实完成后迟到中断探针：固定只读命令退出码 0、回合先 `completed`，随后 `turn/interrupt` 返回脱敏 RPC 错误；保留完成终态并记录未尝试 thread 恢复，49 项测试及 14 份 schema 校验通过。
- 修复匹配的 RPC 错误误使 stdio 传输失效；仍不重试该 RPC，允许继续读取终态。新增命令 item 完成后的立即、1 秒和 5 秒中断切点，真实观察到 `interrupted` 与 `completed` 两种结局；保存脱敏证据，52 项测试及 14 份 schema 校验通过。
- 其余审批决定与中断时序、真实完成优先的终态竞争、审批持久化及真实 Codex 确认丢失仍待 DEV-003 后续验证。

## Unreleased · DEV-002 · 2026-10-23

- Windows 使用 kill-on-close Job 管理后代生命周期，启动门控消除目标启动与归入 Job 之间的竞争；失败时禁止降级运行。
- 修复直接子进程退出后跳过后代清理的问题，删除 Windows taskkill 回退。
- 新增四项本机进程树集成测试；全量 20 项测试及真实隔离握手通过。
- 更新进度、ADR、开发规则和错误记录；M0 尚未完成。

## Unreleased · DEV-001 · 2026-10-23

- 新增 Python M0 验证工具、隔离 doctor、schema 导出与完整性检查。
- 新增有界 stdio 传输和离线故障测试。
- 锁定 14 份关键 schema 的实验兼容基线。
- 新增 progress.md、错误记录和开发验证证据，更新开发规则与现有文档状态。
- 当前没有远端 Host API 或 Android 客户端；真实执行/审批与进程树监督待后续技术验证。
