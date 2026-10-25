# 开发进度与下一步计划

最后更新：2026-10-25（Asia/Shanghai）。本文件是当前进度的维护入口；历史变化见 [CHANGELOG.md](CHANGELOG.md)，错误与修复见 [错误记录](docs/ERRORS.md)。

## 当前阶段

**DEV-003 已观察真实命令与文件审批、受控执行、进程切断、命令完成后中断的两种回合结局、脏工作区中的固定文件新增、固定文件移动与二进制命令写入、验证命令的通过与非零失败，并完成 Git 工作区大文件等本地元信息探针；确认丢失有合成故障注入，审批派发持久边界有独立 SQLite 行为验证。DEV-004 的 Android 占位 APK 已完成本机构建和模拟器安装启动。Q-04 已复验包安装、单实例锁及持锁打开审批记录；Q-05 已完成回环 TLS 身份、一次性配对记录及二者的本地 HTTPS 联动探针。产品 Host 集成与 M0 退出条件仍未完成。**

当前可运行的是本地技术验证工具和 Android 静态占位 APK，不是可供手机连接的产品。没有 FastAPI 业务 API、数据库任务管理、TLS 配对或 Android 业务交互。PRD 的 AT-01～AT-17 尚未完成产品验收。

## 已完成

- DEV-005 / FR-02、FR-04、FR-09 单库审批请求结束前提：核对 epoch、上游请求标识与回合绑定后，`pending/decision_recorded` 审批失效，未领取操作记 `failed/request_closed_before_dispatch`；`dispatching/unknown` 审批记 `resolved`，操作只确认 `request_closed`，不推断允许已生效或命令成功。审批/操作/事件/快照同事务，重复通知不追加事件。16 份契约通过，149 项测试运行（144 通过、5 项本环境限制跳过）。仍处 M0，真实上游通知映射与产品 API 尚未接入；见 [验证记录](docs/evidence/DEV-005-approval-request-closed.md)。
- DEV-005 / FR-02、FR-04、FR-09 审批隔离后恢复前提：可信调用方确认旧进程已隔离后，单事务处理所有未完成审批：`pending`/`decision_recorded` 失效，未领取操作记 `failed/not_dispatched`，`dispatching` 审批与操作记 `unknown/dispatch_unconfirmed`；逐项同步事件/快照并推进 epoch。空库启动、未隔离拒绝、多审批中途失败回滚、跨重开和后续回合中断均有行为测试。16 份契约通过，145 项测试运行（140 通过、5 项本环境限制跳过）。仍处 M0，真实进程隔离、上游确认和产品启动编排尚未接入；见 [验证记录](docs/evidence/DEV-005-approval-epoch-recovery.md)。
- DEV-005 / FR-02、FR-04、FR-09 单库审批派发领取前提：`accepted` 操作及 `decision_recorded` 审批在一个事务中推进为 `dispatching`，提交后才授予一次发送机会；结果不确定可原子记为 `unknown`，均不可再次领取。跨重开、旧 epoch 与第二事件写入失败均有行为测试。16 份契约通过，142 项测试运行（137 通过、5 项本环境限制跳过）。仍处 M0，真实发送、上游确认和新 epoch 的统一恢复尚未接入；见 [验证记录](docs/evidence/DEV-005-approval-dispatch.md)。
- DEV-005 / FR-02、FR-04、FR-09 单库审批决定事务前提：事件库 schema v5 记录审批操作的 actor/request 摘要；决定、操作、两条事件和快照在同一事务。响应丢失重开重试返回原操作，冲突请求、旧版本、失效 epoch 和第二事件失败回滚均有行为测试。16 份契约通过，138 项测试运行（133 通过、5 项本环境限制跳过）。仍处 M0，审批派发、结束与恢复尚未纳入单库事件路径；见 [验证记录](docs/evidence/DEV-005-approval-decision.md)。
- DEV-005 / FR-02、FR-04、FR-09 单库审批请求事务前提：持锁启动切片改用 `host.db` 的共享 SQLite 连接；旧 `approvals.db`/`events.db` 存在时拒绝创建新库。活动回合的审批请求、`approval.requested`、`waiting_approval` 与快照在同一事务；事件失败回滚审批与回合。16 份契约通过，134 项测试运行（129 通过、5 项本环境限制跳过）。仍处 M0，旧双库数据迁移、审批决定/派发和产品 API 未实现；见 [验证记录](docs/evidence/DEV-005-unified-store.md)。
- DEV-005 / FR-02、FR-09 持锁启动前提：新增 `LockedHostJournals`，同一 Windows 单实例锁下按序打开审批与事件库；争锁不碰 SQLite，第二库失败关闭第一库并释放锁，真实持锁进程被终止后重开仍保留活动槽与审批 epoch。16 份契约通过，131 项测试运行（126 通过、5 项本环境限制跳过）。仍处 M0，两个库尚无跨库事务，旧进程隔离和产品启动未接入；见 [验证记录](docs/evidence/DEV-005-locked-startup.md)。
- DEV-005 / FR-09、FR-02 旧回合恢复事务前提：可信调用方声明旧进程已隔离后，按 `run_id`/版本/代次核对活动回合；未派发操作记 `failed`，已领取未确认操作记 `unknown`，已确认启动事实保留；回合中断、事件、快照与活动槽释放同事务提交。未隔离、错代次、写入失败及旧回合误释放均有行为测试。16 份契约通过，128 项测试运行（123 通过、5 项本环境限制跳过）。仍处 M0，真实进程隔离和审批恢复未接入；见 [验证记录](docs/evidence/DEV-005-run-recovery.md)。
- DEV-005 / FR-02、FR-08、FR-09 上游回合确认前提：事件库 v4 在派发领取时记录进程代次，确认时关联 thread/turn，单事务写 `run.started`、`operation.updated`、`running` 回合与 `confirmed` 操作；同一关联重复幂等，错代次、冲突归因及两条事件任一写入失败均有行为测试。16 份契约通过，122 项测试运行（117 通过、5 项本环境限制跳过）。仍处 M0，仅验证本地合成关联，未接入真实 Codex 通知或进程身份核对；见 [验证记录](docs/evidence/DEV-005-turn-confirmation.md)。
- DEV-005 / FR-02、FR-08、FR-09 单次派发领取前提：新回合操作从 `accepted` 到 `dispatching` 的领取与事件/快照更新在同一事务；发送不确定时持久记 `unknown`，不重领且不释放活动槽。跨重开、双连接竞争、插入失败回滚与终态拒绝均有行为测试。16 份契约通过，115 项测试运行（110 通过、5 项本环境限制跳过）。仍处 M0，未连接真实 RPC、进程隔离或确认结果；见 [验证记录](docs/evidence/DEV-005-run-dispatch.md)。
- DEV-005 / FR-02、FR-08 新回合请求幂等前提：事件库 v3 把 actor 与 `request_id`、规范化请求摘要、活动槽、回合、事件和快照放在同一事务；相同请求跨重开返回原 `accepted` 操作，不同内容冲突，双连接重试只建一个回合。16 份契约通过，110 项测试运行（105 通过、5 项本环境限制跳过），离线 wheel 隔离安装和新增模块导入通过。仍处 M0，尚无正式 HTTP、上游 turn 确认或操作状态推进；见 [验证记录](docs/evidence/DEV-005-run-idempotency.md)。
- DEV-005 / FR-02、FR-08、FR-09 的 M1 活动槽前提：在事件库 v2 中以单事务预留全局活动回合、写 `run.created` 与快照，并在终态事务中按回合 ID 释放；并发创建、提交失败回滚、终态重复与旧回合隔离、v1 数据升级均有行为测试。16 份契约通过，104 项测试运行（99 通过、5 项本环境限制跳过）。仍处 M0，未连接 Codex RPC、操作幂等、重启核对或正式 API；见 [验证记录](docs/evidence/DEV-005-active-slot.md)。
- DEV-005 / FR-03、FR-06 的 M1 存储前提：新增独立 SQLite 事件与快照事务切片，连续序号、失败回滚、双连接并发、增量/历史游标及字节分页均有行为测试；16 份契约通过，98 项测试运行（93 通过、5 项因本环境限制跳过）。仍处 M0，尚无上游映射、正式 Host API、长轮询或 Android 同步；见 [验证记录](docs/evidence/DEV-005-event-journal.md)。
- M0 / Q-05 HTTPS 联动前提：新增仅绑定 `127.0.0.1` 的一次性夹具；客户端先核对 TLS 链、主机名和本地 SPKI 指纹，再以 POST body 兑换秘密。错误指纹无 HTTP 请求；提交后丢失响应，同一秘密重放被拒；本地撤销后重新配对成功。获准环境下 91 项测试运行（89 通过、2 项符号链接创建限制跳过），16 份契约通过。尚无正式 Host API、限流或 Android 联调，见 [联动证据](docs/evidence/M0-Q05-https-pairing.md)。
- M0 / Q-05 配对持久边界：新增独立 SQLite 切片，32 字节随机秘密、300 秒到期、摘要存储、事务内一次性消费及唯一有效设备；测试覆盖响应丢失后重放、到期、并发兑换、本地撤销及旧令牌失效。获准环境下 90 项测试运行（88 通过、2 项符号链接创建限制跳过），16 份契约与当前 wheel 安装通过。没有 API、限流、TLS 绑定或 Android 凭据存储，见 [配对证据](docs/evidence/M0-Q05-pairing-journal.md)。
- M0 / Q-05 TLS 身份前提：新增真实回环握手探针，保留证书链与主机名验证后核对本地预期 SPKI；匹配指纹通过，换公钥、错误主机名和不受信任证书均在发送应用字节前拒绝。该阶段 86 项测试运行（获准环境下 84 通过、2 项符号链接创建权限限制跳过），16 份契约通过。Android 专用信任、产品配对与轮换流程未实现，见 [TLS 证据](docs/evidence/M0-Q05-tls-identity.md)。
- M0 / Q-04 持锁启动前提：新增 `LockedApprovalJournal`，先获取 Windows 单实例锁再打开 SQLite；争锁失败不触及数据库，数据库打开失败会释放锁。真实跨进程终止后可重新打开记录且 epoch 不自动推进。获准环境下 85 项测试运行（83 通过、2 项符号链接创建权限限制跳过），16 份契约及当前 wheel 隔离安装通过。旧 app-server 隔离和产品启动集成未完成，见 [启动切片证据](docs/evidence/M0-Q04-locked-journal.md)。
- M0 / Q-04 单实例前提：新增 Windows 锁文件句柄切片，真实跨进程验证第二个进程不能获锁、正常关闭和持锁进程异常退出后可重取；当前 wheel 包含模块并完成隔离安装复验。获准环境下 82 项测试运行（80 通过、2 项符号链接权限限制跳过）、16 份契约通过。尚未接入 Host 启动或 SQLite 活动槽，见 [锁验证证据](docs/evidence/M0-Q04-instance-lock.md)。
- DEV-001 / Q-04 当前包复验：新增离线 wheel、一次性虚拟环境、仓库外运行已安装 CLI 的冒烟脚本；16 份契约校验和当前新增模块导入均通过。旧 `pip --target` 方式的命令入口从仓库外运行失败，已改用真实虚拟环境验证。单实例锁的产品集成与 Host 打包未完成，Q-04 仍开放。见 [包复验证据](docs/evidence/DEV-001-package-recheck.md)。
- DEV-003 / Q-01、Q-07 Windows 路径边界：新增真实 junction 回归，在一次性夹具中让工作区子目录指向外部固定文件；产物读取拒绝该链接，未知审批文件核对记录 `unsafe_path`，目标字节未变。获准环境下 80 项测试运行（78 通过、2 项符号链接创建权限限制跳过），16 份契约校验通过。真实符号链接与检查/读取并发替换仍未验证，见 [复验证据](docs/evidence/DEV-003-windows-junction.md)。
- DEV-004 / Q-03 模拟器冒烟验证：API 37、x86_64 的现有 AVD 上安装 Debug APK 返回 `Success`，冷启动 MainActivity 返回 `Status: ok`，进程与前台 Activity 均核对，截图显示预期占位文案；增加固定包名的可重复检查脚本。没有实机、最低 API 26 或业务功能验证，Q-03 未关闭。见 [安装与构建证据](docs/evidence/DEV-004-toolchain.md)。
- DEV-004 / Q-03 构建基线：新增单应用 Android Compose 占位工程、Gradle 8.13 wrapper 和固定 AGP/Kotlin/Compose 依赖；本机 JDK 21、SDK 36 构建 `:app:assembleDebug` 成功，APK 包名与 SDK 声明经 `aapt` 核对。最低 API 26 暂定，Q-03 未关闭。命令、版本和 APK 哈希见 [构建证据](docs/evidence/DEV-004-toolchain.md)。
- DEV-003 / FR-07、AT-14 技术前提：固定 2,048 字节二进制文件的真实命令写入已验证。CLI `0.164.0-alpha.11.1` 在可丢弃 Git 仓库中对精确命令发起一次审批并获 `accept`；对应命令 item 结构化退出码为 0，文件字节完全匹配，Git `numstat` 为二进制标记，受控产物探针仅返回元信息、不提供正文。79 项测试运行（77 通过、2 项链接环境限制跳过），16 份契约校验通过。见 [脱敏证据](docs/evidence/DEV-003-binary-command.json)。这是命令审批与工作区快照，不是上游二进制 `fileChange` 事件或 Agent 通用归因。
- DEV-003 / FR-07、AT-14 技术前提：固定单文件移动的真实一次性允许探针已通过。上游 `fileChange` item 的唯一变更须精确匹配源/目标绝对路径、`update`/`move_path`、完整补丁及原文件字节；审批请求的 thread/turn/item 和 `grantRoot` 也须核对，额外变更与命令请求拒绝。真实 CLI `0.164.0-alpha.11.1` 中，一次文件审批获 `accept`，同一 item 为 `completed`，源文件消失，目标字节精确匹配，Git 只报告原文件删除与目标未跟踪新增，回合完成且 thread 可恢复。78 项测试运行（76 通过、2 项链接环境限制跳过），16 份契约校验通过。见 [脱敏证据](docs/evidence/DEV-003-file-move-accepted.json)。这只覆盖固定移动，不证明通用移动归因或 Host 产物存储。
- DEV-003 / FR-07、AT-14 技术前提：新增仅拒绝的真实 Codex 文件移动探针。CLI `0.164.0-alpha.11.1` 的可丢弃 Git 仓库中，`item/started` 的单一 `fileChange` 含 `update` 与匹配的 `move_path`；随后收到一次文件修改审批，明确 `decline`，再见请求结束、item 结束和回合 `completed`。原文件字节未变、目标文件不存在、thread 可恢复；不据回合完成推断改动成功。77 项测试运行（75 通过、2 项链接环境限制跳过），16 份契约校验通过。见 [脱敏证据](docs/evidence/DEV-003-file-move-declined.json)。移动批准、上游实际改动和 Host 归因仍未验证。
- DEV-003 / FR-07、AT-14 技术前提：新增可丢弃 Git 重命名探针。相同内容的两组移动中，已暂存 `git mv` 由 Git 明确报告 `R100`；仅在工作区移动的文件显示为删除加未跟踪新增，不能据字节相同认定为重命名或归因于 Agent。结果保留 `git_worktree_snapshot` 来源及 `unknown` 归因，夹具被改动后拒绝报告。76 项测试运行（74 通过、2 项链接权限限制跳过），16 份契约校验通过。见 [脱敏证据](docs/evidence/DEV-003-rename.json)。Codex 上游重命名语义与 Host 差异存储仍未验证。
- DEV-003 / FR-04、FR-09、AT-05、AT-16 技术前提：收紧独立 SQLite 审批派发切片中的未知结果文件核对。先确认 `unknown`，再验证指定工作区内的相对路径及逐段链接边界，有界读取；越界输入不读取文件。75 项测试运行（73 通过、2 项符号链接权限限制跳过），16 份契约校验通过。详见 [审批持久边界](docs/evidence/DEV-003-approval-journal.md)。未接入 Host，链接父目录的真实拒绝仍待具备权限的环境复验。
- DEV-003 / FR-07、AT-14 技术前提：新增本地 Git 差异产物探针。在预先存在的修改中，131,072 字节文本文件超过 64 KiB 内容上限，报告大小/哈希及明确截断；2,048 字节二进制文件只有元信息，Git `numstat` 给出二进制标记。超过 4 MiB 的文件不扫描并标记哈希不可用；小 UTF-8 文本可捕获。来源始终为 `git_worktree_snapshot`、归因为 `unknown`，未声称 Agent 修改。73 项测试运行（72 通过、1 项真实符号链接创建因本机权限跳过），16 份契约校验通过；路径穿越和夹具篡改检查通过。见 [脱敏证据](docs/evidence/DEV-003-artifact-boundaries.json)。不等于上游文件改动或产品产物存储已验证。
- DEV-003 / FR-07、AT-13 技术前提：固定 `exit 7` 在可丢弃工作区中获一次真实命令审批，结构化退出码精确为 7；上游回合仍为 `completed`，但必需验证项及汇总均为 `failed`。测试仓库未新增文件，thread 可恢复。补充对上游命令 item `failed` 且非零退出码的证据判定；70 项测试与 16 份契约校验通过。见 [失败路径脱敏证据](docs/evidence/DEV-003-nonzero-verification.json)。任意命令登记与 Host 持久化未实现。
- DEV-003 / FR-07、AT-13 技术前提：建立多项必需验证命令汇总器，沿用协议的 `failed > running > unknown > not_run > pending > passed` 优先级；空登记表为 `not_run`，同一上游 item 不能充当两项证据。真实可丢弃仓库中，`python --version` 与 `git --version` 分别获得单次审批、结构化完成项与退出码 0，逐项和汇总均为 `passed`。合成测试覆盖非零、缺失、执行中和重复身份；已确认非零退出码在回合仍运行时立即使汇总为 `failed`。见 [脱敏证据](docs/evidence/DEV-003-two-required-verifications.json)。当前 16 份契约、68 项测试通过；Host 状态持久化与产品验收仍未实现。
- DEV-003 / FR-07、AT-13 技术前提：先建立单条预登记命令的结构化证据判定。严格关联 thread/turn/item、命令、cwd、开始/完成时间、状态及整数退出码；未运行、执行中、审批后缺失执行项、证据缺失或重复匹配不会误报通过。真实固定只读命令获一次审批，退出码 0，判定 `passed`。锁定新增两份 item 通知 schema；该阶段 16 份契约和 61 项测试通过。见 [脱敏证据](docs/evidence/DEV-003-required-verification.json)。Host 持久化未实现。
- DEV-003 / FR-07、AT-14 技术前提：建立含预先修改的跟踪文件和预先存在的未跟踪文件的可丢弃 Git 夹具。真实文件修改审批仅允许精确新增 `probe-result.txt` 一次；上游 item 与回合完成，目标字节完全匹配，原有文件未变。Git 状态含两项未跟踪文件，而普通 `git diff --name-only` 只显示跟踪文件；差异归因须结合前态、上游 item、审批和文件核对。CLI 升级到 `0.164.0-alpha.11.1` 后审查并重导出 schema，53 项测试、14 份契约及隔离握手通过。见 [脏工作区证据](docs/evidence/DEV-003-dirty-workspace.json) 和 [版本审查](docs/evidence/DEV-003-schema-review.md)。
- DEV-003：固定只读命令 item 退出码 0 后，分别立即、延迟 1 秒和延迟 5 秒发起真实中断。前两次 RPC 返回且回合 `interrupted`；5 秒切点 RPC 返回脱敏错误，传输仍读到 `completed` 并成功恢复 thread。命令 item 成功、RPC 答复和回合终态分别记录，不推断上游精确并发时间。修复匹配的 RPC 错误误使传输失效的问题；脱敏证据见 [DEV-003](docs/evidence/DEV-003.md)，52 项测试及 14 份 schema 校验通过。
- DEV-003：新的可丢弃仓库中，固定只读命令获一次允许、退出码 0，回合真实终态先为 `completed`；其后一次 `turn/interrupt` 返回脱敏 RPC 错误，探针仍保留完成终态。此为明确先后顺序，不能等同于并发竞争；RPC 错误后未恢复 thread。脱敏证据见 [DEV-003](docs/evidence/DEV-003.md)；49 项测试及 14 份 schema 校验通过。
- DEV-003：新增独立 SQLite 审批派发验证切片。决定先落库、派发前原子领取；数据库重开后的新 epoch 使旧未领取决定失效、已领取决定保持 `unknown`，不自动重发。测试覆盖客户端请求幂等、相反决定、双连接竞争、上游 ID 跨 epoch 复用，以及未知结果的只读文件核对。证据见 [审批持久边界](docs/evidence/DEV-003-approval-journal.md)；47 项测试及 14 份 schema 校验通过。尚未连接 Host API、进程所有权或真实 Codex 派发。
- DEV-003：只读可丢弃仓库中，固定文件修改审批获单次 `accept` 后立即关闭受管 app-server。真实运行最后观察到审批请求，进程关闭返回；未收到文件 item 或回合终态，也未恢复 thread。关闭后目标文件不存在，仓库无额外文件；结果保持未知，不据此推断上游是否处理答复。脱敏证据见 [DEV-003](docs/evidence/DEV-003.md)；41 项测试与 14 份 schema 校验通过。
- DEV-003：合成 peer 在收到审批答复后、模拟副作用前后两个切点关闭连接且不发请求结束通知。两种情况传输层都只发送一次，后续操作被禁用；效果不同而可见传输错误相同，必须保持结果不确定。脱敏证据见 [DEV-003](docs/evidence/DEV-003.md)；当前 40 项测试通过。真实 Codex 与生产 Host 恢复未验证。
- DEV-003：只读可丢弃仓库中，文件改动 item 的路径、类型、完整内容与审批请求关联核对后，真实发送一次 `accept`；上游 item 和回合均 `completed`，目标文件 17 字节完全匹配，仓库仅新增预定文件。脱敏证据见 [DEV-003](docs/evidence/DEV-003.md)；该阶段 39 项测试通过。
- DEV-003：两个新的只读可丢弃仓库均真实出现 `item/fileChange/requestApproval`；显式拒绝后回合 `completed`、thread 可恢复、目标文件不存在，仓库只保留初始标记及 Git 元数据。请求只带 thread/turn/item 等标识和空 `grantRoot`，不直接提供补丁与路径，不能凭请求允许；脱敏证据见 [DEV-003](docs/evidence/DEV-003.md)。该阶段 37 项测试通过。
- DEV-003：在新空测试仓库中，固定长命令获单次 `accept` 后立即发 `turn/interrupt`；真实终态为 `interrupted`，原 thread 可恢复，命令输出标记和退出码均未观察到，不能推断命令已启动。额外审批请求失败关闭，脱敏证据见 [DEV-003](docs/evidence/DEV-003.md)；该阶段 36 项测试通过。
- DEV-003：固定只读命令审批保持未答复时真实发送 `turn/interrupt`；回合终态为 `interrupted`，对应 `serverRequest/resolved` 在终态之后到达，原 thread 可恢复，未批准命令、未生成目标文件。终态后有界观察、非白名单请求失败关闭均已覆盖；该阶段 34 项测试通过。脱敏证据见 [DEV-003](docs/evidence/DEV-003.md)。
- DEV-003：固定命令输出开始标记后发出真实 `turn/interrupt`，RPC 返回，随后收到 `turn/completed: interrupted`，原 thread 可恢复。命令 `item/completed` 与退出码未观察到，故不推断命令验证结果；该阶段 30 项测试通过。脱敏证据见 [DEV-003](docs/evidence/DEV-003.md)。
- DEV-003：在新的可丢弃仓库中，固定 `Set-Content` 命令通过一次真实命令审批，item 退出码 0，目标文件 16 字节完全匹配，回合完成且 thread 可恢复；该场景没有观察到独立文件修改审批。脱敏证据见 [DEV-003](docs/evidence/DEV-003.md)。该阶段 28 项测试通过。
- DEV-003：在同一专用账号与可丢弃仓库中完成真实只读命令的单次 `accept`。严格核对操作、cwd 与回合后，对应命令项真实退出码为 0，回合完成、历史 thread 可恢复；不应用建议的策略修订。脱敏证据见 [DEV-003](docs/evidence/DEV-003.md)。该阶段 27 项测试通过。
- DEV-003：专用账号与可丢弃仓库中完成一次真实回合。出现命令审批，显式 `decline` 后收到 `serverRequest/resolved`，回合终态 `completed`，原 thread 可 `thread/resume`；目标文件未生成，命令退出码缺失，不能把回合完成或请求结束当作执行成功。脱敏形状夹具和环境证据见 [DEV-003](docs/evidence/DEV-003.md)。请求先结束后再审批的竞争现由传输层拒绝；25 项测试通过。
- DEV-003 的显式执行探针已建立：要求专用账号目录及审批决定，仅接受 `.dev` 内带标记的测试仓库，按受限 thread/turn/完成/恢复流程收集脱敏事件形状和文件核对；准备阶段 24 项离线测试通过，后续真实观察见上。
- 本机已在 Git 忽略的 `.dev/codex-home` 完成独立 ChatGPT 登录，`codex login status` 返回已登录；这只满足 DEV-003 的账号前置条件，未证明模型访问或真实审批语义。
- DEV-003 的传输切片：保留命令/文件审批请求与通知，提供显式一次性 `accept/decline` 响应；未知控制请求拒绝，写入超时后不重发。合成时序覆盖审批先于 `turn/start` 响应和响应后到达。22 项离线测试与真实隔离握手通过，见 [DEV-003 记录](docs/evidence/DEV-003.md)。这不是实际模型审批验证。
- DEV-002：Windows kill-on-close Job、启动门控及失败关闭已实现；新增父进程先退出、孙进程持有管道、Host 强制退出、Job 分配失败四项真实进程测试。全量 20 项测试通过，14 份 schema 校验通过，真实 Codex 隔离握手通过，见 [DEV-002](docs/evidence/DEV-002.md)。

