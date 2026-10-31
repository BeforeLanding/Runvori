> 归档快照：本文件是 `docs/README.md` 在提交 `bda8ca7`（2026-10-30）时的原文，2026-10-31 文档结算时冻结，只修正了相对链接以适应新位置。当前状态见 [progress.md](../../progress.md)。

# 文档导航与维护约定

更新日期：2026-10-30。基线：[定位与首版方案](../定位与首版方案.md)，DEV-007 交付提交 `c00b67b`，DEV-008 第一切片提交 `a5290e5`；DEV-008 第二、第三切片与 DEV-009 运行方案见下文证据。

## 当前状态

DEV-007 把 Android 从占位工程接成最小业务客户端：补齐会话列表与按回合的审批列表、把上游 item 生命周期投影成时间线（事件库 v15），Android 侧实现配对载荷解析、Keystore 凭据、固定指纹 HTTPS、SQLite 缓存与同步、四个屏幕与新回合/中断。设备验收用**真实 APK → 固定指纹 HTTPS → 真实 `HostRuntime` → 合成 app-server** 跑通九个场景，见 [DEV-007 设备验收](../evidence/DEV-007-device-acceptance.md)。同日又用真实 Codex 执行 **1 次**核心闭环设备验收（`s/a/b/c/g/e`，1 次模型回合，`observed_runs=1`，未重试），见 [DEV-007 真实设备验收](../evidence/DEV-007-device-acceptance-real.md)。

DEV-008 把命令、验证结论和产物接到产品端：事件库 v16 保存命令证据与回合级验证要求，v17 新增受控产物与内容块；新增 `/v1/runs/{run_id}`、`/results`、`/commands`、`/artifacts` 和按 ID 分块读取。Android 增加回合结果与产物详情。第三切片修复真实文件 item 绝对路径、`item/updated` 与审批页刷新，新增有限包装匹配和 Host 重启读回；锁定 0.164 已恢复到隔离目录，真实 `l`（允许）、`m`（拒绝）、`v`（验证通过）与 `r`（重启读回一致）全部通过（[ERR-038](../ERRORS.md) 已关闭，见 [真实 v/r 证据](../evidence/DEV-008-vr-real.json)）。Android 生命周期专项新增 `o/q/w/x/t/y/z/s2`，覆盖切后台、Activity 旋转、进程重建、回执丢失、Host 重启后旧审批失效、系统 `am kill` 与 `force-stop` 分别记录、产物快速切换。DEV-009 真实中断与审批失效验收已通过：首次 2 个模型回合中 `ri` 执行中真实中断通过；`ra` 手机侧通过，但真实 Codex 在回合终止后关闭上游请求并改写当前 `resolution_kind`，首次 Host 分类器误判，读取器改为结合事件历史确认回合终止失效后，经单独授权的 1 个回合复跑 `ra` 通过。另加 0 回合合成伴侣 `s3` 先验证审批页中断的产品路径。合成场景合计 23 个。见 [DEV-009 验收结果](../evidence/DEV-009-interrupt-approval-real.md) 与 [运行方案](../evidence/DEV-009-interrupt-approval-plan.md)。

DEV-010 把设备验收的传输与零模型入口补齐：`--host-address` 与 `--serial` 分开配置，支持 LAN/VPN IP 或 DNS 名，`--listen-address` 控制本机绑定，`--host-port` 可固定端口；证书按端点写 IP/DNS SAN，TLS 链、主机名和 SPKI 校验不放宽。`--profile device-baseline` 通过合成上游跑 14 个零模型场景，覆盖安装/APK 版本核对、配对、读取、错误指纹、撤销、离线只读缓存、Host 重启后的游标恢复、系统杀进程/force-stop 和 200% 字体。模拟器上最终全绿（`expected_model_turns=0`）；**实际手机未连接，真机可达性、真实断网与性能仍未验收**。见 [DEV-010 证据](../evidence/DEV-010-device-baseline.md) 与 [脱敏报告](../evidence/DEV-010-device-baseline.json)。见 [第一切片证据](../evidence/DEV-008-results.md)、[第二切片证据](../evidence/DEV-008-artifacts.md) 与 [第三切片证据](../evidence/DEV-008-real-approval-slice.md)。

