# 设计决策与待验证事项

日期：2026-10-23。这里区分来源于定位的既定边界与本次补充的设计方案；“采用”不等于“已实现/已验证”。

## ADR-001：首版聚焦离席后的移动接管

状态：沿用既有定位。

依据：[定位与首版方案](定位与首版方案.md)。选择 Android 原生客户端、开发电脑 Host、自建 Codex 会话；一台 Host、一台手机、全局一个活动回合。

原因：把稳定阅读、审批一致性和恢复做成有证据的闭环。后果：不支持任意终端、现有外部会话接管、多个并行任务和公网中继；这些不是隐藏的首版任务。

## ADR-002：通过 stdio 复用 Codex app-server

状态：集成方向沿用定位，精确版本与行为待 M0 验证。

选择：Host 管理本地 app-server，Android 仅调用 Runvori 协议。对版本导出的 schema 建立适配层和脱敏夹具，不把上游枚举散落到 UI。

原因：复用执行和 thread/turn/审批能力，把项目工作集中在移动端和可靠状态。后果：需要维护兼容矩阵；未知控制请求不能默认允许；上游协议变化先通过探针与契约测试再升级。

## ADR-003：事件、快照和 HTTPS 长轮询

状态：本次提出，作为首版实现基线；M1 验证。

选择：SQLite 保存每会话连续序号和投影，事务提交后对外可见；HTTPS 增量/历史分页，前台长轮询。暂不引入消息代理、独立事件平台、WebSocket/SSE 双通道。

原因：在单 Host/单客户端规模下，先使重放、分页和状态一致性可验证。后果：端到端延迟与数据库吞吐必须实测；改变传输方式也不得改变游标和持久边界。

## ADR-004：副作用只派发一次，不确定就核对

状态：本次提出的正确性约束，M1 验证。

选择：操作先落库，原子接受决定，一次派发，丢失确认时保留 unknown；不把 SQLite 与 stdio 描述为共同事务。

原因：上游不能假定具备 Runvori 请求标识级的幂等执行能力。后果：崩溃窗口可能需要人工处理或停止旧执行；不能承诺跨进程恰好一次，也不能为了“自动恢复”重放批准。

## ADR-005：三种恢复分开

DEV-002 实现补充：Windows 通过不可继承的 kill-on-close Job 持有进程树；内部 Python 启动器先等单字节放行，Host 成功分配 Job 后才放行目标命令，避免先启动再分配的竞争。Job 分配失败直接关闭，且直接子进程先退出也必须关闭 Job。四项本机合成进程树验收见 [DEV-002](evidence/DEV-002.md)，真实 Codex 执行树与权限策略仍待验证。

状态：沿用定位，补充进程隔离要求。

选择：手机重连恢复视图；历史 thread 加载后开始新回合；执行进程中断则旧回合明确终止。Host 重启先确认遗留进程停止/隔离，再开放新回合。

原因：恢复持久状态不等于恢复执行进程。后果：无法确认进程安全时 Host 保持只读，不自动重跑；具体 OS 行为是 M0 阻断项。

## ADR-006：TLS 身份绑定与本地配对管理

状态：本次补充的首版方案，M0/M1 验证。

选择：HTTPS、系统信任链或配对指纹校验、短时一次性秘密、单设备令牌，本地撤销；模型凭据由电脑 Codex 管理。

原因：可信网络仍需服务身份与操作身份校验，避免把配对做成普通永久密码。后果：首次配置与证书轮换需要电脑参与；不支持多设备迁移和公网认证平台。

## ADR-007：回合、验证与差异来源独立呈现

状态：沿用定位的真实结果要求，补充汇总契约。

选择：上游回合 completed 只表示回合结束；验证依据登记必需项的真实结构化命令记录与退出码。差异必须注明上游回合记录或 Git 工作区对比。

原因：模型摘要、部分命令结果和脏工作区差异都不足以证明任务验证成功。后果：证据不完整时结果可为 unknown/not_run；不会为了界面整齐补造成功。

