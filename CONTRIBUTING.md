# 贡献、环境与评审约定

日期：2026-10-24。已建立 M0 Python 验证工具、包配置、离线测试和可构建的 Android 占位工程；尚无业务 Host 或 CI。本文区分当前可执行命令和未来开发计划，进度见 [progress.md](progress.md)。

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

Android Debug APK 已在本机完成构建。配置 JDK 17 或更高版本及 Android SDK，并在当前 PowerShell 会话设置 `ANDROID_HOME` 为 SDK 根目录，然后执行：

```powershell
cd android
.\gradlew.bat --no-daemon :app:assembleDebug
```

输出位于 `android/app/build/outputs/apk/debug/app-debug.apk`。目前只显示静态占位界面；最低 API 26 是暂定构建值，尚无实机安装和产品功能验收。版本、构建证据及限制见 [DEV-004](docs/evidence/DEV-004-toolchain.md)。
连接一台测试设备或启动 AVD 后，可在仓库根目录运行固定包名的安装/启动冒烟检查：

```powershell
.\scripts\smoke_android.ps1 -Serial emulator-5554
```

省略 `-Serial` 时须恰好连接一台设备。脚本核对安装返回、进程和前台 Activity；它不验证 Host 连接或业务功能。模拟器实测见 [DEV-004](docs/evidence/DEV-004-toolchain.md)。

本地 Git 产物边界探针无需模型和账号，只能在 `.dev` 内准备全新夹具。它生成预先存在的跟踪文本/二进制改动和未跟踪文件，检查大小、哈希、内容截断与 Git 二进制标记；报告只含固定夹具的元信息，不包含文件正文。结果不能归因于 Codex 回合；见 [脱敏证据](docs/evidence/DEV-003-artifact-boundaries.json)：

```powershell
python scripts/probe_artifacts.py prepare --fixture .dev/artifact-fixture
python scripts/probe_artifacts.py inspect --fixture .dev/artifact-fixture
```

### DEV-001 已验证命令

当前实测 Windows、Python 3.12.10、SQLite 3.49.1，Codex CLI 锁定 `0.164.0-alpha.11.1`；0.163 的历史证据保留原版本标注。使用系统标准库运行，不要求安装 pytest/ruff，Host 未采用 FastAPI 等第三方框架。仓库根目录执行：

```powershell
python scripts/check.py
python scripts/runvori.py verify-contracts
python scripts/runvori.py doctor
```

check 同时校验契约和运行 unittest，不访问模型/账号。doctor 需要该精确版本 Codex 在 PATH，先验证契约，再创建 `.dev` 临时 HOME 进行握手与 thread/list。只输出白名单诊断，退出码非零表示失败。它不会启动 turn 或更改用户 Codex 配置。非仓库目录调用时使用 `--workspace <仓库根目录>`。

### DEV-005 Host 启动与重启（M1 切片）

`HostRuntime` 已组合单实例锁、v13 epoch 状态、统一恢复、写门禁、设备访问与唯一上游，但尚无 HTTP/TLS，Android 不能连接。启动入口要求精确版本 `codex-cli 0.164.0-alpha.11.1`；`codex` 不在 PATH 时用 `--codex` 或 `RUNVORI_CODEX` 提供绝对路径。版本探测在取得锁、打开数据库和创建配对库之前执行，版本不符或不可执行直接以稳定 code 拒绝：

```powershell
New-Item -ItemType Directory -Force .dev/host-data | Out-Null
$env:PYTHONPATH = "$PWD\host\src"
$codex = "$env:LOCALAPPDATA\OpenAI\Codex\bin\2993767bd1dfb009\codex.exe"
python -m runvori.host --data-dir (Resolve-Path .dev/host-data).Path --codex $codex
```

Ctrl+C 触发正常停止（`host_shutdown`）。上游连接故障时进程恢复后以退出码 3 结束，等待外部重启；启动被拒绝为退出码 2 并输出稳定 code。旧库无身份记录会返回 `legacy_upstream_unverified`，只能归档数据目录后重建，不能删改库内状态强行启动。

### DEV-006 HTTPS 与本地管理入口（M1 切片）

