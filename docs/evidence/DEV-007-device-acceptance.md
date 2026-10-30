# DEV-007 Android 最小业务闭环（合成上游 + 真实设备）

日期：2026-10-27。需求关联：FR-01、FR-02、FR-03、FR-04、FR-05、FR-06、FR-08；路线图：M1 最小协议 → M2 Android 主流程。项目仍处 M0。

本轮把 DEV-004 的静态占位 APK 接成可操作的最小业务客户端：手机配对后能列出 Host 管理的会话、读时间线、阅读并提交审批、发起新回合与中断。**验收链路是真实 APK → 固定 SPKI 指纹的 HTTPS → 真实 `HostRuntime` 子进程 → 合成 app-server**。本文件只记录合成上游的九场景；真实 Codex 的核心闭环已于同日单独执行 1 次并通过，见 [真实设备验收](DEV-007-device-acceptance-real.md)。

## 本轮交付

- **会话列表接口**：`GET /v1/sessions`（游标为不透明 `session_id`，稳定分页）返回 `title / workspace_name / session_version / snapshot_seq / active_run / latest_run / pending_approval_count / updated_at`。列表只读一份 SQLite 读事务，`pending_approval_count` 统计 `pending/decision_recorded/dispatching/unknown`，与快照口径一致。
- **时间线持久化（事件库 v15）**：上游 `item/started|item/completed` 与 `item/agentMessage/delta`、`item/commandExecution/outputDelta` 投影为 `message.*` / `command.*` 事件，按 `(thread_id, turn_id)` 持久绑定定位 run，调用方无法把输出挂到别人的回合。新增 `run_timeline_budget` 表为单回合文本设 2 MiB 上限，超出后追加 `timeline.truncated` 并逐条记 `text_omitted`/`dropped_bytes`，不静默丢弃；单块上限 8 KiB。时间线事件只推进 `seq`，不改 `session_version` 或快照，因此持续输出不会让所有审批版本冲突。
- **Android 数据层**：`RunvoriCache`（SQLite，`(host_id, session_id, seq)` 唯一，连续游标与事件写入同事务提交）、`HostEndpoint`（手工 HTTP/1.1 over `SSLSocket`，固定指纹 + 主机名校验，无重试）、`RunvoriApi`（`/v1` 端点映射）、`HostSession`（配对、凭据、同步顺序）。网络访问只在数据层，Composable 不持 socket。
- **配对与凭据**：`PairingInfo.parse` 校验 HTTPS 端点与 64 位十六进制指纹；`PinnedTrust` 用 `X509ExtendedTrustManager` 在链、主机名之外核对 SPKI，不匹配时不写请求。令牌由 Android Keystore 的不可导出 AES-256-GCM 密钥加密后存 SharedPreferences，`allowBackup=false` 且 `data_extraction_rules` 同时排除凭据与缓存库，云备份与设备迁移都带不走。
- **界面与主流程**：配对 → 会话列表 → 时间线（历史向前分页、回合状态、命令与输出）→ 审批页（命令原文、cwd、reason、变更详情；详情不足只给拒绝）→ 新回合输入 → 中断二次确认。每个控件带稳定 test tag，文案按协议表述（202 是“已受理”，不确定结果写“待确认”）。
- **测试编排**：`scripts/accept_device.py` 启动真实 Host（`listen_host="127.0.0.1"`）、登记工作区与会话、签发配对信息，再逐场景调用 instrumented 测试；场景之间由编排器执行手机做不到的 Host 侧动作（撤销设备）。报告中只出现码、计数、布尔和摘要。

## 传输层（更正上一轮的判断）

上一轮把设备联调失败归因为 `adb reverse` 故障与 Windows 防火墙拦截，并要求人工放行。这个判断是错的，本轮已用实验推翻：