## ADR-008：真实上游启动前锁定，首次回合不依赖 rollout

状态：DEV-005 真实 Codex 实测补充。

选择：Host 只接受精确版本 `codex-cli 0.164.0-alpha.11.1`；在取得单实例锁、打开数据库、创建配对库和推进 epoch 之前，核验实际将启动的 `upstream_command[0]`，不提供“核验 A、启动 B”的旁路。当前 app-server 连接刚刚创建的 thread 在首次回合前跳过 `thread/resume`；该标记只在内存中，Host 重启后旧 thread 仍必须成功 resume。审批决定只能经命令邮箱进入既有持久化和单次派发，`accepted` 不等于上游确认。

原因：真实 Codex 新建 thread 在首个 turn 产生 rollout 之前无法被 resume，无条件 resume 会让“新建会话后立即开始第一回合”失败；版本不符或不可执行必须在产生任何持久状态之前拒绝。后果：真实测试中需要注入时序的 relay 不能启用 Host pin，必须单独验证真实二进制并在证据中披露；重启后不复用首次回合的跳过逻辑，保持失败关闭。验证见 [真实 Codex 五切点](evidence/DEV-005-host-runtime-restart-real-codex.md) 与 [ERR-028](ERRORS.md)。

## ADR-009：最小 HTTPS 服务用标准库，协议与邮箱边界不变

状态：DEV-006 实现并验证（合成闭环 + 真实 Codex 一次）。

选择：HTTPS 入口用 `http.server.ThreadingHTTPServer` + `ssl`，单进程、16 并发上限、30 秒连接超时、64 KiB 请求体与 256 KiB 响应页上限；TLS 必需且最低 1.2，SPKI 指纹由实际证书计算。所有业务读写仍经命令邮箱，HTTP 线程不打开 SQLite。

原因：仓库 `dependencies = []`，离线 wheel 安装与 smoke 检查是既有交付条件；PRD 中的 FastAPI 可后续替换，但不得改变协议、认证和邮箱边界。后果：标准库服务器不是经审计的生产实现，日志必须不记正文与凭据；未来替换框架时，`api/handlers.py` 的映射与 `DeviceReadGateway` 的鉴权复核应保留。

## ADR-010：审批详情不足一律禁止允许（事件库 v14）

状态：DEV-006 实现并验证。

选择：审批请求落库时附带规范化详情：命令、cwd（必须证明在登记工作区内）、reason、item 标识与时间；文件审批的路径与差异来自 `item/started|item/completed`，两种到达顺序都支持。详情缺失、被截断、类型不是普通命令或路径不安全时 `details_status=insufficient`，`allowed_decisions` 只含 `deny`，允许请求返回 `409 approval_details_insufficient`。v13 及更早的审批迁移为 `insufficient`，不人工回填。

原因：没有可核实内容的审批只能盲批，违反 SECURITY.md 的审批范围要求。后果：旧数据中尚在等待的审批只能拒绝；文件审批在 item 事件始终缺失时不可允许，需要中断回合并在电脑处理。真实 Codex 在 Windows 上可能以 shell 包装串发起命令，详情展示保留原始串但不宣称与探针字面量一致。

## ADR-011：本地管理入口只用 Windows 命名管道

状态：DEV-006 实现并验证。

选择：运行中的 Host 通过 `\\.\pipe\runvori-host-<digest>`（`multiprocessing.connection` + `admin.key` authkey）接受 `status / issue-pairing / revoke-device / register-workspace / create-session / start-thread`。命令全部经邮箱在 SQLite 所有者线程执行；CLI 是唯一客户端。

原因：SECURITY.md 要求生成配对信息、登记工作区与撤销只能经本地入口，且撤销需取消在途长轮询；HTTP 管理接口会扩大攻击面。后果：管理通道依赖同一 Windows 用户会话，不支持 Linux/macOS；Host 未运行时 `revoke-device` 回落离线撤销，该模式不能取消在途请求，CLI 必须标注 `mode: offline`。

