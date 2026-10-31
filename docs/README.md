# 文档导航与维护约定

更新日期：2026-10-31。基线：[定位与首版方案](定位与首版方案.md)。

## 当前状态

当前进度与里程碑状态只在 [progress.md](../progress.md) 维护；首版边界与已知限制见 [DECISIONS.md 待决策清单](DECISIONS.md#待决策清单)；全部验证证据见 [证据索引](evidence/README.md)。2026-10-30 前本节逐轮追加的状态原文见 [归档](history/2026-10-30-docs-README.md)。

以下 PRD/架构/协议文档描述目标与已落地边界；只有进入仓库且有执行记录的内容才算“已实现/已验证”。

## 阅读顺序

1. [定位与首版方案](定位与首版方案.md)：为什么做、参考项目、已确定的范围。
2. [PRD](PRD.md)：为谁解决什么问题、需求编号、交付条件。
3. [AGENTS.md](../AGENTS.md)：全仓库通用开发约束；按改动区域再读 [ENGINEERING_RULES.md](ENGINEERING_RULES.md) 的对应节；[agent.md](../agent.md) 指向同一规范。
4. [ARCHITECTURE.md](ARCHITECTURE.md)：模块边界、持久化、恢复与失败处理。
5. [PROTOCOL.md](PROTOCOL.md)：Host 对 Android 的计划接口、状态机、事件和幂等语义。
6. [UX.md](UX.md)：页面、交互、状态文案与阅读恢复。
7. [TEST_PLAN.md](TEST_PLAN.md)：需求对应的测试、故障注入、性能测量及演示证据。
8. [ROADMAP.md](ROADMAP.md)：6–8 周交付顺序、依赖和退出条件。
9. [CONTRIBUTING.md](../CONTRIBUTING.md)：开发环境、提交和评审方式。
10. [SECURITY.md](../SECURITY.md)、[DECISIONS.md](DECISIONS.md)：信任边界、风险与设计决策。
11. [progress.md](../progress.md)、[CHANGELOG.md](../CHANGELOG.md)、[ERRORS.md](ERRORS.md)：当前进度（唯一状态来源）、历史变化与错误修复；[history/](history/) 为冻结的旧版原文。

## 文档分工与变更

- 定位文档保留产品决策背景；PRD 是功能范围和产品验收的维护来源。
- PRD 的 `FR-*`、`NFR-*` 为需求标识；测试使用 `AT-*`，里程碑使用 `M*`，决策使用 `ADR-*`。
- 协议文档维护字段、枚举和错误语义；架构与 UX 引用它，不另造同名状态。
- AGENTS.md 维护通用开发规则，ENGINEERING_RULES.md 维护按区域的细则（二者共同构成唯一规则来源）；CONTRIBUTING.md 维护操作流程；SECURITY.md 维护产品安全设计。
- 状态只写在 progress.md；ROADMAP 只写计划与“状态：”行，二者由 `host/tests/test_docs_consistency.py` 核对。
- 示例 ID、响应体、目录均为设计示例；只有已经进入仓库且执行有记录的内容才可标记“已实现/已验证”。
- 文档冲突先判断是范围、协议还是实现问题；以定位边界和 PRD 为产品依据，同步修正相关文档，不能靠选择性阅读掩盖冲突。
- 新增功能要同时说明需求编号、接口/状态影响、失败路径、测试证据和是否影响首版范围。

## 待验证事项

M0 已按 [ADR-017](DECISIONS.md#adr-017m0-结算接受已实测基线剩余边界转为已知限制) 结算；仍开放或转为已知限制的事项见 [待决策清单](DECISIONS.md#待决策清单)。