- 从模拟器内用 `toybox nc` 直连 `10.0.2.2:<port>`，连接在宿主机侧以 `('127.0.0.1', …)` 到达——模拟器的 `10.0.2.2` 由它自身的 SLIRP NAT 转成宿主机回环连接，**不经过 Windows 防火墙**，也不需要 `adb reverse`/`adb forward`。
- 因此删除了 `DeviceRelay.kt`、`scripts/device_bridge.py`、`scripts/check_device_bridge.py` 及相关诊断脚本，Host 改为只监听回环。没有新增防火墙规则，也没有放宽任何校验。
- 证书由 `generate_certificate(folder, "device", extra_sans=["IP:10.0.2.2"])` 生成，把模拟器实际拨号的地址写进 SAN。客户端“证书必须命名我要连的地址”这条规则照常生效，不是绕过。

## 修复的产品缺陷

以下四条都是产品行为错误，不是测试脚手架问题：

1. **存活轮询在审批页停摆**：`startPolling` 只要离开 Timeline 就退出，而审批页是叠在 Timeline 之上的页面，导致用户在审批页期间回合输出与终态都不再更新，返回时间线看到的是旧内容。改为只在回到 Sessions 时停止，`closeApproval` 再补一次同步。
2. **决定提交后从不回查操作状态**：原来只轮询审批视图，缓存里的操作永远停在 `accepted`，直接失约“提交后查询操作状态”。改为按原 `request_id` 查询 `/v1/operations?kind=approval` 并把回执写入缓存，超时显示“结果待确认”。
3. **Host 在 worker 线程做 TLS 握手**：`ThreadingHTTPServer` 把已接受的 socket 交给工作线程再 wrap，并发时两个握手会在一对 socket 上交错，客户端看到 EOF、服务端报 `SSLV3_ALERT_CERTIFICATE_UNKNOWN`。改为在 `get_request`（accept 线程）完成握手，失败就地关闭。
4. **运行被拒的原因被静默吞掉**：`startRun` 捕获 `ProtocolError` 设置 `error` 后立即调用 `syncSelectedSession()`，而 `applySync` 无条件 `error = null`，于是“已有活动回合”“会话已变化”这类拒绝信息在用户看到之前就被抹掉。改为只清除网络类瞬时错误，其余错误保留；顺带修好“知道了”按钮只清 `error` 而让 `notice` 横幅留在屏幕上的问题。

## 验证

### 单元与集成

- `python scripts/check.py`：`Schema integrity: 16 passed`，`Ran 374 tests`，`OK (skipped=3)`。三项跳过为：两项宿主符号链接创建受限，一项真实 Codex 测试未启用（`RUNVORI_REAL_HOST_TESTS is not enabled`）。
- Android JVM 单元测试：`./gradlew :app:testDebugUnitTest` → 19 项，0 失败（`HostnameMatch`、`JsonValue`、`PairingInfo`、`TimelineProjector` 等，含“截断标记是独立一行而不是并入消息”）。
- 新增 Host 测试：`test_host_timeline.py`（会话列表游标稳定性与最近回合、待审批计数与按状态过滤的 run 审批列表、时间线只推 seq 不改版本、需绑定的活动回合、预算只截一次并记录缺口——断言 `timeline.truncated` 恰好一条且位置在首个被截断事件之后）、`test_timeline_projection.py`（item/输出投影）。

### 设备验收（真实 APK → HTTPS → 真实 HostRuntime）

环境：Windows 11 Home China 10.0.26200；Python 3.12.10；模拟器 `Medium_Phone`，API 37、x86_64、Android 17；APK `dev.runvori.app` 0.0.2。

命令：`python scripts/accept_device.py --serial emulator-5554`（合成 app-server，无模型调用）。结果 `status: passed`，退出码 0，九个场景全部通过。本轮合成模式共跑 **3 次**，每次都九个场景全绿：

1. 修完 TLS 握手与轮询停摆缺陷后首次跑通；
2. 修复“拒绝原因被同步抹掉”“横幅按钮只清错误不清提示”后重装 APK 重跑；
3. 为 `timeline.truncated` 补上真正的写入实现（此前只声明了事件种类却从未产生）并加断言后重跑。

三次都是重跑整套用例，没有自动重试单个场景，也没有为了变绿而改断言。真实模式分支落地后另补跑 1 次合成回归（不产生模型调用），不计入上述 3 次；见 [真实设备验收](DEV-007-device-acceptance-real.md)。