## ADR-012：时间线只推进 seq，文本预算耗尽要留可见缺口（事件库 v15）

状态：DEV-007 核心真实链路通过，剩余专项待验收（合成上游 + 真实设备，合成验收三轮全绿；另有 1 次真实 Codex 核心闭环设备验收，见 [真实设备验收](evidence/DEV-007-device-acceptance-real.md)）。

选择：上游 item 生命周期投影成时间线事件（`message.*` / `command.*`），**只推进 `seq`，不改变 `session_version` 与快照**。单回合文本预算 2 MiB，由 `run_timeline_budget` 逐回合记账；耗尽后仍写事件元数据但去掉正文，标 `text_omitted` / `dropped_bytes` 并追加一次 `timeline.truncated`。每条写入都以持久化的 `(thread_id, turn_id)` 绑定定位活动回合，解析不到就返回 `upstream_turn_not_found` 而不写。

原因：如果时间线也递增 `session_version`，一段持续输出的回答会让屏幕上每个待审批的 `expected_version` 在提交前就过期，用户永远批不掉。文本必须有上限，否则一条超长回答能把数据库撑爆；但上限不能表现为“看起来是空的输出”，那会把截断伪装成模型没说话。不信任调用方标识是因为时间线写入来自上游通知，任何一条被错误归属的输出都会让用户在错误的回合里读别人的内容。

后果：客户端必须把 `text_omitted` 渲染成明确缺口，不能当成空串；`timeline.truncated` 每回合只写一次，所以缺口位置由第一条被截断的事件给出，不是逐条通告。2 MiB 是首版选择，未经真实长回答验证，可能需要按实际观测调整。

## ADR-013：设备联调走模拟器别名 + 回环监听，不引入任何转发装置

状态：DEV-007 实现并验证。

选择：Host 在设备验收中只监听 `127.0.0.1`，模拟器通过 `10.0.2.2` 直连；验收证书额外带 `IP:10.0.2.2` 的 SAN。删除 `DeviceRelay`、`device_bridge.py`、`adb forward` 与相关诊断脚本。物理手机需要自己的可达路径，`host_alias()` 对非模拟器直接报 `emulator_required_for_alias`。

原因：上一轮把联调失败归因为 `adb reverse` 故障加 Windows 防火墙拦截，并请用户手动放行。实验推翻了该判断——从模拟器内 `toybox nc` 直连 `10.0.2.2:<port>`，连接在宿主机侧以 `('127.0.0.1', …)` 到达，说明别名由模拟器自身 SLIRP NAT 转成回环连接，**从不经过防火墙**。既然转发装置解决的是不存在的问题，它就只是多一层可能出错的组件。

后果：验收路径就是产品路径，没有任何测试专用的中转；客户端“证书必须命名我所连的地址”这条校验照常生效，SAN 是把真实地址写进证书，不是放宽检查。代价是该编排器只适用于模拟器；真机验收需要另建可达性方案，且必须同样保持指纹与主机名校验。

## ADR-014：验证要求按回合快照，结果从持久命令证据计算（事件库 v16）

状态：DEV-008 第一切片实现并验证（合成设备验收与离线状态矩阵通过，真实 Codex 验证匹配未跑）。

选择：工作区通过受信本地 `register-workspace` 声明 `verification_commands`，每个 run 创建时把命令与 cwd 快照到 `run_verifications`；命令 item 在同一事务投影到 `run_commands`。`/v1/runs/{run_id}/results` 读取时把持久证据重放给现有 `codex/verification.py`，不新增 `verification.updated` 事件，也不在内存里保存验证器。`configured=false` 明确表示未配置，空集不报通过；退出码 null 保持 null；命令文本截断不能匹配要求。

原因：如果结果依赖当前工作区配置，用户修改验证命令会追溯改写旧回合的成败，历史证据失去意义；如果只保存在内存，Host 重启后结果就不可查询。验证器本身已有保守的“同一 item 不能充当两项、完成状态与退出码必须自洽、时间顺序必须成立”规则，产品侧复用它可以避免出现第二套更宽松的判定。

