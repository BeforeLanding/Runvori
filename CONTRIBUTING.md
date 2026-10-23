# 贡献、环境与评审约定

日期：2026-10-23。已建立 M0 Python 验证工具、包配置和离线测试；尚无业务 Host、Gradle wrapper、Android 或 CI。本文区分当前可执行命令和未来开发计划，进度见 [progress.md](progress.md)。

## 1. 开始前

阅读 [AGENTS.md](AGENTS.md)、[PRD](docs/PRD.md)、[路线图](docs/ROADMAP.md) 和本次改动对应的设计。新增需求使用 `FR-* / NFR-*`，技术任务对应 `M*` 和验收 `AT-*`。

检查分支及未提交修改，保留已有工作。建议围绕一个可验证行为建立短分支，如 `docs/v0.1-baseline`、`feat/approval-idempotency`；不要求为了纯文档变更创建多份工作树。

## 2. 环境建立顺序

### 现在可以执行

在仓库根目录使用 Git 检查修改：

```powershell
git status --short
git diff --check
git diff --stat
```

新增未跟踪文件也需单独检查；`git diff --check` 本身不覆盖未跟踪内容。当前没有已通过的产品 Host/Android 验收，探针的故障测试可执行如下。

### DEV-001 已验证命令

本轮实测 Windows、Python 3.12.10、SQLite 3.49.1，Codex CLI 锁定 `0.163.0-alpha.11.2`。使用系统标准库运行，不要求安装 pytest/ruff/FastAPI。仓库根目录执行：

```powershell
python scripts/check.py
python scripts/runvori.py verify-contracts
python scripts/runvori.py doctor
```

check 同时校验契约和运行 unittest，不访问模型/账号。doctor 需要该精确版本 Codex 在 PATH，先验证契约，再创建 `.dev` 临时 HOME 进行握手与 thread/list。只输出白名单诊断，退出码非零表示失败。它不会启动 turn 或更改用户 Codex 配置。非仓库目录调用时使用 `--workspace <仓库根目录>`。

### DEV-003 真实执行探针（显式启用）

`scripts/probe_execution.py` 与 doctor 分离。先在仓库内 `.dev/execution-fixture` 建立只含标记文件的可丢弃 Git 仓库：

```powershell
python scripts/probe_execution.py prepare
```

真实执行必须显式提供专用、已登录的 `CODEX_HOME` 目录及本次审批决定；不复制当前用户的认证文件。本机实验使用 `.dev/codex-home` 作为独立目录。需要重新登录时，在同一个 PowerShell 进程中设置 `$env:CODEX_HOME = (Resolve-Path .dev/codex-home).Path`，运行 `codex login`，再用 `codex login status` 核验；该目录被 `.gitignore` 排除，不要打开或提交其中的 `auth.json`。这不是修改全局用户环境变量。

以下命令会发起模型回合，并可能在可丢弃仓库内创建文件；本机已用 `decline` 观察一次真实命令审批与回合终态，结果见 [DEV-003](docs/evidence/DEV-003.md)：

```powershell
python scripts/probe_execution.py run --account-home .dev/codex-home --approval decline
```

探针固定 `workspace-write`、`on-request`、`approvalsReviewer=user`，拒绝扩权型或未知控制请求。显式改为 `--approval accept` 时，只有测试仓库内预定的只读 `python --version`（含本机固定 Codex PowerShell 包装形式）可收到一次 `accept`；其他审批继续拒绝。真实单次允许与退出码 0 的证据见 [DEV-003](docs/evidence/DEV-003.md)。此模式不验证文件写入，不应用建议的 execpolicy 修订。超时或协议异常关闭受管进程，不自动重发；不把关闭进程写成上游已确认中断。成功时仅在被 Git 忽略的 `.dev/execution-probe-report.json` 保存事件字段名/类型、实际决定、终态和文件核对结果；不保存提示、输出或审批内容值。运行前检查测试仓库和账号目录，运行后人工核对报告并脱敏评审；这条探针不代替审批清理、中断竞争或生产 Host 验收。

schema 已提交，通常不需要重新导出。`python scripts/runvori.py export-schema` 在基线存在时拒绝覆盖；仅经评审后使用 `--replace`。升级须更新版本常量、schema、测试及兼容记录。

已安装 setuptools 81.0.0 的环境可离线构建探针 wheel（尚非独立部署的 Host）：

```powershell
New-Item -ItemType Directory -Force .dev/tmp | Out-Null
$env:TMPDIR = (Resolve-Path .dev/tmp).Path
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
python -m pip wheel . --no-build-isolation --no-deps --no-index --wheel-dir .dev/wheels
```