真机与性能仍未覆盖。

DEV-006 把真实 `HostRuntime` 接成可由独立 HTTPS 客户端操作的最小 Host：审批详情与禁止盲批闸门、Windows 命名管道本地管理入口、HTTPS 与设备认证/限流、最小业务端点。合成上游闭环见 [DEV-006 HTTPS Host](../evidence/DEV-006-https-host.md)，真实 Codex 一次验收见 [DEV-006 真实验收](../evidence/DEV-006-https-host-real.md)。

旧受管上游进程的持久身份与启动隔离门见 [DEV-005 上游进程身份](../evidence/DEV-005-upstream-process-identity.md)；无身份记录时的失败关闭与产品 Host 启动/崩溃重启闭环见 [DEV-005 Host 启动与重启](../evidence/DEV-005-host-runtime-restart.md)。锁定版本真实 Codex、启动前版本门、审批决定入口和五个真实崩溃切点见 [DEV-005 真实 Codex 五切点](../evidence/DEV-005-host-runtime-restart-real-codex.md)；AT-16 仍因首发 OS 等条件未整体通过。

恢复后 Host 身份与配对、读取入口的内部组合见 [DEV-005 Host 设备访问](../evidence/DEV-005-host-device-access.md)。

权威 Host 数据库中的稳定身份前提见 [DEV-005 Host 身份](../evidence/DEV-005-host-identity.md)。

配对秘密、设备令牌与当前 Host 身份的内部绑定见 [DEV-005 配对身份绑定](../evidence/DEV-005-pairing-host-binding.md)。

等待线程的已提交事件提示见 [DEV-005 事件通知](../evidence/DEV-005-event-notification.md)。

内部增量事件等待见 [DEV-005 命令邮箱事件等待](../evidence/DEV-005-mailbox-event-wait.md)；取消边界见 [DEV-005 事件等待取消](../evidence/DEV-005-event-wait-cancel.md)，归属循环退出后的等待释放见 [DEV-005 邮箱停机边界](../evidence/DEV-005-mailbox-shutdown.md)，配对令牌保护的内部读取见 [DEV-005 设备鉴权读取](../evidence/DEV-005-device-reads.md)，进程内撤销切片见 [DEV-005 在途读取取消](../evidence/DEV-005-device-revocation.md)，停机后的 CLI 入口见 [DEV-005 离线撤销](../evidence/DEV-005-offline-device-revoke.md)。

同事务快照与最近事件尾页见 [DEV-005 快照尾页](../evidence/DEV-005-snapshot-tail.md)。

邮箱中的原始快照与事件页读取见 [DEV-005 命令邮箱读取侧](../evidence/DEV-005-mailbox-reads.md)。

跨线程请求交给 SQLite 所有者的内部入口见 [DEV-005 命令邮箱](../evidence/DEV-005-command-mailbox.md)。

已领取 `turn/start` 但尚未绑定 turn 的中断意图见 [DEV-005 已领取启动的中断](../evidence/DEV-005-claimed-start-interrupt.md)。

尚未派发的 `starting` 回合原子取消见 [DEV-005 未派发取消](../evidence/DEV-005-unstarted-cancel.md)。

中断请求在归属连接上的一次 stdio 派发见 [DEV-005 中断派发](../evidence/DEV-005-owned-interrupt.md)。

已绑定回合的中断请求持久边界见 [DEV-005 中断记录](../evidence/DEV-005-interrupt-journal.md)。上游 `turn/interrupt` 尚未接入。

已受理回合在归属连接上的串行派发见 [DEV-005 回合调度](../evidence/DEV-005-owned-run-start.md)。

本地 `thread/start` 的连接归属调度见 [DEV-005 本地 thread 创建](../evidence/DEV-005-owned-thread-start.md)。

当前连接上的审批串行派发见 [DEV-005 审批调度](../evidence/DEV-005-owned-approval-scheduling.md)。