- 建立 Python 包、源码启动入口与零第三方运行依赖的离线检查入口。
- 当前探针锁定 Python 3.12.10、Codex CLI 0.164.0-alpha.11.1；旧版 0.163.0-alpha.11.2 的历史证据保留原版本标注。它们是实验兼容基线，不是正式支持矩阵。
- 导出 14 份关键上游 schema，逐文件 SHA-256 校验；CLI 版本不符时拒绝探测，不自动适配未知版本。
- 实现有界 JSONL stdio 客户端：初始化顺序、响应关联、通知、行长/队列限制、读写超时、错误脱敏、失败后禁用连接、进程清理。
- 真实隔离环境通过 initialize → initialized → thread/list；不读取用户 Codex HOME，不发起模型回合。
- 建立协议异常、上游退出、重复初始化、缓冲溢出、确认超时和 schema 篡改的自动测试。
- 离线构建 `runvori_host-0.0.1-py3-none-any.whl` 成功。
- 建立 progress、changelog、错误记录及本轮证据，并把可复用约束写入 AGENTS.md。

## 验证证据

命令、环境、结果与限制见 [DEV-001 验证记录](docs/evidence/DEV-001.md)。仅通过握手不能证明审批、执行恢复或进程树安全；合成测试消息不能冒充真实 Codex 审批夹具。

