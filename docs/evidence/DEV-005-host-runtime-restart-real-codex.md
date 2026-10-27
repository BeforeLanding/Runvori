# DEV-005 真实 Codex Host 崩溃重启五切点

日期：2026-10-26。需求关联：FR-02、FR-04、FR-05、FR-09、NFR-01；验收参考：AT-16。工作区基线 HEAD `a32af060ea9d6a094705f80ddb3a4278bedaa827`，本轮未提交。文中的真实运行结果只对当前机器、当前二进制和当前账号环境成立。

## 结论

在真实 `HostRuntime` 子进程和锁定版本真实 Codex app-server 上，完成了 starting、running、等待审批、审批决定派发后、中断过程五个切点的崩溃重启：

- 五个切点都使用真实 Windows Job、真实 PID＋创建时间、真实 Codex 进程树；
- 崩溃后 epoch 不提前推进，重启后旧回合均标记 `interrupted`，原因为 `host_restart`；
- 等待审批切点审批 `expired`，新请求重放返回 `approval_expired`；
- 决定派发后切点审批和操作保持 `unknown` / `dispatch_unconfirmed`，旧审批 ID 保留且重放不产生新操作或新派发；
- 中断过程切点中断操作保持 `unknown` / `dispatch_unconfirmed`；
- 所有观测到的 launcher、relay、`codex.exe` 和后代在 Host 被杀后均为 `absent`、`exited` 或 `replaced`；
- 每个切点都完成第二个 Host 启动/停止和第三个 Host 启动/停止，锁释放与 epoch 闭环成立。

这仍不等于 AT-16 整体通过：首发 Host 操作系统尚未决定；三个切点使用测试 relay 注入时序；真实 thread 通过产品 `thread/start` 创建，但崩溃测试没有覆盖真实手机审批详情读取；证据来自可丢弃夹具和固定提示，不是通用模型行为验证。

## 环境与二进制

- OS：Windows 11 `10.0.26200`，x86_64。
- Python：3.12.10；SQLite：3.49.1。
- Codex：`codex-cli 0.164.0-alpha.11.1`。
- 安装来源：OpenAI Codex 桌面应用随附 CLI。
- 二进制：`%LOCALAPPDATA%\OpenAI\Codex\bin\2993767bd1dfb009\codex.exe`。
- 大小：354,661,168 字节。
- SHA-256：`1103e9e09c71fa2cd0afd6d8f3b9a2d63174fe8efd7cb5bd09f1cb14ef58a098`。
- 账号目录：`.dev/codex-home`（Git 忽略）。本轮没有打开、复制或提交 `auth.json`；所有启动都显式设置 `CODEX_HOME` 并移除名称含 TOKEN／API_KEY／SECRET／PASSWORD 的环境变量。
- `codex` 不在 PATH。Host 通过绝对路径启动；这证明 PATH 缺失不等于目标不可运行。

版本门在取得单实例锁、打开数据库、创建配对库或推进 epoch 之前执行。版本不符、不可执行、探测超时或输出格式错误均以稳定 code 拒绝，且不产生状态。

## 测试装置

`host/tests/host_real_peer.py` 在子进程运行真实 `HostRuntime`：

1. 在 `.dev` 临时目录用 `execution_probe.prepare_fixture` 建立一次性 Git 夹具；
2. 播种 session 与 workspace（`workspace-write`）；
3. Host 启动后通过产品 `thread_starts` 队列创建真实 Codex thread，等待 `get_session_thread` 读到绑定；
4. 通过产品命令邮箱提交 `start_run`；
5. 父测试在确定性标记处终止 Host 进程；
6. 重启 Host 读取数据库并正常停止。

`host/tests/codex_relay.py` 是测试用 JSONL 行转发器：

- starting：转发 `turn/start` 给真实 Codex 后扣住 Codex→Host 输出；
- approval_dispatched：转发审批答复后扣住输出；
- interrupting：转发 `turn/interrupt` 后扣住输出；
- 两条管道共享转发锁，先原样转发并 flush，再写标记并冻结下游；
- 冻结后继续读取并丢弃 Codex 输出，保持 relay 存活，避免 Host 提前看到 EOF；
- relay、真实 Codex 与 bootstrap launcher 都在同一 Windows Job 中；
- 标记只表示字节已写入真实 Codex 管道，不表示 Codex 已解析或副作用已生效。
- 三个门控切点按 relay 代码分别写 `turn_start`、`approval`、`interrupt`；本轮成功运行的审批操作只有一条且重放未新增派发。后续测试已在杀点前读取答复计数并断言为 1，本轮证据 JSON 生成于该断言加入之前。