后果：修改工作区只影响之后创建的回合。产品匹配是精确命令文本加解析后的 cwd；Windows 命令包装目前没有规范化，真实 Codex 可能落到 `unknown`/`not_run`，必须保留这个保守结果而不是猜测通过。结果页和命令列表是读侧资源，产物/差异仍需后续切片。见 [DEV-008 证据](evidence/DEV-008-results.md)。

## 待决策清单

DEV-001 更新：实验环境已确认 Windows、Python 3.12.10、Codex CLI 0.164.0-alpha.11.1 和 SQLite 3.49.1；0.163 历史证据保留原版本标注。初始化与 schema 导出已验证。上述仅为探针基线，Q-01～Q-08 仍开放，尤其未证明首发 OS、真实审批详情与 Android 组合。证据见 [DEV-001](evidence/DEV-001.md)。

以下没有默认的“已支持”答案。每项记录验证人、环境、实际证据和结论后才能关闭；当前均开放。

- **Q-01，M0 阻断**：首发 Host OS/架构。用启动、stdio、进程树停止、路径/junction 和重启探针决定；首版只承诺经过实测的组合。
- **Q-02，M0 阻断**：Codex CLI 精确版本/安装来源/schema 哈希，以及命令/文件审批、请求清理与终态信号。不能只填写版本号，必须有真实夹具和确认语义。DEV-005 已记录 `0.164.0-alpha.11.1`、桌面应用随附来源、二进制 SHA-256、真实命令审批和五个崩溃切点；生产分发校验、审批详情和正常退出 EOF 仍开放。
- **Q-03，M0 阻断**：Android 最低版本、SDK/JDK/Kotlin/Compose/Gradle/AGP 组合；需能在干净环境构建并安装基准机。DEV-007 已在 API 37 模拟器（x86_64）上跑通九个设备验收场景，`minSdk 26` 仍未在 API 26 设备或实机验证。
- **Q-04，M0 阻断**：Python/依赖/SQLite 版本与打包方式；需验证单实例锁、持久化和包安装。
- **Q-05，M0 阻断**：TLS 证书配置、指纹分发、手动配对和过期/轮换路径；不可选择关闭证书校验来绕过。DEV-007 已验证 Android 侧的真实信任实现：自实现 `X509ExtendedTrustManager` 保留链与主机名校验后核对 SPKI，错误指纹在写出任何请求字节前拒绝，令牌由 Keystore 加密并排除备份。证书轮换与过期路径仍未实现。
- **Q-06，M0 完成基线、M5 验收**：性能基准机、固定事件数据和跟踪工具。PRD 数字是初始目标，修订必须保留原目标、实测和理由。
- **Q-07，M0/M2**：差异采集、未跟踪/脏工作区/二进制支持程度与验证命令匹配规则；无法可靠关联的内容明确降级。

2026-10-24 首次补充：Q-02 的探针版本升级为 `0.164.0-alpha.11.1`，当时 14 份契约重新导出并校验；仅两份响应/通知 schema 的图像生成条目有变化，首次真实回归覆盖脏工作区固定文件审批，其他旧版行为仍待新版本复核，见 [版本审查](evidence/DEV-003-schema-review.md)。Q-07 的固定文本案例表明：Git diff 不包含未跟踪文件，且预先修改的跟踪文件不能归于本回合；差异必须保留来源、前态和未知范围。大差异、二进制及多命令验证仍开放，见 [观察](evidence/DEV-003-dirty-workspace.json)。

2026-10-24 单命令阶段补充：Q-02 新锁定 `v2/ItemStartedNotification` 与 `v2/ItemCompletedNotification`，当前共 16 份契约。Q-07 先验证单条固定命令的结构化关联与退出码；该阶段仅这一种真实命令获 `passed`，多项登记/汇总尚未实现，Host 集成仍开放，见 [观察](evidence/DEV-003-required-verification.json)。

