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

探针固定 `workspace-write`、`on-request`、`approvalsReviewer=user`，拒绝扩权型或未知控制请求。显式改为 `--approval accept` 时，默认只允许测试仓库内预定的只读 `python --version`（含本机固定 Codex PowerShell 包装形式）；其他审批继续拒绝。真实单次允许与退出码 0 的证据见 [DEV-003](docs/evidence/DEV-003.md)。它不应用建议的 execpolicy 修订。超时或协议异常关闭受管进程，不自动重发；不把关闭进程写成上游已确认中断。成功时仅在被 Git 忽略的 `.dev/execution-probe-report.json` 保存事件字段名/类型、实际决定、终态和文件核对结果；不保存提示、输出或审批内容值。运行前检查测试仓库和账号目录，运行后人工核对报告并脱敏评审；这条探针不代替审批清理、中断竞争或生产 Host 验收。

受控写入使用全新的可丢弃测试仓库，避免覆盖上次探针结果：

```powershell
python scripts/probe_execution.py prepare --fixture .dev/execution-write-fixture
python scripts/probe_execution.py run --fixture .dev/execution-write-fixture --account-home .dev/codex-home --approval accept --scenario write_file
```

写入场景只允许固定 `Set-Content` 命令及已审查的本机 Codex PowerShell 包装，结果须同时满足命令退出码 0、目标文件 16 字节完全匹配；运行一次后测试仓库不再为空，重新运行需另建新的 `.dev` 测试目录。此场景出现的是**命令执行审批**，不是独立的文件修改审批。已观察结果与限制见 [DEV-003](docs/evidence/DEV-003.md)。

主动中断场景另用空的可丢弃仓库，固定命令先输出标记再等待 60 秒。探针在看到标记后发送 `turn/interrupt`；RPC 返回只记为请求已响应，只有真实回合终态为 `interrupted` 才记录中断已确认。若输出标记未出现，探针在允许命令两秒后发送请求，并标注较弱的触发依据。已观察的真实终态与缺失的命令退出码见 [DEV-003](docs/evidence/DEV-003.md)：

```powershell
python scripts/probe_execution.py prepare --fixture .dev/execution-interrupt-fixture
python scripts/probe_execution.py run --fixture .dev/execution-interrupt-fixture --account-home .dev/codex-home --approval accept --scenario interrupt --timeout 180
```

待审批中断场景使用新的空测试仓库，固定只读命令审批先经白名单核对，再保持未答复并请求中断；不执行命令、不发送允许。探针在回合终态与 thread 恢复后短暂接收迟到的请求结束通知。没有观察到通知时仅记录“未观察到”，不推断它永久不会到达。真实事件顺序见 [DEV-003](docs/evidence/DEV-003.md)：

```powershell
python scripts/probe_execution.py prepare --fixture .dev/execution-pending-interrupt-fixture
python scripts/probe_execution.py run --fixture .dev/execution-pending-interrupt-fixture --account-home .dev/codex-home --approval defer --scenario interrupt_pending --timeout 120
```

审批后立即中断使用另一个空测试仓库，探针只对固定长命令发送一次 `accept`，随后立即请求 `turn/interrupt`，不等待命令输出。它只观察这一种时序，不能凭中断响应推断终态或命令是否启动。真实结果见 [DEV-003](docs/evidence/DEV-003.md)：

```powershell
python scripts/probe_execution.py prepare --fixture .dev/execution-approval-interrupt-fixture
python scripts/probe_execution.py run --fixture .dev/execution-approval-interrupt-fixture --account-home .dev/codex-home --approval accept --scenario interrupt_on_approval --timeout 180
```

独立文件修改审批拒绝使用新的空测试仓库，线程沙箱固定为 `read-only`，提示模型仅尝试 `apply_patch`，并对收到的审批显式回复 `decline`。真实运行已观察到 `item/fileChange/requestApproval`，但其参数不直接提供补丁内容或目标路径；不能仅凭请求允许文件写入。脱敏证据及边界见 [DEV-003](docs/evidence/DEV-003.md)：

```powershell
python scripts/probe_execution.py prepare --fixture .dev/execution-file-change-decline-fixture
python scripts/probe_execution.py run --fixture .dev/execution-file-change-decline-fixture --account-home .dev/codex-home --approval decline --scenario file_change_decline --timeout 180
```