在上面命令基础上增加 `--listen`、`--tls-cert`、`--tls-key`、`--host-name`、`--admin-pipe`；也可用环境变量 `RUNVORI_LISTEN`、`RUNVORI_TLS_CERT`、`RUNVORI_TLS_KEY`、`RUNVORI_HOST_NAME`、`RUNVORI_ADMIN_PIPE=1`。不给 `--listen` 时 Host 仍不监听网络。证书必须是绝对路径的普通文件，启动时计算 SPKI 指纹；没有跳过校验的开关。

```powershell
$env:PYTHONPATH = "$PWD\host\src"
$data = (Resolve-Path .dev/host-data).Path
openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 2 `
  -keyout .dev/host-data/tls.key -out .dev/host-data/tls.pem `
  -subj "/CN=Runvori Host" -addext "subjectAltName=DNS:localhost"
python -m runvori.host --data-dir $data --codex $codex `
  --listen 127.0.0.1:8765 --tls-cert .dev/host-data/tls.pem `
  --tls-key .dev/host-data/tls.key --admin-pipe
```

启动后用同一台电脑上的命名管道做本地管理（不经过 HTTP）：

```powershell
python scripts/runvori.py host-status --data-dir $data
python scripts/runvori.py register-workspace --data-dir $data --workspace-id ws1 --root .dev/demo-repo --sandbox workspace-write --verification-command "python=python --version"
python scripts/runvori.py create-session --data-dir $data --workspace-id ws1 --session-id sess1 --create-thread --request-id thread-1
python scripts/runvori.py pairing-info --data-dir $data
python scripts/runvori.py revoke-device --data-dir $data   # 运行中撤销；Host 未运行则 mode=offline
```

`pairing-info` 输出 `https_url / host_id / spki_sha256 / pairing_secret / expires_at`；客户端必须固定该指纹后 `POST /v1/pairings/exchange`，秘密只放 body 且只能兑换一次。

合成上游闭环（不访问模型，退出码 0 表示全部步骤通过）：

```powershell
python scripts/probe_https_host.py
```

真实 Codex HTTPS 验收默认跳过，只有显式启用才发起 1 次真实模型回合，且不自动重试：

```powershell
$env:RUNVORI_REAL_HOST_TESTS = "1"
$env:RUNVORI_REAL_CODEX = "$env:LOCALAPPDATA\OpenAI\Codex\bin\2993767bd1dfb009\codex.exe"
$env:RUNVORI_REAL_CODEX_HOME = (Resolve-Path .dev/codex-home).Path
python scripts/accept_https_host_real.py
```

结果与限制见 [合成证据](docs/evidence/DEV-006-https-host.md) 与 [真实验收](docs/evidence/DEV-006-https-host-real.md)。命令明细、验证结果与受控产物已由 DEV-008 接通，见 [结果页证据](docs/evidence/DEV-008-results.md) 与 [产物与差异证据](docs/evidence/DEV-008-artifacts.md)。必需验证命令由本地 `register-workspace --verification-command KEY=COMMAND` 声明，手机端不能修改；命令按精确文本或已观察的完整 shell 包装与 cwd 匹配，未观察的包装保守判为 `unknown/not_run`。

真实设备验收需要已连接且已授权的 adb 设备，以及 Debug APK 与 test APK。模拟器走历史的 `10.0.2.2` NAT 路径；物理手机必须用 `--host-address` 指定手机可达的局域网/VPN IP 或 DNS 名，必要时用 `--listen-address` 指定本机绑定、用 `--host-port` 固定端口以便防火墙放行。证书按端点写 IP/DNS SAN，客户端仍校验证书链、主机名和 SPKI 指纹。

零模型真机入口如下，先 dry-run 核对设备、端点、TLS 与场景集；`device-baseline` 统一使用合成上游，`expected_model_turns=0`：

```powershell
python scripts/accept_device.py --serial <phone-serial> `
  --profile device-baseline --host-address <LAN-or-VPN-address-or-DNS> `
  --apk android/app/build/outputs/apk/debug/app-debug.apk `
  --test-apk android/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk `
  --dry-run
```