2026-10-24 双命令补充：Q-07 两条固定命令的真实逐项与汇总结果为 `passed`，汇总器按协议优先级处理缺失、失败和执行中状态，并防止同一上游 item 重复计入。此结论不关闭任意命令登记、Host 持久化或产品验收，见 [观察](evidence/DEV-003-two-required-verifications.json)。

2026-10-27 DEV-008 补充：Q-07 的必需命令已进入产品 Host：工作区本地声明、回合级快照、命令证据持久化与 `/results` 汇总已接通。匹配规则冻结为精确命令文本 + 解析后的 cwd；Windows Codex 的 PowerShell 包装尚未规范化，真实运行若出现包装只会得到 `unknown`/`not_run`，不会被误判通过。见 [DEV-008 证据](evidence/DEV-008-results.md)。Q-07 仍开放。

2026-10-24 失败路径补充：Q-07 的固定 `exit 7` 真实退出码为 7，回合 `completed` 与验证 `failed` 同时成立；该单一固定命令只证明非零路径，见 [观察](evidence/DEV-003-nonzero-verification.json)。

2026-10-24 Git 产物补充：Q-07 的本地夹具表明，128 KiB UTF-8 改动可报告大小/哈希而不暴露内容，二进制改动有 Git 标记并只保留元信息；超过 4 MiB 的文件不计算哈希且显式报告限制。来源只能写 `git_worktree_snapshot`，归因仍为 `unknown`。上游大差异/二进制、重命名及 Host 产物存储仍开放，见 [观察](evidence/DEV-003-artifact-boundaries.json)。

2026-10-24 重命名补充：Q-07 本地 Git 夹具中，已暂存移动有明确 `R100`；未暂存移动只有删除和未跟踪新增，即使内容相同也不能确认重命名。两者均不能归因于 Agent。Codex 上游改动项与 Host 存储仍开放，见 [观察](evidence/DEV-003-rename.json)。

2026-10-24 上游移动拒绝补充：Q-02/Q-07 的真实只读回合出现匹配的 `fileChange` `update`/`move_path` 和一次文件审批；拒绝后原文件未变、目标不存在。仅确认当前 CLI 的提案与拒绝路径，移动允许、实际内容、通用路径映射及 Host 归因仍开放，见 [观察](evidence/DEV-003-file-move-declined.json)。

2026-10-24 固定移动允许补充：Q-02/Q-07 的当前 CLI 在精确补丁和路径白名单后接受一次文件审批，文件改动 item 完成，源文件缺失、目标字节和 Git 状态均符合预期。允许范围限于该固定案例；通用移动策略、进程重启竞争和 Host 产物归因仍开放，见 [观察](evidence/DEV-003-file-move-accepted.json)。

2026-10-24 二进制命令补充：Q-07 的当前 CLI 通过一次固定命令审批写入 2 KiB 二进制文件，退出码、目标字节与 Git 二进制标记相符；来源标为 Git 工作区快照，归因仍须区分。上游二进制 `fileChange` 与 Host 存储仍开放，见 [观察](evidence/DEV-003-binary-command.json)。

2026-10-24 Windows junction 补充：Q-01/Q-07 的本地文件读取边界已在实际 junction 父目录上复验拒绝，未知审批核对仅记录 `unsafe_path`，见 [证据](evidence/DEV-003-windows-junction.md)。符号链接创建与检查/读取竞态未验证，Q-01 不因此关闭。

2026-10-24 包复验补充：Q-04 的当前探针 wheel 离线构建、正常虚拟环境安装及仓库外已安装 CLI 入口校验通过；旧 `pip --target` 不能验证入口，见 [证据](evidence/DEV-001-package-recheck.md)。业务 Host、单实例锁集成和可部署包仍未决。

2026-10-24 单实例锁补充：Q-04 的 Windows 文件句柄锁已在真实跨进程竞争和持锁进程终止后复验；锁文件是否存在不作为当前所有权证据。锁尚未连接 Host 启动、SQLite 活动槽和受管进程恢复，见 [证据](evidence/M0-Q04-instance-lock.md)。