running 与 waiting_approval 使用直接 Codex 命令并启用 Host 版本门，没有 relay。

固定提示类别：running、starting、interrupting 使用“不调用工具、输出长篇编号列表”的模型回合，让 turn 保持 running；waiting_approval 与 approval_dispatched 使用仅执行 `python --version` 的命令提示，以产生真实命令审批。提示正文和模型输出不进入仓库。

## 切点结果

公共结果：Host A 恢复后 epoch 为 2；崩溃时 epoch 保持 2；父测试先确认所有观测身份隔离，才启动 Host B（epoch 3，停止 4）和 Host C（启动 5，停止 6）。每个切点只有一个 `run.created`，旧回合 `interrupted`，没有自动重跑。

| 切点 | 杀点 | 崩溃前持久状态 | 重启后持久状态 |
|---|---|---|---|
| starting | relay 已把 `turn/start` 写入 Codex 管道并冻结响应 | run 操作 `dispatching`，无 `run.started` | run 操作 `unknown/dispatch_unconfirmed`；无 `run.started`；无上游 turn 绑定 |
| running | 邮箱快照 `active_run.status=running` | run 操作 `confirmed/turn_started`；turn 已绑定 | run 操作保持 confirmed；`upstream_turns` 保留；回合 `interrupted` |
| waiting_approval | 快照 `waiting_approval` 且 `open_approval_ids` 非空 | 审批 `pending`，版本 1 | 审批 `expired`（版本 2）；快照清空；无审批操作；重放返回 `approval_expired` |
| approval_dispatched | relay 已把审批答复写入 Codex 管道并冻结输出 | 审批 `dispatching`（版本 3），操作 `dispatching` | 审批 `unknown`（版本 4），操作 `unknown/dispatch_unconfirmed`；ID 保留；重放返回原 unknown 操作，不新增操作 |
| interrupting | relay 已把 `turn/interrupt` 写入 Codex 管道并冻结输出 | 中断操作 `dispatching` | 中断操作 `unknown/dispatch_unconfirmed`；无伪造 `turn.completed` |

`approval_dispatched` 的决定为 `accept`（产品 `allow_once`）。重放使用新的 request id；持久化操作仍只有一条，审批仍为 unknown，没有新的队列派发。

## 进程身份与隔离

父测试在杀 Host 前对 Host 子进程的后代做三次 Toolhelp32 快照（间隔 100 ms），记录 PID、父 PID、exe 名和创建时间；随后终止 Host，等待每个记录身份变为 `absent`、`exited` 或 `replaced`。下表是实际观测到的全部后代，不是 Job 成员的完整枚举。

| 切点 | 角色 | PID | 创建时间（FILETIME） | 杀后结果 |
|---|---|---|---|---|
| starting | launcher（数据库记录） | 28832 | 134374981895949197 | absent |
| starting | relay（python） | 30632 | 134374981896819523 | absent |
| starting | 真实 Codex | 34492 | 134374981898034886 | exited |
| running | launcher（数据库记录） | 5496 | 134374981313277422 | absent |
| running | 真实 Codex | 61612 | 134374981314189240 | exited |
| waiting_approval | launcher（数据库记录） | 72440 | 134374981432530607 | exited |
| waiting_approval | 真实 Codex | 26328 | 134374981433422418 | exited |
| waiting_approval | codex-code-mode-host | 56872 | 134374981489489507 | exited |
| waiting_approval | conhost | 66160 | 134374981489641727 | exited |
| approval_dispatched | launcher（数据库记录） | 22280 | 134374981600728963 | absent |
| approval_dispatched | relay（python） | 39604 | 134374981601705185 | exited |
| approval_dispatched | 真实 Codex | 3996 | 134374981602990479 | exited |
| approval_dispatched | codex-code-mode-host | 73348 | 134374981656274778 | exited |
| approval_dispatched | conhost | 6816 | 134374981656407787 | absent |
| interrupting | launcher（数据库记录） | 51328 | 134374981783707447 | absent |
| interrupting | relay（python） | 26164 | 134374981784677537 | exited |
| interrupting | 真实 Codex | 33956 | 134374981786041804 | exited |