确认后去掉 `--dry-run`，按需加 `--listen-address`/`--host-port`，并用 `--keep --report .dev/<name>.json` 保留脱敏结果。基线覆盖安装与 APK 版本核对、配对、读取、错误指纹、撤销、离线缓存只读、Host 不可达后的恢复、系统杀进程/force-stop 与 200% 字体；物理设备报告不含原始地址或 serial。

真实模型模式默认场景集为 `s/a/b/c/g/r/e`；`l/m/v/ri/ra` 是真实专用场景（文件允许、文件拒绝、验证结论、执行中中断、回合终止导致审批失效），各有独立提示词；`d/f/h/i/j/k/n/o/q/w/x/t/y/z/s2/s3/fs/off/rec` 会以 `synthetic_only_scenario` 拒绝。`ri/ra` 合计 2 个模型回合；`s3` 是 `ra` 的 0 回合合成伴侣。先 dry-run 校验，再经显式授权按预算执行模型回合；失败不自动重试：

```powershell
$env:RUNVORI_REAL_HOST_TESTS = "1"
$env:RUNVORI_REAL_CODEX = (Resolve-Path .dev/codex-0.164/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe).Path
$env:RUNVORI_REAL_CODEX_HOME = (Resolve-Path .dev/codex-home).Path
python scripts/accept_device.py --serial emulator-5554 --real --only ri,ra --dry-run
python scripts/accept_device.py --serial emulator-5554 --real --only ri,ra --keep `
  --report .dev/dev009-interrupt-approval-real.json
