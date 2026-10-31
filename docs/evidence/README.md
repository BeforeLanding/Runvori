# 验证证据索引

本目录保存每轮验证的脱敏记录：`.md` 为说明，`.json` 为验收器原始报告，`.png` 为截图。证据按产生时的原文保留，不随后续实现改写；当前状态见 [progress.md](../../progress.md)。本索引按文件名生成，新增证据时补一行。

## DEV-001

- [DEV-001/Q-04：当前 Host 探针包复验](DEV-001-package-recheck.md)
- [DEV-001 验证记录](DEV-001.md)

## DEV-002

- [DEV-002：Windows 进程监督验证](DEV-002.md)

## DEV-003

- [DEV-003：审批派发持久边界验证](DEV-003-approval-journal.md)
- [DEV-003：Codex CLI 版本迁移审查](DEV-003-schema-review.md)
- [DEV-003：Windows junction 路径边界复验](DEV-003-windows-junction.md)
- [DEV-003：审批传输准备记录](DEV-003.md)

## DEV-004

- [DEV-004：Android 构建基线](DEV-004-toolchain.md)

## DEV-005

- [DEV-005 全局活动槽事务切片](DEV-005-active-slot.md)
- [DEV-005 单库审批决定事务切片](DEV-005-approval-decision.md)
- [DEV-005 单库审批派发领取切片](DEV-005-approval-dispatch.md)
- [DEV-005 审批隔离后 epoch 恢复切片](DEV-005-approval-epoch-recovery.md)
- [DEV-005 单库审批请求结束切片](DEV-005-approval-request-closed.md)
- [DEV-005 审批一次派发与 stdio 答复切片](DEV-005-approval-stdio-dispatch.md)
- [DEV-005 已领取启动的中断意图](DEV-005-claimed-start-interrupt.md)
- [DEV-005 Host 内部命令邮箱](DEV-005-command-mailbox.md)
- [DEV-005 设备鉴权读取边界](DEV-005-device-reads.md)
- [DEV-005 本地撤销与在途读取取消](DEV-005-device-revocation.md)
- [DEV-005 上游单消息持久交接切片](DEV-005-event-handoff.md)
- [DEV-005 事件与快照事务切片](DEV-005-event-journal.md)
- [DEV-005 已提交事件的等待通知](DEV-005-event-notification.md)
- [DEV-005 内部事件等待取消边界](DEV-005-event-wait-cancel.md)
- [DEV-005 持锁启动原子恢复切片](DEV-005-host-atomic-recovery.md)
- [DEV-005 恢复后 Host 与设备访问组合](DEV-005-host-device-access.md)
- [DEV-005 Host 身份持久化](DEV-005-host-identity.md)
- [DEV-005 真实 Codex Host 崩溃重启五切点](DEV-005-host-runtime-restart-real-codex.md)
- [DEV-005 产品 Host 启动与崩溃重启闭环](DEV-005-host-runtime-restart.md)
- [DEV-005 中断请求持久边界](DEV-005-interrupt-journal.md)
- [DEV-005 本地工作区登记与创建归属切片](DEV-005-local-workspace-registration.md)
- [DEV-005 审批与事件库持锁启动切片](DEV-005-locked-startup.md)
- [DEV-005 命令邮箱增量事件等待](DEV-005-mailbox-event-wait.md)
- [DEV-005 命令邮箱快照与事件读取](DEV-005-mailbox-reads.md)
- [DEV-005 Host 邮箱停机边界](DEV-005-mailbox-shutdown.md)
- [DEV-005 离线本地设备撤销命令](DEV-005-offline-device-revoke.md)
- [DEV-005 归属连接审批串行派发](DEV-005-owned-approval-scheduling.md)
- [DEV-005 归属连接持续事件循环](DEV-005-owned-event-loop.md)
- [DEV-005 归属连接单步事件泵](DEV-005-owned-event-pump.md)
- [DEV-005 中断请求归属派发](DEV-005-owned-interrupt.md)
- [DEV-005 已受理回合归属调度](DEV-005-owned-run-start.md)
- [DEV-005 本地 thread 创建归属调度](DEV-005-owned-thread-start.md)
- [DEV-005 当前受管上游退出恢复切片](DEV-005-owned-upstream-exit.md)
- [DEV-005 配对凭据与 Host 身份绑定](DEV-005-pairing-host-binding.md)
- [DEV-005 新回合单次派发领取切片](DEV-005-run-dispatch.md)
- [DEV-005 新回合请求幂等切片](DEV-005-run-idempotency.md)
- [DEV-005 旧回合隔离后恢复事务](DEV-005-run-recovery.md)
- [DEV-005 新回合一次派发与 stdio 绑定切片](DEV-005-run-stdio-dispatch.md)
- [DEV-005 会话与上游 thread 持久归属切片](DEV-005-session-thread-binding.md)
- [DEV-005 快照与最近事件同事务读取](DEV-005-snapshot-tail.md)
- [DEV-005 共享库恢复前写门控](DEV-005-startup-write-gate.md)
- [DEV-005 历史 thread 恢复身份核对切片](DEV-005-thread-resume-identity.md)
- [DEV-005 上游 thread 一次创建切片](DEV-005-thread-start-once.md)
- [DEV-005 上游回合确认持久切片](DEV-005-turn-confirmation.md)
- [DEV-005 回合终态审批收尾切片](DEV-005-turn-terminal-approval-settlement.md)
- [DEV-005 单库审批请求事务切片](DEV-005-unified-store.md)
- [DEV-005 未派发回合取消](DEV-005-unstarted-cancel.md)
- [DEV-005 上游审批请求身份映射切片](DEV-005-upstream-approval-request.md)
- [DEV-005 上游审批结束身份映射切片](DEV-005-upstream-approval-resolved.md)
- [DEV-005 旧受管上游进程的持久身份与启动隔离门](DEV-005-upstream-process-identity.md)
- [DEV-005 上游连接唯一归属切片](DEV-005-upstream-single-owner.md)
- [DEV-005 上游回合终态身份映射切片](DEV-005-upstream-turn-terminal.md)