说明：

- “launcher”是 Host 持久记录的 bootstrap 进程，不是 Codex 本身；真实 Codex 及各后代由快照记录。
- 真实 Codex 在等待审批和决定派发两个切点观测到 `codex-code-mode-host.exe` 与 `conhost.exe` 后代；这些进程都被同一 Job 收敛。其他切点的快照窗口内未观测到，不代表不会派生。
- 快照窗口内新创建的后代可能未被列出；Job 仍会随 Host 终止关闭。不能把上表表述为进程树的绝对完整成员集合。
- 没有留活进程需要测试额外清理。

## 真实运行发现的问题

首次用真实 Codex 运行时，Host 在 `run_start_fault` 恢复后退出。原因是 Codex 新建 thread 在首次 `turn` 之前没有 rollout，`thread/resume` 返回 `no rollout found for thread id ...`；产品此前总是在首次 `turn/start` 前无条件 resume，因此“新建会话后立即开始第一回合”在真实上游不可用。

修复：`StdioClient` 记录本连接创建的 thread；产品 `thread/start` 成功后标记；`resume_session_thread` 对同一连接新建的 thread 跳过 resume，首次 `turn/start` 直接使用该 thread。Host 重启后该内存标记消失，仍必须成功 resume，保持失败关闭。详见 [ERR-028](../ERRORS.md)。

本轮随后重新运行五个切点均通过；上述失败运行在首回合前被 resume 拦截，没有进入模型回合，也没有计入成功证据。

## 验证命令与记录

离线检查：

```powershell
python scripts/check.py
```

真实测试（显式启用，产生真实模型回合）：

```powershell
$env:PYTHONPATH = "$PWD\host\src;$PWD\host\tests"
$env:RUNVORI_REAL_HOST_TESTS = "1"
$env:RUNVORI_REAL_CODEX = "$env:LOCALAPPDATA\OpenAI\Codex\bin\2993767bd1dfb009\codex.exe"
$env:RUNVORI_REAL_CODEX_HOME = (Resolve-Path .dev/codex-home).Path
$env:RUNVORI_REAL_EVIDENCE_DIR = (Resolve-Path .dev).Path + "\real-evidence"
python -m unittest discover -s host/tests -p "test_host_restart_real.py" -v
```

本轮先运行 1 次等待审批，因 ERR-028 在 resume 阶段失败（未进入模型回合）；修复后逐个切点运行五个切点，各 1 次真实模型回合通过，共 5 次真实模型回合，没有自动重试。脱敏结构证据保存在本地 `.dev/real-evidence/{scenario}.json`，运行日志在 `.dev/real-{scenario}.log`，均被 Git 忽略，不包含提示、模型输出、认证或工作区正文。

离线结果见 [progress.md](../../progress.md)：336 项测试运行（333 通过、3 项跳过），全量检查连续 6 次通过。真实测试默认属于 1 项跳过的测试类。

## 限制与未完成项

- **AT-16 未整体通过**：首发 Host 操作系统仍未决定；本证据只覆盖当前 Windows 11 开发机。
- starting、approval_dispatched、interrupting 的杀点由测试 relay 保持；它证明 Host 把消息写入真实 Codex 管道后崩溃，不证明 Codex 已解析、执行或产生副作用。running 与 waiting_approval 是直连真实 Codex。
- 真实 thread 通过产品 `thread/start` 创建（策略 `on-request`），但审批决定入口没有审批详情读取；真实测试只对固定的一次性夹具“盲批”。手机审批资源读取、HTTP/TLS 与 Android 联调仍开放。
- 真实模型回合需要网络、账号和费用；默认离线检查只跳过这组测试，不把它们计为通过。
- 进程树是杀前三次快照的并集；breakaway 进程或快照后新创建的后代不在证据内。
- 版本探测与实际启动之间存在 TOCTOU；SHA-256 只标识本次使用的文件，不提供不可替换保证。
- 没有测试真实 Codex 正常退出且后代继承 stdio 的 EOF 场景；[ERR-027](../ERRORS.md) 仍开放。
- 本轮未提交；证据不能引用最终提交 SHA。