```

`ri` 只在真实命令输出 `RUNVORI_SLEEP_STARTED` 后中断；自然完成记 `terminal_race_natural_completion`，RPC/202 不算成功。`ra` 从保持打开的审批页中断，要求事件历史中存在回合终止导致的审批 `expired`、最终仍失效、旧决定被拒绝且 `approval_operations=0`；真实上游可能在之后补发 `request_closed` 并改写当前 `resolution_kind`，不能只看当前行。方案与验收结果见 [DEV-009](docs/evidence/DEV-009-interrupt-approval-real.md)。

`--dry-run` 只校验版本门、设备、场景集与回合预算，不启动 Host、不发起模型回合。`--report` 在成功或失败时都写脱敏 JSON；`--keep` 保留一次性数据目录，并在 stderr 打印路径。验证失败时用 `python scripts/inspect_kept_commands.py <保留目录>` 在本机核对 Host 原文；该脚本只允许读取 `.dev` 内目录，原文不得写入报告或证据。失败保留报告且不自动重跑。结果与限制见 [真实设备验收](docs/evidence/DEV-007-device-acceptance-real.md) 与 [真实 v/r 证据](docs/evidence/DEV-008-vr-real.json)。

定向测试使用真实子进程与 fake app-server（不访问模型）：

```powershell
$env:PYTHONPATH = "$PWD\host\src"
python -m unittest discover -s host/tests -p "test_host_*.py" -v
```

合成崩溃用例会启动并终止真实 Host 子进程，验证 launcher、fake app-server 与后代 PID 的 Job 隔离；它们不能替代真实 Codex 执行树的 AT-16 验收，限制见 [记录](docs/evidence/DEV-005-host-runtime-restart.md)。

真实 Codex 五切点测试默认跳过，只有显式启用才会发起真实模型回合并产生账号/网络费用。每个切点默认只运行一次，不自动重试；失败先判断夹具还是产品，复跑前说明原因。需要绝对路径、专用已登录 `CODEX_HOME`，不要复制或提交认证文件：

```powershell
$env:PYTHONPATH = "$PWD\host\src;$PWD\host\tests"
$env:RUNVORI_REAL_HOST_TESTS = "1"
$env:RUNVORI_REAL_CODEX = "$env:LOCALAPPDATA\OpenAI\Codex\bin\2993767bd1dfb009\codex.exe"
$env:RUNVORI_REAL_CODEX_HOME = (Resolve-Path .dev/codex-home).Path
$env:RUNVORI_REAL_EVIDENCE_DIR = (Resolve-Path .dev).Path + "\real-evidence"
python -m unittest discover -s host/tests -p "test_host_restart_real.py" -v
```

测试在 `.dev` 创建一次性 Git 夹具，通过产品 `thread/start` 创建真实 thread，并通过真实 Windows Job 运行 launcher、测试 relay 与 `codex.exe`。starting、决定派发和中断切点由 [relay](host/tests/codex_relay.py) 在消息写入 Codex 管道后冻结下游；它只证明写入，不证明解析或副作用。结果与限制见 [真实 Codex 五切点](docs/evidence/DEV-005-host-runtime-restart-real-codex.md)。

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

单条必需验证命令探针使用新的可丢弃仓库，预登记固定 `python --version`。只有同一 thread/turn/item 的结构化开始和完成事件、精确命令与 cwd、有效时间和整数退出码齐全，才判为 `passed` 或 `failed`；缺失与重复关联保持 `unknown`。真实观察见 [脱敏证据](docs/evidence/DEV-003-required-verification.json)：

```powershell
python scripts/probe_execution.py prepare --fixture .dev/execution-verification-fixture --scenario verify_command
python scripts/probe_execution.py run --fixture .dev/execution-verification-fixture --account-home .dev/codex-home --approval accept --scenario verify_command --timeout 180
```

双命令探针预登记上述命令及 `git --version`，分别运行、分别审批。每项按结构化证据判定，再按协议优先级汇总；共用一个上游 item 的两项都降为 `unknown`。真实观察仅覆盖两项均通过，失败和混合状态由本地行为测试覆盖，见 [脱敏证据](docs/evidence/DEV-003-two-required-verifications.json)：

```powershell
python scripts/probe_execution.py prepare --fixture .dev/execution-two-verifications-fixture --scenario verify_two_commands
python scripts/probe_execution.py run --fixture .dev/execution-two-verifications-fixture --account-home .dev/codex-home --approval accept --scenario verify_two_commands --timeout 180
```

失败路径使用新的可丢弃仓库，只允许精确 `exit 7` 一次。运行结果必须核对结构化退出码精确为 7、回合终态与必需验证汇总分别记录；真实观察见 [脱敏证据](docs/evidence/DEV-003-nonzero-verification.json)：

```powershell
python scripts/probe_execution.py prepare --fixture .dev/execution-nonzero-verification-fixture --scenario verify_nonzero
python scripts/probe_execution.py run --fixture .dev/execution-nonzero-verification-fixture --account-home .dev/codex-home --approval accept --scenario verify_nonzero --timeout 180
```

受控写入使用全新的可丢弃测试仓库，避免覆盖上次探针结果：

```powershell
python scripts/probe_execution.py prepare --fixture .dev/execution-write-fixture
python scripts/probe_execution.py run --fixture .dev/execution-write-fixture --account-home .dev/codex-home --approval accept --scenario write_file
```

写入场景只允许固定 `Set-Content` 命令及已审查的本机 Codex PowerShell 包装，结果须同时满足命令退出码 0、目标文件 16 字节完全匹配；运行一次后测试仓库不再为空，重新运行需另建新的 `.dev` 测试目录。此场景出现的是**命令执行审批**，不是独立的文件修改审批。已观察结果与限制见 [DEV-003](docs/evidence/DEV-003.md)。

固定二进制命令探针同样使用新的可丢弃仓库，先提交固定二进制基线，只允许精确的单文件写入命令。结果分别核对命令 item 退出码、2,048 字节目标内容、Git 二进制标记和产物正文不可用；它不验证上游二进制文件修改审批。真实记录见 [脱敏证据](docs/evidence/DEV-003-binary-command.json)：

```powershell
python scripts/probe_execution.py prepare --fixture .dev/execution-binary-fixture --scenario binary_command
python scripts/probe_execution.py run --fixture .dev/execution-binary-fixture --account-home .dev/codex-home --approval accept --scenario binary_command --timeout 120
```

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

脏工作区探针在准备阶段提交固定基线，再制造一个跟踪文件的预先修改和一个预先存在的未跟踪文件；运行阶段仍只允许上述精确新增文件的一次审批。运行后需同时核对上游 item、目标字节、原有文件字节、`git status --porcelain` 和 `git diff --name-only` 的可见范围。每次使用新的 `.dev` 夹具目录；真实观察见 [脱敏证据](docs/evidence/DEV-003-dirty-workspace.json)：

```powershell
python scripts/probe_execution.py prepare --fixture .dev/execution-dirty-file-change-fixture --scenario file_change_accept_dirty
python scripts/probe_execution.py run --fixture .dev/execution-dirty-file-change-fixture --account-home .dev/codex-home --approval accept --scenario file_change_accept_dirty --timeout 180
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