这些环境变量仅作用于当前终端；受限沙箱优先使用 `.dev` 临时路径。构建后 wheel 仍需要仓库契约文件，安装入口通过 `--workspace` 指向 checkout。普通源代码检查无须构建或安装包。

### M0 必须建立的可复现环境

- 选择首发 Host 操作系统和受支持架构，实测进程启动、stdio、子进程终止及路径处理。
- 锁定 Python 精确版本/约束、包管理方式、FastAPI/SQLite 驱动与测试工具；提交依赖清单及锁文件。禁止只记录“最新版”。
- 锁定 Android 最低版本、compile/target SDK、JDK、Kotlin、Compose、AGP 与 Gradle 组合，提交 wrapper 和构建说明。
- 锁定 Codex CLI 精确版本、安装来源和校验方式，保留 `codex --version`、本机帮助和导出的 schema 证据。
- 验证用户在电脑本地已经具备可用模型访问。真实模型凭据不用于普通 PR CI。
- 建立脱敏测试仓库和一台明确型号/系统/刷新率的 Android 性能基准机。

Codex 官方允许按安装版本导出 app-server schema。M0 在核对本机帮助后，将实际版本与生成产物纳入兼容记录；示例命令并不表示当前仓库已安装该版本：[官方说明](https://learn.chatgpt.com/docs/app-server)。

```powershell
codex --version
codex app-server --help
codex app-server generate-json-schema --out .dev/codex-schema
```

完整导出目录不纳入版本管理；已提交的 14 份关键 schema 见 `contracts/codex/`，真实执行夹具仍待取得。不要提交认证文件或完整真实会话。上面的原生命令仅用于理解导出方式，日常请使用隔离 HOME 的 Runvori 导出命令。

### M0/M1 完成后应补齐的操作指南

实现者必须把实际可执行命令加入本文，包括：依赖安装、Host 本地启动、工作区登记、首次任务、TLS 配置、配对/撤销、Android 构建安装、数据库迁移、单元/集成检查、恢复与删除数据。命令需在干净环境执行并记录结果，再标记为可用。

预期检查工具可采用 Host 的 pytest/静态检查，以及 Android 的 Gradle 单元测试、lint 和设备测试；工具版本与命令以实际配置为准。本阶段不编造 `runvori serve`、`make test` 或不存在的脚本。

## 3. 开发顺序与代码约定

先完成 schema/适配器验证，再固化领域状态与持久化，再提供 API 和客户端投影。UI 可用脱敏 fixture 开发，但不能把 mock 验收当成真实上游联调。

- Python 使用类型注解和明确异常分类；异步任务的取消/退出要清理资源，禁止吞异常后返回成功。
- Kotlin 使用明确状态模型、协程结构化并发和有界数据流；界面通过 ViewModel 访问领域用例。
- 协议字段及代码标识符使用英文；中文用于产品说明和必要注释。注释解释约束和取舍，不复述代码。
- 不提前建立多 Host、多用户和插件框架。共享逻辑先按真实复用提取，不为假想扩展增加抽象。
- 存储变更带迁移和恢复说明；不能以删除用户数据库代替升级。
- API 示例与协议同步；批准、中断和新回合必须复用同一幂等/活动槽规则。

## 4. 测试与提交

测试以行为为中心：普通文案修正无需新增测试；状态机、事件持久化、审批和恢复必须覆盖关键成功及失败路径。测试范围见 [TEST_PLAN.md](docs/TEST_PLAN.md)。

提交建议采用 `docs:`、`feat(host):`、`feat(android):`、`fix:`、`test:` 等清楚前缀。每个变更尽量对应一个行为；格式化和重构不要混入无关功能。

PR 描述说明具体问题、最终行为、需求编号、验证命令和结果、兼容/迁移影响及未验证事项。新行为至少有一个清楚的触发与结果例子。没有执行的检查必须标为未执行，不以计划冒充通过。

## 5. 完成定义

- 范围符合 PRD；涉及的状态与接口更新已同步。
- 对应验收可复现；正确性修复有合适的回归，且检查结果可核对。
- 没有真实秘密、个人路径、真实业务源码或设备令牌进入 Git。
- 安全/存储/兼容变化说明迁移或回退边界；未处理风险有明确记录。
- 文档、界面和发布说明严格区分已实现、待验证和不支持。
- 功能合入后更新路线图状态；版本发布按 PRD 门槛，不能仅因“六周已到”宣布完成。

## 6. 许可证与对外材料

当前未在仓库提供许可证。不要替维护者擅自选择许可证或宣称已获特定开源授权；对外发布前处理 [DECISIONS.md](docs/DECISIONS.md) 的相关事项。参考项目只用于理解设计取舍；引入代码/素材前核查对应许可证和来源。