DEV-001 的原始结果为 16 项自动测试、14 份 schema 校验、真实隔离握手、wheel 离线构建及隔离安装校验；19 份 Markdown 的 85 处内部链接检查无缺失。当前完整离线检查为 149 项测试运行（144 通过、5 项本环境限制跳过）、16 份 schema 校验；DEV-003 的真实命令/文件审批、受控执行与中断、Git 产物和 SQLite 派发边界证据保持有效。Windows 单实例锁、持锁打开 SQLite 的启动顺序、一次性回环 TLS 身份、配对记录及本地 HTTPS 联动路径已验证；当前源码 wheel 的隔离安装及仓库外 CLI 校验已通过。

## 下一步（按依赖排序）

1. **DEV-002 已完成的边界：** Windows 合成进程树生命周期验证通过；真实工作区执行仍需 DEV-003 的审批、权限及中断验证，不直接开放远程操作。
2. **DEV-003 / M0：完成上游证据映射。** 已观察 thread/start、turn/start、审批、受控执行、命令完成后不同延迟中断的两种终态、延迟请求结束、thread/resume、审批答复后的受控进程切断、脏工作区固定新增文件、固定文件移动审批拒绝与单次允许、固定二进制命令写入、两条固定必需验证命令的通过汇总，以及回合 `completed` 同时验证 `failed`。确认丢失有合成双切点测试，持久派发边界有独立 SQLite 测试；大文本仅有本地 Git 元信息，二进制已有固定命令写入与 Git 元信息但没有上游二进制 fileChange 证据，重命名有本地 Git 元信息和固定上游移动证据。下一步验证其余审批时序、真实通知丢失、上游大差异/二进制 fileChange 语义，并冻结 M0 兼容映射；Host 进程隔离与 SQLite 记录的集成留待 M1。
3. **DEV-004 / M0：工具链与证据。** 本机已用固定 Gradle/AGP/Kotlin/Compose 组合构建 Android 占位 APK，并在 API 37 模拟器上安装启动，见 [记录](docs/evidence/DEV-004-toolchain.md)；仍须在干净环境和实机验证构建/安装、确定最低 API、冻结 TLS 配置及差异/验证命令采集策略。
4. **Q-04 / M0：Host 启动约束。** 单实例锁、先锁后打开审批记录和当前包安装已验证；仍须验证产品 Host 全生命周期、旧 app-server 隔离后才推进 epoch、SQLite 活动槽及依赖/部署方式。
5. **Q-05 / M0：配对和 TLS。** 回环信任链、主机名及 SPKI 拒绝路径，一次性 SQLite 配对记录和定向 HTTPS 联动已验证；仍须实现产品 Host、证书/本地指纹展示、Android 信任、限流及轮换流程。
6. **DEV-005 / M1：Host 领域与 SQLite。** 事件/快照、活动槽、新回合幂等、单次派发、上游 turn 确认、旧回合恢复及单库审批请求/决定/派发领取/隔离后 epoch 恢复/请求结束事务已有切片；M0 退出条件满足后仍须完成真实上游通知映射、审批与回合恢复的启动编排、旧双库迁移策略和进程隔离，再开放认证 API。