| 场景 | 验证内容 |
|---|---|
| `s` | Keystore 凭据往返成功；落盘字节是密文，不含令牌明文；`clear()` 后读不到 |
| `a` | 配对信息里篡改指纹后确认配对：UI 显示拒绝，未存任何凭据，待核对载荷仍留在屏幕上 |
| `b` | 完整闭环：列出两个会话 → 打开 `devsess0` → 发新回合 → 读到审批 → 审批页展示命令原文与 cwd、`details_status=complete`、可允许可拒绝 → 双击只产生一个决定操作 → 审批转 `resolved`、回合转终态 → 时间线出现命令、输出与 Agent 文本 → 回会话列表 |
| `c` | 先制造历史，再丢弃内存连接、构造新 ViewModel 从磁盘游标重同步：seq 无重复、从 1 连续、无缺口、终态事件仍在 |
| `d` | 审批被请求结束时，UI 的允许与拒绝都不可用；直接用 API 提交允许被 Host 拒绝 |
| `f` | 文件审批缺 item 证据时 `details_status=insufficient`、只给拒绝、允许按钮不可用 |
| `h` | 中断两次确认：第一次只弹确认框，取消后回合继续；确认后回合结束、输入框重新可用 |
| `g` | 同一 `request_id` 连续查询两次，状态稳定 |
| `e` | 编排器在 Host 侧撤销设备后，旧令牌被拒，UI 显示“配对凭据已失效，请在电脑重新配对” |

`b` 与 `f` 里用到的 `python --version`、`[file-change]`、`[interrupt]`、`[expire-request]` 都是合成上游的固定脚本，不是模型行为。

报告是由编排输出整理的脱敏 JSON（`docs/evidence/DEV-007-device-acceptance.json`）：只有码、计数和布尔，没有令牌、配对秘密、命令正文或绝对路径。本次运行的临时目录在退出时删除。

## 未完成与限制

- **本文件不包含真实 Codex 结论**：九场景的上游是合成 app-server。真实 Codex 设备验收已于同日单独执行 1 次（`s/a/b/c/g/e`，1 次模型回合，`observed_runs=1`），见 [真实设备验收](DEV-007-device-acceptance-real.md)。真实文件审批已由 [DEV-008 第三切片](DEV-008-real-approval-slice.md) 补验；真实中断/过期与 `d/f/h` 仍未覆盖。
- **`command_matches_probe=false` 仍未消除**：本机 Windows 上 Codex 以 shell 包装串发起命令（[ERR-007](../ERRORS.md)、[ERR-016](../ERRORS.md)）。界面按原样展示上游给出的完整命令串，不简化成未经核实的裸命令；但“展示串等于 `python --version` 字面量”这件事仍未被证实，也未被当作验收前提。
- **未覆盖的验收项**：真实文件审批（本轮文件审批走的是合成 `insufficient` 路径，`complete` 的文件审批没有端到端证据）；`GET /v1/runs/{run_id}`、命令明细分页、产物清单与产物字节端点当时仍未实现。命令明细、`/results` 与产物资源已由后续 DEV-008 补齐，见 [结果页](DEV-008-results.md) 与 [产物与差异](DEV-008-artifacts.md)；真实文件审批已由 [DEV-008 第三切片](DEV-008-real-approval-slice.md) 补验。
- **`active_run_exists` 的 UI 呈现无断言**：本轮修掉了“拒绝原因被同步抹掉”的缺陷（[ERR-030](../ERRORS.md)），但设备套件仍没有“已有活动回合时再发起”的场景，因此该提示的实际呈现只有代码依据，没有红绿实验证据；拒绝路径的回归测试待补。
- **Android 侧未覆盖**：旋转、切后台、系统杀进程（`c` 用重建 ViewModel 模拟进程丢失，不等于真实进程死亡）、清除缓存、200% 字体与 TalkBack、性能基线。
- **AT-16 未整体通过**：首发 OS 未定；starting/决定派发/中断切点由测试 relay 注入；真实上游正常退出且后代继承 stdio 的 EOF 检测（[ERR-027](../ERRORS.md)）未覆盖。
- 模拟器 NAT 别名 `10.0.2.2` 只适用于模拟器；物理手机需要自己的可达路径，不在本编排器范围内，`host_alias()` 对有真机时会直接报 `emulator_required_for_alias`。