同一命令还在 Windows 运行独立的单实例锁跨进程测试：第二进程不能获取锁，持锁进程被终止后可重新获取。锁尚未接入产品 Host 启动顺序，见 [M0/Q-04 证据](docs/evidence/M0-Q04-instance-lock.md)。
另有持锁打开审批记录的最小启动测试：锁失败时不打开 SQLite，数据库失败时释放锁，进程异常退出后重开不自动推进 epoch；见 [启动切片证据](docs/evidence/M0-Q04-locked-journal.md)。

M0 TLS 身份探针需要本机 OpenSSL CLI 和可用回环端口，不使用真实凭据或远端 Host。执行 `python scripts/probe_tls_identity.py`；通过与拒绝路径见 [Q-05 证据](docs/evidence/M0-Q05-tls-identity.md)。普通受限沙箱可能禁止回环连接，此时完整检查会将该用例标记跳过，需在获准的本地环境复验。

同一离线检查还运行独立的一次性配对 SQLite 测试，覆盖 5 分钟到期、并发单次兑换、响应丢失后的重放拒绝和本地撤销；没有网络配对接口或真实设备凭据。见 [Q-05 配对记录](docs/evidence/M0-Q05-pairing-journal.md)。

定向 HTTPS 联动夹具可用 `python scripts/probe_https_pairing.py` 运行：仅监听本机回环临时端口，使用一次性测试证书、秘密和数据库；需 OpenSSL CLI 与可用回环连接。结果和限制见 [Q-05 联动证据](docs/evidence/M0-Q05-https-pairing.md)。

Windows 上已有独立配对库的 Host 数据目录，可在 Host 停止后执行离线本地撤销：先将 `$runvoriDataDir` 设置为含 `pairing.db` 的**现有绝对目录**，再运行：

```powershell
python scripts/runvori.py revoke-device --data-dir $runvoriDataDir
```

命令先取得同目录的 `host.lock`；锁被占用时拒绝且不撤销令牌，缺失配对库也拒绝，不要求 Codex 或模型账号。该命令只适用于当前独立配对切片；运行中撤销需未来可信本地通道调用进程内网关，见 [验证记录](docs/evidence/DEV-005-offline-device-revoke.md)。

schema 已提交，通常不需要重新导出。`python scripts/runvori.py export-schema` 在基线存在时拒绝覆盖；仅经评审后使用 `--replace`。升级须更新版本常量、schema、测试及兼容记录。

已安装 setuptools 81.0.0 的环境可复验当前探针 wheel、隔离安装和真实命令入口（尚非独立部署的 Host）：

```powershell
.\scripts\smoke_package.ps1
```

脚本将 `TEMP/TMP` 局限在仓库 `.dev`，离线构建 wheel，在一次性虚拟环境安装，并从仓库外调用已安装的 `runvori.exe` 校验 16 份契约和模块导入；结束后清理夹具。wheel 仍需要仓库契约文件，安装入口通过 `--workspace` 指向 checkout；`pip install --target` 的命令入口不作为安装成功证据。结果见 [包复验](docs/evidence/DEV-001-package-recheck.md)。普通源代码检查无须构建或安装包。

### M0 必须建立的可复现环境

- 选择首发 Host 操作系统和受支持架构，实测进程启动、stdio、子进程终止及路径处理。
- 锁定 Python 精确版本/约束、包管理方式、SQLite 版本与测试工具；当前零第三方运行依赖（未采用 FastAPI），引入任何依赖时提交依赖清单及锁文件。禁止只记录“最新版”。
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

推送到 `main` 和每个 PR 都会运行 [CI](.github/workflows/ci.yml)：Windows 上执行 `python scripts/check.py`，Ubuntu 上执行 Android JVM 单测与 Debug/AndroidTest 构建。CI 不启动模拟器、不调用 Codex；设备验收与真实 Codex 运行仍需在本机执行并在 PR 中写明结果。

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