## 已知限制与风险

- 当前 stdio 客户端仅供顺序调用的短时探针，虽可显式回复已识别的审批请求，仍未实现生产级并发 RPC 路由、事件持久化、请求结束证据持久关联或审批状态机。真实命令消息出现导出 schema 未列出的 `availableDecisions` 字段；文件修改审批请求不直接包含路径或补丁内容，通用映射须关联 item。上游升级需复核。
- 新增 SQLite 切片只验证审批派发状态的本地事务边界；Windows 锁已与审批记录打开顺序组成最小启动切片，但尚无产品 Host、活动槽或进程所有权集成。调用方提供的“旧进程已隔离”条件尚不能由该模块验证，文件核对只记录当时快照，不能证明副作用归因。Windows junction 父目录实际拒绝已复验；符号链接创建测试和检查/读取间替换风险仍开放。
- Windows 已使用不可继承句柄的 kill-on-close Job 和启动门控替代 taskkill。普通子进程继承场景通过本机测试；Job 不能约束经外部服务/WMI 发起的进程，也不是权限沙箱。其他平台和真实 Codex 工具执行链尚未验收，Q-01 仍未整体关闭。
- CLI 为 alpha 版本；上游更新必须重新导出、评审 schema 并验证行为，禁止绕过版本检查。
- Android 目前仅为静态占位工程；仅有 API 37 模拟器安装/启动，未做实机安装或性能基线，NFR 数字仍为目标。
- 安装包仅为探针；需要 checkout 中的契约文件和 `--workspace`，不是独立可部署的 Host 包。

## 每次开发必须维护

记录本轮交付、实际验证、未完成项、下一步依赖和已知风险。发生错误后在 ERRORS.md 记录触发、根因、修复和回归；只有可复用的约束才提升到 AGENTS.md。更新路线图与文档状态，保留失败历史，不把计划写成通过。