归属连接持续消费与停止边界见 [DEV-005 持续事件循环](../evidence/DEV-005-owned-event-loop.md)。该入口仍需与审批答复和 RPC 调度整合。

归属连接单步事件交接与故障恢复验证见 [DEV-005 事件泵](../evidence/DEV-005-owned-event-pump.md)。

当前 Host 上游连接唯一归属的验证见 [DEV-005 连接归属](../evidence/DEV-005-upstream-single-owner.md)。

当前受管上游退出后的隔离与恢复验证见 [DEV-005 上游退出](../evidence/DEV-005-owned-upstream-exit.md)。

本地工作区登记与会话归属的本轮验证见 [DEV-005 工作区登记](../evidence/DEV-005-local-workspace-registration.md)。

上游 thread 一次创建的本轮验证见 [DEV-005 thread 创建](../evidence/DEV-005-thread-start-once.md)。

历史 thread 恢复身份核对的本轮验证见 [DEV-005 thread 恢复](../evidence/DEV-005-thread-resume-identity.md)。

会话与上游 thread 持久归属的本轮验证见 [DEV-005 thread 绑定](../evidence/DEV-005-session-thread-binding.md)。

新回合一次派发到 stdio 的本轮验证见 [DEV-005 回合派发](../evidence/DEV-005-run-stdio-dispatch.md)。

审批一次派发到 stdio 的本轮验证见 [DEV-005 审批派发](../evidence/DEV-005-approval-stdio-dispatch.md)。

上游单消息持久交接的本轮验证见 [DEV-005 事件交接](../evidence/DEV-005-event-handoff.md)。

回合终态时的审批收尾行为见 [DEV-005 终态审批收尾](../evidence/DEV-005-turn-terminal-approval-settlement.md)。

上游回合终态身份映射的本轮验证见 [DEV-005 回合终态映射](../evidence/DEV-005-upstream-turn-terminal.md)。该切片仍未接入产品事件循环。

上游审批请求身份映射的本轮验证见 [DEV-005 上游请求映射](../evidence/DEV-005-upstream-approval-request.md)。该切片仍未接入产品事件循环。

本仓库已进入 M0 技术验证阶段。已建立 Python 探针、schema 基线、故障测试、事件/活动槽/请求幂等/派发/回合确认/恢复存储切片、单库审批生命周期、持锁启动原子恢复、恢复前写门控、上游结束通知身份映射、可构建的 Android 业务客户端、可选 HTTPS Host、命令/验证结果读出与受控产物。真实 Codex 已完成一次设备核心闭环；真实文件审批 `l/m` 的允许与拒绝、真实 `v` 验证结论与 `r` 重启读回均已通过；真实 `ri/ra` 中断与审批失效均通过；`ra` 首次运行因上游补发 `request_closed` 被分类器误判，离线修复后经单独授权的 1 个回合复跑通过，真机性能仍未完成。Android 生命周期专项已在模拟器覆盖切后台、旋转、进程重建、系统 `am kill` 与 `force-stop`、决定回执丢失和 Host 重启后的旧审批失效。DEV-010 已补真机地址/绑定分离和零模型 14 场景基线，模拟器通过；实际手机尚未接入，离线验收用停止 Host 代替真实网络切换。以下 PRD/架构文档描述目标与已落地边界，实际完成项以 [progress.md](../../progress.md)、[DEV-007 设备验收](../evidence/DEV-007-device-acceptance.md)、[DEV-007 真实设备验收](../evidence/DEV-007-device-acceptance-real.md)、[DEV-008 结果页](../evidence/DEV-008-results.md)、[DEV-008 产物与差异](../evidence/DEV-008-artifacts.md)、[DEV-008 第三切片](../evidence/DEV-008-real-approval-slice.md)、[DEV-008 生命周期恢复](../evidence/DEV-008-lifecycle-recovery.md)、[DEV-009 中断与审批失效运行方案](../evidence/DEV-009-interrupt-approval-plan.md) 以及 [DEV-001](../evidence/DEV-001.md)、[DEV-002](../evidence/DEV-002.md)、[DEV-003](../evidence/DEV-003.md)、[DEV-004](../evidence/DEV-004-toolchain.md)、[DEV-005 事件](../evidence/DEV-005-event-journal.md)、[DEV-005 活动槽](../evidence/DEV-005-active-slot.md)、[DEV-005 幂等](../evidence/DEV-005-run-idempotency.md)、[DEV-005 派发](../evidence/DEV-005-run-dispatch.md)、[DEV-005 确认](../evidence/DEV-005-turn-confirmation.md)、[DEV-005 恢复](../evidence/DEV-005-run-recovery.md)、[DEV-005 启动](../evidence/DEV-005-locked-startup.md)、[DEV-005 单库](../evidence/DEV-005-unified-store.md)、[DEV-005 审批决定](../evidence/DEV-005-approval-decision.md)、[DEV-005 审批领取](../evidence/DEV-005-approval-dispatch.md)、[DEV-005 审批恢复](../evidence/DEV-005-approval-epoch-recovery.md)、[DEV-005 请求结束](../evidence/DEV-005-approval-request-closed.md)、[DEV-005 原子恢复](../evidence/DEV-005-host-atomic-recovery.md)、[DEV-005 写门控](../evidence/DEV-005-startup-write-gate.md)、[DEV-005 上游结束映射](../evidence/DEV-005-upstream-approval-resolved.md) 验证证据为准。

