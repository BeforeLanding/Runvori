# DEV-007 真实 Codex 设备验收（1 次模型回合）

日期：2026-10-27。需求关联：FR-01、FR-02、FR-03、FR-04、FR-05、FR-06、FR-08；路线图：M1 最小协议 → M2 Android 主流程。项目仍处 M0。

本轮用**真实 APK → 固定指纹 HTTPS → 真实 `HostRuntime` → 真实 Codex app-server** 完成一次设备验收。只发起 **1 次**真实模型回合，未自动重试，未在失败后改断言重跑。**DEV-007 状态：核心真实链路通过，剩余专项待验收**（见文末限制）。九场景合成上游证据见 [合成设备验收](DEV-007-device-acceptance.md)；真实模式编排的版本门、场景白名单与回合计数修正见 [ERR-031](../ERRORS.md)。

## 环境

- OS：Windows 11 Home China 10.0.26200；Python 3.12.10。
- Codex：`codex-cli 0.164.0-alpha.11.1`，固定绝对路径，启动前经 Host 版本门核验；专用已登录 `CODEX_HOME`（仓库 `.dev/codex-home`，不入库）。测试环境变量中的 token/API key/secret/password 在启动上游前清除。
- 模拟器：`Medium_Phone`，API 37、x86_64、Android 17；APK `dev.runvori.app` debug。
- Host：真实 `HostRuntime` 子进程，`expected_upstream_version` 生效，TLS 回环证书、命名管道管理入口、HTTPS 监听 `127.0.0.1:0`；工作区是一次性 git fixture。
- 客户端：真实 APK，经模拟器自身 NAT 的 `10.0.2.2` 到达宿主机回环；证书 SAN 命名该地址，未放宽主机名或指纹校验。
- 命令（路径用占位符，实际为固定绝对路径）：

```text
RUNVORI_REAL_HOST_TESTS=1 \
RUNVORI_REAL_CODEX=<pinned codex.exe> \
RUNVORI_REAL_CODEX_HOME=<dedicated CODEX_HOME> \
python scripts/accept_device.py --serial emulator-5554 --real \
  --report .dev/dev007-real-1call.json
```

- 运行次数：**1 次**；未自动重试。报告 `expected_model_turns=1`、`observed_runs=1`。这里的“1 次”指 Host 接受并派发的一个真实回合（`runs` 行数），不是 Codex 内部 token 级调用计数。

## 提示词与审批范围

真实模式复用已由 [DEV-006 真实 HTTPS 验收](DEV-006-https-host-real.md) 验证过的 `ACCEPT_PROMPT`：

> This is a disposable Runvori protocol probe. In this workspace, run only the exact command `python --version` and report its result. Do not use other commands, access paths outside this workspace, or use the network.

- 工作区：一次性 fixture（git 初始化 + `.runvori-disposable-fixture`），登记为 `workspace-write`；`thread/start` 使用 `approvalPolicy=on-request`、`approvalsReviewer=user`。
- 只处理 `item/commandExecution/requestApproval`；只允许 **`allow_once`**，不做持久允许。
- 允许前必须同时满足：`details_status=complete`、命令非空、`cwd_inside_workspace=true`、`allowed_decisions` 含 `allow_once` 与 `deny`。任一不满足则验收失败，不降级为允许。
- 不触发文件审批、网络审批或 `apply_patch`；不使用 `[expire-request]`、`[file-change]`、`[interrupt]` 等合成上游专用标记。

## 场景与断言

真实模式只跑 `s / a / b / c / g / e`；`d / f / h` 被脚本拒绝，因为其依赖合成上游的 `[expire-request]`、`[file-change]`、`[interrupt]` 时序。

| 场景 | 模型回合 | 本轮通过的断言 |
|---|---:|---|
| `s` | 0 | Keystore 凭据往返；落盘为密文；`clear()` 后读不到 |
| `a` | 0 | 篡改指纹后在写请求前拒绝；未存凭据；待核对载荷仍留在屏幕 |
| `b` | 1 | 从手机列出两个会话并打开 `devsess0`；真实 `ACCEPT_PROMPT` 受理；审批详情 `complete`、命令非空、cwd 在登记工作区内、`allowed_decisions=[allow_once,deny]`；双击只产生一个决定操作；`allow_once` 后操作 `confirmed`、审批 `resolved`、回合 `run.completed`；时间线命令 exit 0 且输出非空；Agent 文本非空 |
| `c` | 0 | 复用 `b` 在 `devsess0` 的缓存历史；丢弃内存连接后重建 ViewModel 重同步：seq 连续、无重复、无缺口、终态事件仍在 |
| `g` | 0 | 至少存在一个审批操作；同一 `request_id` 连续查询两次状态稳定 |
| `e` | 0 | Host 侧撤销设备后旧令牌被拒绝，UI 显示需重新配对 |

`b` 的断言不依赖合成文本：不再要求命令串等于 `python --version`、输出含 `3.12` 或 Agent 文本含“合成完成”。报告记录 `command_match_asserted=false`；Windows 上真实命令串可能带 shell 包装（[ERR-007](../ERRORS.md)、[ERR-016](../ERRORS.md)），本轮只断言“命令非空 + cwd 在登记工作区内 + 详情完整”，不宣称与探针字面量一致。

## 结果

`status: passed`，退出码 0：

- 真实 Host 启动，版本门通过；工作区 fixture 登记；两个会话的 thread 绑定完成后才发送首个真实回合。
- 真实模型回合受理；审批链路完整；决定只派发一次；操作 `confirmed`；回合 `run.completed`。
- 时间线投影在真实 item 事件下可用：命令 exit 0、输出非空、Agent 文本非空。
- 设备重同步与操作查询稳定；撤销后拒绝。
- `observed_runs=1`，与 `expected_model_turns=1` 一致；没有额外模型回合。

脱敏报告：[DEV-007-device-acceptance-real.json](DEV-007-device-acceptance-real.json)。报告只有场景、状态、端口、会话数和计数；没有令牌、配对秘密、提示词正文、命令正文或绝对路径。

改完真实模式分支后，合成九场景也重新跑了一轮全绿，确认真实分支没有破坏原合成断言；该合成轮次不产生模型调用。

## 未完成与限制

- **真实模式未覆盖 `d / f / h`**：`[expire-request]`、`[file-change]`、`[interrupt]` 是合成上游时序。真实中断/过期需要第二次显式授权的模型回合；真实文件审批的 `complete`/`insufficient` 路径仍未覆盖。
- **只跑了 1 次真实回合**：不证明批量稳定性，也不证明其他审批类型；失败不重试是本轮约定。
- **命令字面量未断言**：`command_match_asserted=false`，不能据此宣称展示串等于 `python --version`。
- **未覆盖**：真实文件审批、命令/产物明细端点（命令明细与 `/results` 已由后续 DEV-008 补齐；产物仍未实现）、旋转/切后台/系统杀进程/清除缓存/200% 字体/TalkBack/性能、物理手机可达路径、证书轮换与过期。
- **AT-16 仍未整体通过**：首发 OS 未定；starting/决定派发/中断切点仍由测试 relay 注入；真实上游正常退出且后代继承 stdio 的 EOF 检测（[ERR-027](../ERRORS.md)）未覆盖。
- 本轮没有自动重试；若后续运行失败，报告会保留失败场景与断言，不会改写成通过。