2026-10-24 启动顺序补充：Q-04 最小切片在持锁后打开审批 SQLite，争锁失败不会建库；崩溃后重开保持既有 epoch，不把 Host 锁当成旧 app-server 已隔离的证据。产品启动和恢复仍待实现，见 [证据](evidence/M0-Q04-locked-journal.md)。

2026-10-24 TLS 身份补充：Q-05 的回环测试证书在保留信任链与主机名校验时完成本地 SPKI 指纹核对；不同公钥即使在测试信任集合中也不能沿用旧指纹发送应用数据，见 [证据](evidence/M0-Q05-tls-identity.md)。Android 自签名信任、配对载荷及轮换流程仍未决。

2026-10-24 配对记录补充：Q-05 的独立 SQLite 切片已验证 32 字节秘密、5 分钟到期、摘要保存、单次消费及唯一有效设备；失去兑换响应后同一秘密不可再发令牌，见 [证据](evidence/M0-Q05-pairing-journal.md)。尚未确定 HTTP 限流、Android 凭据保存和产品证书绑定。

2026-10-24 HTTPS 联动补充：Q-05 的一次性回环夹具先完成 TLS 链/主机名与本地 SPKI 核对，才发送携带秘密的 POST body；已提交但响应丢失后重放被拒，见 [证据](evidence/M0-Q05-https-pairing.md)。仅为测试路径，不决定生产 HTTP 框架或配对限流实现。

2026-10-26 工具链基线：Q-03 本机有 JDK/Javac 21.0.12、Android Studio、SDK 和 adb；已加入 Gradle 8.13 wrapper 与 Android 占位工程，完成本机 Debug APK 构建及 API 37 模拟器安装启动。实机、干净环境复现和产品最低版本决策待验证，见 [记录](evidence/DEV-004-toolchain.md)。

2026-10-26 进程隔离补充：Q-01 的 Windows 进程身份改由 PID＋创建时间共同核对，事件库 v12 在目标进程放行前持久记录启动器身份，恢复入口不再接受调用方声明。跨进程崩溃用例验证了 Job 内启动器被杀后放行、Job 外遗留进程拒绝。该记录不证明后代进程树已停止，Q-01 仍开放，见 [证据](evidence/DEV-005-upstream-process-identity.md)。

2026-10-26 旧库无身份记录策略：事件库 v13 为每个已完成 epoch 增加 `unlaunched`／`recorded`／`legacy_unknown` 状态。v12 之前已推进 epoch 且没有身份记录的库标为 `legacy_unknown`，Host 失败关闭、保持只读、不推进 epoch。选择不提供人工“旧进程已停止”声明，也不做无记录回填，因为无法证明遗留进程已隔离；迁移路径是归档旧数据目录、新建 Host 并重新配对。代价是旧数据不可自动继续，且会更换 `host_id`。触发问题：v12 恢复门禁把缺少记录当作已隔离；证据见 [记录](evidence/DEV-005-host-runtime-restart.md)。

2026-10-26 产品 Host 生命周期：新增 `HostRuntime` 与 `python -m runvori.host`，固定持锁 → 对账 → 统一恢复 → 写门禁 → 设备访问组合 → 上游启动的顺序；正常停止用 `host_shutdown` 恢复并推进 epoch。上游连接故障完成隔离恢复后 Host 进程退出（退出码 3），由外部监督决定何时重启；本轮不做进程内重拉上游或自动重跑旧回合。触发问题：各启动组件只有独立探针，崩溃重启没有真实 Host 证据；证据见 [记录](evidence/DEV-005-host-runtime-restart.md)。真实 Codex 执行树与审批决定派发切点仍开放。
- **Q-08，公开发布前**：许可证、第三方依赖/素材声明、发布包分发与私密问题报告入口。由维护者选择，不擅自补默认许可证。

## 更新方式

实现若改变上述方案，新增或更新 ADR，写明触发问题、证据、选择、替代方案及兼容/验收影响。保留被替代结论的状态和原因，不能把未经运行的设计改写成既成事实。
