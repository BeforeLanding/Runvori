# Runvori · 续行

让开发者离开电脑后，仍能在手机上查看 Codex 任务、处理审批、检查执行结果。

项目目前处于 M0 技术验证阶段，首版定位为「开发者离席后的 Codex 移动接管」。已提供 Python 本地探针、隔离 Codex 握手、schema 校验、故障测试，以及可由独立 HTTPS 客户端操作的最小 Host（配对、会话列表、快照、时间线、回合、审批详情与决定、中断、操作查询、命令明细、验证结果与受控产物）。Android 客户端已能配对、列出 Host 管理的会话、读时间线、阅读并决定审批、发起新回合与中断，并在结果页分开查看回合状态、验证结论、命令输出和文件差异；设备验收用真实 APK 对真实 `HostRuntime` 跑通十二个合成场景，并已用真实 Codex 完成 1 次核心闭环设备验收（1 次模型回合）。真机安装、真实中断/过期、真实文件审批 `complete` 路径与 Windows 命令包装仍未完成。

参见 [Runvori 定位收敛与首版方案](docs/定位与首版方案.md)，包含参考项目研究、首版范围、验收标准及 6–8 周安排。

## 首版边界

- Android 原生客户端：Kotlin＋Jetpack Compose。
- 开发电脑上的 Runvori Host：Python＋SQLite，通过 stdio 接入 Codex app-server；当前最小 HTTPS 服务用标准库实现，FastAPI 为后续可替换目标。
- 一台 Host、一台配对手机、全局一个活动回合；只管理 Runvori 启动的会话。
- 通过 HTTPS 在可信局域网或已有 VPN 中访问，模型凭据留在电脑端。
- 重点验证持续输出下的稳定阅读、审批一致性、断线恢复和可检查的执行结果。

以上产品功能均为首版目标，尚未完成验收；已完成的技术验证见 [progress.md](progress.md)。

## 运行本轮开发工具

本轮实测 Python 3.12.10、Codex CLI 0.164.0-alpha.11.1，工具无第三方运行依赖。在仓库根目录执行：

```powershell
python scripts/check.py
python scripts/runvori.py verify-contracts
python scripts/runvori.py doctor
python scripts/probe_https_host.py   # 合成上游的 HTTPS 客户端闭环，不访问模型
```

前两条与 `probe_https_host.py` 离线运行；doctor 需要 PATH 中的精确版本 Codex，会创建隔离的临时 HOME，验证初始化与会话列表，不发起模型回合。Android 设备验收需要已启动的模拟器与已安装的 Debug APK：

```powershell
python scripts/accept_device.py --serial emulator-5554   # 合成上游，十二个场景，不访问模型
```

详情见 [贡献指南](CONTRIBUTING.md)、[本轮证据](docs/evidence/DEV-001.md)、[HTTPS 闭环证据](docs/evidence/DEV-006-https-host.md)、[合成设备验收](docs/evidence/DEV-007-device-acceptance.md)、[真实设备验收](docs/evidence/DEV-007-device-acceptance-real.md)、[DEV-008 结果页证据](docs/evidence/DEV-008-results.md) 与 [DEV-008 产物与差异证据](docs/evidence/DEV-008-artifacts.md)。

## 文档入口

- [文档导航与状态](docs/README.md)：阅读顺序、文档职责和待决策项。
- [产品需求 PRD](docs/PRD.md)：用户、范围、需求编号、验收及发布条件。
- [开发规范 AGENTS.md](AGENTS.md)：适用于全仓库的人与 Agent 协作规则；[agent.md](agent.md) 为兼容阅读入口。
- [架构设计](docs/ARCHITECTURE.md)、[接口与状态协议](docs/PROTOCOL.md)、[移动端交互](docs/UX.md)。
- [测试与验收](docs/TEST_PLAN.md)、[开发路线图](docs/ROADMAP.md)。
- [贡献与环境约定](CONTRIBUTING.md)、[安全与隐私](SECURITY.md)、[设计决策](docs/DECISIONS.md)。

开始开发前，先读 PRD、AGENTS.md、progress.md 和首周技术验证任务。架构文档中的业务目录仍为计划；当前实际存在的模块与命令以贡献指南为准。