独立文件修改单次允许也在全新的只读测试仓库中运行。探针要求文件改动 item 与审批请求 ID 关联，唯一变更的绝对路径、`add` 类型、完整内容和空 `grantRoot` 均精确匹配后才发送一次 `accept`；完成后核对 item 状态、文件 17 字节及仓库文件清单。真实结果见 [DEV-003](docs/evidence/DEV-003.md)：

```powershell
python scripts/probe_execution.py prepare --fixture .dev/execution-file-change-accept-fixture
python scripts/probe_execution.py run --fixture .dev/execution-file-change-accept-fixture --account-home .dev/codex-home --approval accept --scenario file_change_accept --timeout 180
```

审批答复后受控进程切断使用另一个全新测试仓库，沿用上述精确文件修改白名单。探针在单次 `accept` 写入返回后立即关闭受管 app-server，不继续读事件或恢复 thread；报告将文件 item 和回合终态保持为未观察到，并在关闭后核对目标文件及目录内容。它用于验证未知结果的表示，不能证明上游收到答复、通知确实丢失或 Host 重启可恢复。真实切点记录见 [DEV-003](docs/evidence/DEV-003.md)：

```powershell
python scripts/probe_execution.py prepare --fixture .dev/execution-file-change-cutoff-fixture
python scripts/probe_execution.py run --fixture .dev/execution-file-change-cutoff-fixture --account-home .dev/codex-home --approval accept --scenario file_change_cut_after_accept --timeout 180
```

完成后迟到中断使用新的空测试仓库，先完成固定只读命令且核对退出码 0，再等待真实 `turn/completed: completed`，随后仅为技术探针发送一次迟到的 `turn/interrupt`。即使 RPC 返回错误，先观察到的终态也不改变；真实结果和限制见 [DEV-003](docs/evidence/DEV-003.md)。产品 Host 在已知终态时应直接返回当前状态，不需要发送该 RPC。

```powershell
python scripts/probe_execution.py prepare --fixture .dev/execution-late-interrupt-fixture
python scripts/probe_execution.py run --fixture .dev/execution-late-interrupt-fixture --account-home .dev/codex-home --approval accept --scenario interrupt_after_turn_completed --timeout 180
```

命令完成切点使用固定只读命令，只有对应命令 item 退出码为 0 才请求中断。`interrupt_after_command_completed` 立即请求；`interrupt_after_command_completed_delayed` 固定等待 5 秒再请求，期间不读取回合终态。两者随后都继续接收真实终态、尝试恢复 thread。匹配的 RPC 错误不损坏 stdio 连接，也不授权重试原请求；真实不同切点的结果见 [DEV-003](docs/evidence/DEV-003.md)。每次运行必须使用新的可丢弃仓库：

```powershell
python scripts/probe_execution.py prepare --fixture .dev/execution-command-completion-race-fixture
python scripts/probe_execution.py run --fixture .dev/execution-command-completion-race-fixture --account-home .dev/codex-home --approval accept --scenario interrupt_after_command_completed --timeout 180
python scripts/probe_execution.py prepare --fixture .dev/execution-command-completion-delayed-5s-fixture
python scripts/probe_execution.py run --fixture .dev/execution-command-completion-delayed-5s-fixture --account-home .dev/codex-home --approval accept --scenario interrupt_after_command_completed_delayed --timeout 180
```

不使用真实账号的确认丢失故障测试由 `python scripts/check.py` 执行。合成 peer 在收到审批答复后于模拟副作用前或后关闭连接，均不发请求结束通知；测试核对答复只发送一次、后续操作被禁用。它用于验证传输边界，不能替代真实 Codex 或持久化 Host 的恢复验收，见 [DEV-003](docs/evidence/DEV-003.md)。

同一离线检查命令还运行独立 SQLite 审批派发验证切片：以 `.dev` 临时数据库模拟决定已记录、派发已领取、数据库重开及新 epoch；核对未知结果不重发、文件只读快照不充当上游确认。此模块没有接入真实 Codex 或 Host API，验证范围见 [审批持久边界记录](docs/evidence/DEV-003-approval-journal.md)。

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