## DEV-006

- [DEV-006 真实 Codex HTTPS 验收（显式 1 次）](DEV-006-https-host-real.md)
- [DEV-006 可通过 HTTPS 操作的最小 Host（合成上游闭环）](DEV-006-https-host.md)

## DEV-007

- [DEV-007 真实 Codex 设备验收（1 次模型回合）](DEV-007-device-acceptance-real.md)
- [DEV-007 Android 最小业务闭环（合成上游 + 真实设备）](DEV-007-device-acceptance.md)

## DEV-008

- [DEV-008 受控产物与差异（第二切片）](DEV-008-artifacts.md)
- [DEV-008 Android 生命周期恢复专项](DEV-008-lifecycle-recovery.md)
- [DEV-008 真实文件审批与验证匹配（第三切片）](DEV-008-real-approval-slice.md)
- [DEV-008 命令详情与验证结果（第一切片）](DEV-008-results.md)

## DEV-009

- [DEV-009 真实中断与审批失效验收：运行方案](DEV-009-interrupt-approval-plan.md)
- [DEV-009 真实中断与审批失效验收结果](DEV-009-interrupt-approval-real.md)

## DEV-010

- [DEV-010 真机地址配置与零模型验收入口](DEV-010-device-baseline.md)

## DEV-011

- [DEV-011 时间线阅读模型与浏览模式设备验收](DEV-011-reading.md)

## M0-Q04

- [M0/Q-04：Windows 单实例锁验证切片](M0-Q04-instance-lock.md)
- [M0/Q-04：持锁打开审批记录的启动切片](M0-Q04-locked-journal.md)

## M0-Q05

- [M0/Q-05：HTTPS 配对回环联动探针](M0-Q05-https-pairing.md)
- [M0/Q-05：一次性配对记录验证切片](M0-Q05-pairing-journal.md)
- [M0/Q-05：回环 TLS 与 Host 指纹验证探针](M0-Q05-tls-identity.md)

## 原始报告与截图

- [DEV-003-approval-interrupted.json](DEV-003-approval-interrupted.json)
- [DEV-003-artifact-boundaries.json](DEV-003-artifact-boundaries.json)
- [DEV-003-binary-command.json](DEV-003-binary-command.json)
- [DEV-003-command-accepted.json](DEV-003-command-accepted.json)
- [DEV-003-command-completion-interrupt-race.json](DEV-003-command-completion-interrupt-race.json)
- [DEV-003-command-declined.json](DEV-003-command-declined.json)
- [DEV-003-confirmation-lost-synthetic.json](DEV-003-confirmation-lost-synthetic.json)
- [DEV-003-dirty-workspace.json](DEV-003-dirty-workspace.json)
- [DEV-003-file-change-accepted.json](DEV-003-file-change-accepted.json)
- [DEV-003-file-change-cut-after-accept.json](DEV-003-file-change-cut-after-accept.json)
- [DEV-003-file-change-declined.json](DEV-003-file-change-declined.json)
- [DEV-003-file-move-accepted.json](DEV-003-file-move-accepted.json)
- [DEV-003-file-move-declined.json](DEV-003-file-move-declined.json)
- [DEV-003-file-written.json](DEV-003-file-written.json)
- [DEV-003-late-interrupt-rejected.json](DEV-003-late-interrupt-rejected.json)
- [DEV-003-nonzero-verification.json](DEV-003-nonzero-verification.json)
- [DEV-003-pending-interrupted.json](DEV-003-pending-interrupted.json)
- [DEV-003-rename.json](DEV-003-rename.json)
- [DEV-003-required-verification.json](DEV-003-required-verification.json)
- [DEV-003-turn-interrupted.json](DEV-003-turn-interrupted.json)
- [DEV-003-two-required-verifications.json](DEV-003-two-required-verifications.json)
- [DEV-004-emulator.png](DEV-004-emulator.png)
- [DEV-006-https-host-real.json](DEV-006-https-host-real.json)
- [DEV-006-https-host.json](DEV-006-https-host.json)
- [DEV-007-device-acceptance-real.json](DEV-007-device-acceptance-real.json)
- [DEV-007-device-acceptance.json](DEV-007-device-acceptance.json)
- [DEV-008-artifacts.json](DEV-008-artifacts.json)
- [DEV-008-results.json](DEV-008-results.json)
- [DEV-008-third-bootstrap.json](DEV-008-third-bootstrap.json)
- [DEV-008-third-real.json](DEV-008-third-real.json)
- [DEV-008-third-slice.json](DEV-008-third-slice.json)
- [DEV-008-vr-real.json](DEV-008-vr-real.json)
- [DEV-009-first-run.json](DEV-009-first-run.json)
- [DEV-009-ra-rerun.json](DEV-009-ra-rerun.json)
- [DEV-010-device-baseline.json](DEV-010-device-baseline.json)
- [DEV-011-reading-all.json](DEV-011-reading-all.json)
- [DEV-011-reading-baseline.json](DEV-011-reading-baseline.json)
- [DEV-011-reading-mutation.json](DEV-011-reading-mutation.json)
