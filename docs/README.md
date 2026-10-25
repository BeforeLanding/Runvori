# 文档导航与维护约定

更新日期：2026-10-23。基线：[定位与首版方案](定位与首版方案.md)，对应仓库提交 `9cf6495`。

## 当前状态

本仓库已进入 M0 技术验证阶段。已建立 Python 探针、schema 基线、故障测试、事件/活动槽/请求幂等/派发/回合确认/恢复存储切片、单库审批请求/决定/派发领取/隔离后恢复/请求结束事务切片及可构建的 Android 占位工程；Host 产品服务与 Android 业务功能尚未实现。以下 PRD/架构文档仍主要描述待实现目标，实际完成项以 [progress.md](../progress.md) 和 [DEV-001](evidence/DEV-001.md)、[DEV-002](evidence/DEV-002.md)、[DEV-003](evidence/DEV-003.md)、[DEV-004](evidence/DEV-004-toolchain.md)、[DEV-005 事件](evidence/DEV-005-event-journal.md)、[DEV-005 活动槽](evidence/DEV-005-active-slot.md)、[DEV-005 幂等](evidence/DEV-005-run-idempotency.md)、[DEV-005 派发](evidence/DEV-005-run-dispatch.md)、[DEV-005 确认](evidence/DEV-005-turn-confirmation.md)、[DEV-005 恢复](evidence/DEV-005-run-recovery.md)、[DEV-005 启动](evidence/DEV-005-locked-startup.md)、[DEV-005 单库](evidence/DEV-005-unified-store.md)、[DEV-005 审批决定](evidence/DEV-005-approval-decision.md)、[DEV-005 审批领取](evidence/DEV-005-approval-dispatch.md)、[DEV-005 审批恢复](evidence/DEV-005-approval-epoch-recovery.md)、[DEV-005 请求结束](evidence/DEV-005-approval-request-closed.md) 验证证据为准。

## 阅读顺序

1. [定位与首版方案](定位与首版方案.md)：为什么做、参考项目、已确定的范围。
2. [PRD](PRD.md)：为谁解决什么问题、需求编号、交付条件。
3. [AGENTS.md](../AGENTS.md)：全仓库开发约束；[agent.md](../agent.md) 指向同一规范。
4. [ARCHITECTURE.md](ARCHITECTURE.md)：模块边界、持久化、恢复与失败处理。
5. [PROTOCOL.md](PROTOCOL.md)：Host 对 Android 的计划接口、状态机、事件和幂等语义。
6. [UX.md](UX.md)：页面、交互、状态文案与阅读恢复。
7. [TEST_PLAN.md](TEST_PLAN.md)：需求对应的测试、故障注入、性能测量及演示证据。
8. [ROADMAP.md](ROADMAP.md)：6–8 周交付顺序、依赖和退出条件。
9. [CONTRIBUTING.md](../CONTRIBUTING.md)：开发环境、提交和评审方式。
10. [SECURITY.md](../SECURITY.md)、[DECISIONS.md](DECISIONS.md)：信任边界、风险与设计决策。
11. [progress.md](../progress.md)、[CHANGELOG.md](../CHANGELOG.md)、[ERRORS.md](ERRORS.md)：当前进度、历史变化与错误修复。

## 文档分工与变更

- 定位文档保留产品决策背景；PRD 是功能范围和产品验收的维护来源。
- PRD 的 `FR-*`、`NFR-*` 为需求标识；测试使用 `AT-*`，里程碑使用 `M*`，决策使用 `ADR-*`。
- 协议文档维护字段、枚举和错误语义；架构与 UX 引用它，不另造同名状态。
- AGENTS.md 维护开发规则；CONTRIBUTING.md 维护操作流程；SECURITY.md 维护产品安全设计。
- 示例 ID、响应体、目录均为设计示例；只有已经进入仓库且执行有记录的内容才可标记“已实现/已验证”。
- 文档冲突先判断是范围、协议还是实现问题；以定位边界和 PRD 为产品依据，同步修正相关文档，不能靠选择性阅读掩盖冲突。
- 新增功能要同时说明需求编号、接口/状态影响、失败路径、测试证据和是否影响首版范围。

## 待验证事项

首周必须完成：Codex CLI 精确版本与 schema、审批及终止事件的实测、首发 Host 操作系统、Android 最低版本及 SDK/JDK 组合。当前探针版本和 schema 已固定，产品兼容矩阵及真实执行语义仍未冻结，见 [待决策清单](DECISIONS.md#待决策清单)。

本次设计补充了定位文档未展开的接口、缓存和配对细节。它们是可评审的首版约定，可根据技术验证修订；修订时须保留事件一致性、审批不误报和恢复边界。
