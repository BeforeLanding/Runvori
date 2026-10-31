# Runvori · 续行

让开发者离开电脑后，仍能在手机上查看 Codex 任务、处理审批、检查执行结果。

首版定位为「开发者离席后的 Codex 移动接管」。当前已有：可由 HTTPS 客户端操作的 Host（配对、会话、时间线、审批、中断、回合结果与产物），以及能完成「配对 → 列会话 → 读时间线 → 审批 → 中断 → 看结果与差异」的 Android 客户端；主流程在模拟器与合成上游上通过，核心闭环、文件审批、验证结果和中断另有真实 Codex 证据。**稳定阅读与性能测量尚未开始，实际手机尚未接入。** 里程碑状态与下一步见 [progress.md](progress.md)，首版不保证的边界见 [待决策清单](docs/DECISIONS.md#待决策清单)。

参见 [Runvori 定位收敛与首版方案](docs/定位与首版方案.md)，包含参考项目研究、首版范围、验收标准及 6–8 周安排。

## 首版边界

- Android 原生客户端：Kotlin＋Jetpack Compose。
- 开发电脑上的 Runvori Host：Python＋SQLite，通过 stdio 接入 Codex app-server；HTTPS 服务用标准库实现，零第三方运行依赖（未采用 FastAPI，见 ADR-009）。
- 一台 Host、一台配对手机、全局一个活动回合；只管理 Runvori 启动的会话。
- 通过 HTTPS 在可信局域网或已有 VPN 中访问，模型凭据留在电脑端。
- 重点验证持续输出下的稳定阅读、审批一致性、断线恢复和可检查的执行结果。

以上为首版边界，PRD 的验收用例尚未整体通过；首版 Host 只支持 Windows 11 x64 与锁定的 Codex CLI 版本（[ADR-017](docs/DECISIONS.md)）。

## 运行本轮开发工具

本轮实测 Python 3.12.10、Codex CLI 0.164.0-alpha.11.1，工具无第三方运行依赖。在仓库根目录执行：

```powershell
python scripts/check.py
python scripts/runvori.py verify-contracts
python scripts/runvori.py doctor
python scripts/probe_https_host.py   # 合成上游的 HTTPS 客户端闭环，不访问模型
```

前两条与 `probe_https_host.py` 离线运行；doctor 需要 PATH 中的精确版本 Codex，会创建隔离的临时 HOME，验证初始化与会话列表，不发起模型回合。Android 设备验收需要已启动的模拟器或已授权的物理手机，以及已安装的 Debug APK 与 test APK。模拟器仍走历史路径：

```powershell
python scripts/accept_device.py --serial emulator-5554   # 合成上游，二十三个场景，不访问模型
```

真机零模型基线需要手机可达的局域网/VPN 地址或 DNS 名；`--host-address` 与设备 serial 分开配置，证书继续按端点写 SAN 并校验链、主机名和 SPKI：

```powershell
python scripts/accept_device.py --serial <phone-serial> `
  --profile device-baseline --host-address <LAN-or-VPN-address-or-DNS> `
  --apk android/app/build/outputs/apk/debug/app-debug.apk `
  --test-apk android/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk `
  --dry-run
```

详情见 [贡献指南](CONTRIBUTING.md)；全部验证记录见 [证据索引](docs/evidence/README.md)。

## 文档入口

- [文档导航与状态](docs/README.md)：阅读顺序、文档职责和待决策项。
- [产品需求 PRD](docs/PRD.md)：用户、范围、需求编号、验收及发布条件。
- [开发规范 AGENTS.md](AGENTS.md)：适用于全仓库的人与 Agent 协作规则；[agent.md](agent.md) 为兼容阅读入口。
- [架构设计](docs/ARCHITECTURE.md)、[接口与状态协议](docs/PROTOCOL.md)、[移动端交互](docs/UX.md)。
- [测试与验收](docs/TEST_PLAN.md)、[开发路线图](docs/ROADMAP.md)。
- [贡献与环境约定](CONTRIBUTING.md)、[安全与隐私](SECURITY.md)、[设计决策](docs/DECISIONS.md)。

开始开发前，先读 progress.md、PRD 与 AGENTS.md；当前实际存在的模块与命令以贡献指南为准。