## 阅读顺序

1. [定位与首版方案](../定位与首版方案.md)：为什么做、参考项目、已确定的范围。
2. [PRD](../PRD.md)：为谁解决什么问题、需求编号、交付条件。
3. [AGENTS.md](../../AGENTS.md)：全仓库开发约束；[agent.md](../../agent.md) 指向同一规范。
4. [ARCHITECTURE.md](../ARCHITECTURE.md)：模块边界、持久化、恢复与失败处理。
5. [PROTOCOL.md](../PROTOCOL.md)：Host 对 Android 的计划接口、状态机、事件和幂等语义。
6. [UX.md](../UX.md)：页面、交互、状态文案与阅读恢复。
7. [TEST_PLAN.md](../TEST_PLAN.md)：需求对应的测试、故障注入、性能测量及演示证据。
8. [ROADMAP.md](../ROADMAP.md)：6–8 周交付顺序、依赖和退出条件。
9. [CONTRIBUTING.md](../../CONTRIBUTING.md)：开发环境、提交和评审方式。
10. [SECURITY.md](../../SECURITY.md)、[DECISIONS.md](../DECISIONS.md)：信任边界、风险与设计决策。
11. [progress.md](../../progress.md)、[CHANGELOG.md](../../CHANGELOG.md)、[ERRORS.md](../ERRORS.md)：当前进度、历史变化与错误修复。

## 文档分工与变更

- 定位文档保留产品决策背景；PRD 是功能范围和产品验收的维护来源。
- PRD 的 `FR-*`、`NFR-*` 为需求标识；测试使用 `AT-*`，里程碑使用 `M*`，决策使用 `ADR-*`。
- 协议文档维护字段、枚举和错误语义；架构与 UX 引用它，不另造同名状态。
- AGENTS.md 维护开发规则；CONTRIBUTING.md 维护操作流程；SECURITY.md 维护产品安全设计。
- 示例 ID、响应体、目录均为设计示例；只有已经进入仓库且执行有记录的内容才可标记“已实现/已验证”。
- 文档冲突先判断是范围、协议还是实现问题；以定位边界和 PRD 为产品依据，同步修正相关文档，不能靠选择性阅读掩盖冲突。
- 新增功能要同时说明需求编号、接口/状态影响、失败路径、测试证据和是否影响首版范围。

## 待验证事项

首周必须完成：Codex CLI 精确版本与 schema、审批及终止事件的实测、首发 Host 操作系统、Android 最低版本及 SDK/JDK 组合。当前探针版本和 schema 已固定，产品兼容矩阵及真实执行语义仍未冻结，见 [待决策清单](../DECISIONS.md#待决策清单)。

本次设计补充了定位文档未展开的接口、缓存和配对细节。它们是可评审的首版约定，可根据技术验证修订；修订时须保留事件一致性、审批不误报和恢复边界。
