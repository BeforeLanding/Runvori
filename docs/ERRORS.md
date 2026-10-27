# 开发错误与修复记录

此处保存已发生的错误。规则唯一来源为 [AGENTS.md](../AGENTS.md)，进度见 [progress.md](../progress.md)。禁止粘贴未脱敏栈、个人目录、令牌或真实会话。

## ERR-032：合成验收把成功的验证配置步骤误标为失败

- 日期/阶段：2026-10-27，DEV-008 结果页合成验收。
- 触发：`scripts/accept_device.py` 在运行 `i` 场景前更新工作区验证配置，管理入口实际返回 `verification_count=1`，但报告步骤写 `status: failed`，因为代码读取了响应中不存在的 `status` 字段。
- 根因：把“管理命令成功”错误地绑定到响应形状；`register_workspace` 成功时返回的是工作区字段，不是统一的 `status: ok`。
- 修复与回归：步骤状态改为按 `verification_count` 是否为正判断；重新运行合成十场景，`verification_config` 显示 `ok`、`i` 场景通过，整体 `status: passed`。见 [DEV-008 证据](evidence/DEV-008-results.md)。

## ERR-DEV-006：审批详情闸门导致既有测试夹具被拒

- 日期/阶段：2026-10-27，DEV-006 审批详情与禁止盲批。
- 触发：加入 `allow_once` 闸门后，既有 13 处 `record_approval_request` 调用不提供详情，`record_approval_decision` 返回 `approval_details_insufficient`；合成 app-server 的审批载荷只有 `threadId/turnId`，也不满足 detail 完整性。
- 根因：闸门按设计拒绝缺少 command/item/cwd 的请求，而测试辅助函数仍按旧形态构造审批；另有一处测试夹具在临时目录内注册工作区但会话未绑定工作区，导致 `cwd` 无法核实。
- 修复与回归：新增 `host/tests/approval_fixtures.py` 统一提供可审阅的命令详情与工作区注册，13 处调用改为显式详情或断言 409；合成 app-server 按真实载荷补齐 `command/cwd/itemId/startedAtMs` 并支持 `RUNVORI_FAKE_WORKSPACE`。全量 362 项通过。

## ERR-DEV-006：邮箱内嵌套命令自锁

- 日期/阶段：2026-10-27，DEV-006 本地管理入口。
- 触发：`create-session --create-thread` 经管道进入邮箱后，所有者线程在 `create_session` 内再次调用 `start_thread` 邮箱命令，等待自己完成，10 秒后返回 `command_result_unknown`。
- 根因：管理命令在所有者线程上同步发起第二个邮箱命令，违反“邮箱只由外部线程提交”的假设。
- 修复与回归：`create_session` 命令自身在已持久创建会话后直接向 `OwnedThreadStartQueue` 入队（同一命令内完成），不再嵌套提交；新增的合成闭环与 API 测试覆盖该路径。

## ERR-DEV-006：命名管道连接不支持 settimeout

- 日期/阶段：2026-10-27，DEV-006 本地管理入口。
- 触发：`LocalAdminClient.call` 抛 `AttributeError: 'PipeConnection' object has no attribute 'settimeout'`。
- 根因：`multiprocessing.connection.PipeConnection` 与套接字接口不同，无超时 API。
- 修复与回归：移除超时设置；对端关闭以 `EOFError/OSError` 呈现并按“结果未知/未运行”分类，客户端明确不自动重试可能已执行的命令。

## ERR-DEV-006：崩溃重启测试在标记改名后偶发 PermissionError

- 日期/阶段：2026-10-27，DEV-006 全量检查。
- 触发：`test_host_restart` 偶发在读取 `markers/started` 时抛 `PermissionError`，单独重跑总是通过。
- 根因：测试辅助函数在原子改名后立即打开文件，Windows 上扫描器/索引进程可能短暂持有句柄；这是测试装置竞态，不是 Host 结果。
- 修复与回归：`_wait_file` 在读取阶段对 `PermissionError` 重试至同一超时，超时才判失败；连续两次定向运行与全量检查通过。

## ERR-DEV-005：重开夹具使用过时代次

- 日期/阶段：2026-10-26，DEV-005 Host 设备访问组合。
- 触发：新增关闭后重开同一对象的测试，第二次统一恢复返回 `stale_epoch`。
- 根因：测试辅助函数把旧进程代次固定为 0，首次恢复已经将其推进。
- 修复与回归：每次从当前审批库读取代次后恢复；4 项定向测试通过，随后完整检查通过。

## ERR-DEV-005：迁移测试未关闭临时 SQLite 连接

- 日期/阶段：2026-10-26，DEV-005 Host 身份持久化。
- 触发：首次定向测试的两项迁移场景在 Windows 清理临时目录时遇到文件占用。
- 根因：测试使用 `with sqlite3.connect(...)`，该上下文只提交/回滚事务，不关闭连接。
- 修复与回归：改用 `contextlib.closing` 并显式提交；48 项事件库定向测试复验通过。

## ERR-DEV-005：定向测试未设置源码导入路径

- 日期/阶段：2026-10-26，DEV-005 邮箱停机边界。
- 触发：直接用 `python -m unittest discover -s host/tests -p test_command_mailbox.py` 启动，未设置 `host/src` 的模块导入路径，测试加载时报 `ModuleNotFoundError`。
- 根因：该仓库的完整检查脚本自行加入 `host/src`，直接调用 unittest 不会继承此设置。
- 修复与回归：在本次 PowerShell 进程内设置 `PYTHONPATH` 后运行同一命令，11 项通过；完整 `python scripts/check.py` 通过。未修改系统或全局环境。

## ERR-DEV-005：未派发取消测试使用错误分页字段

- 日期/阶段：2026-10-26，DEV-005 未派发回合取消。
- 触发：首次完整检查时，新增测试读取分页结果的 `items`，实际接口返回 `events`。
- 根因：测试未按现有分页契约取字段。
- 修复：改为读取 `events`，定向测试已通过；完整结果见 [记录](evidence/DEV-005-unstarted-cancel.md)。

## ERR-DEV-005：schema 升级后迁移测试仍断言旧版本

- 日期/阶段：2026-10-25，DEV-005 中断请求持久边界。
- 触发：首次完整检查中，六项旧库迁移测试仍预期事件库版本 9，实际已升级到 10。
- 根因：新增中断表时未同步迁移测试的目标版本断言。
- 修复：将六项迁移断言更新到版本 10，并补充 v9 到 v10 的数据保留测试。
- 验证：定向与完整检查结果见 [记录](evidence/DEV-005-interrupt-journal.md)。

## ERR-DEV-005：持续循环测试跨线程使用 SQLite

- 日期/阶段：2026-10-25，DEV-005 归属连接持续事件循环。
- 触发：首次完整检查中，测试把循环移到另一线程后读取 Host epoch，SQLite 拒绝跨线程使用创建于主线程的连接。
- 根因：测试将可跨线程的停止信号误当成可跨线程移动的 Host 存储所有权。
- 修复：循环在持有数据库连接的 Host 线程运行，计时线程只设置停止信号。
- 验证：定向测试及完整检查结果见 [记录](evidence/DEV-005-owned-event-loop.md)。

## ERR-DEV-005：事件交接故障注入与错误分类

- 日期/阶段：2026-10-25，DEV-005 单消息持久交接。
- 触发：首次故障注入触发器引用 events 表不存在的 `type` 列，未命中预期的事件写入故障；修正后发现写入失败被包装成“重复审批请求”。
- 根因：测试未按表实际字段判断事件类型；审批入口把所有 SQLite 完整性错误统一转换为唯一键冲突。
- 修复：触发器检查 `event_json`；审批入口仅将 SQLite 唯一键/主键冲突转换为重复请求，其余错误原样传播并关闭上游连接。
- 验证：定向回归及完整检查通过；165 项测试运行，160 通过、5 项环境限制跳过。

## ERR-DEV-005：迁移测试连接未关闭

- 日期/阶段：2026-10-25，DEV-005 上游审批请求身份映射。
- 触发：首次完整检查中，旧表迁移测试在 Windows 清理临时目录时遇到文件占用；实现相关测试均通过。
- 根因：测试夹具将 `sqlite3.Connection` 用作上下文管理器；退出只结束事务，不关闭连接。
- 修复：测试显式提交并在 `finally` 关闭连接。
- 验证：重新执行完整检查，157 项测试运行，152 通过、5 项环境限制跳过。

## ERR-001：Codex 默认临时目录不可写

- 日期/阶段：2026-10-23，DEV-001 环境检查。
- 触发：直接运行本机 Codex version/help，stderr 出现 stale arg0 清理与 PATH alias 创建的 Access denied 警告；版本和帮助仍正常返回。
- 根因：工具默认 HOME 内临时目录与本任务写权限不一致。不是 app-server 协议失败。
- 修复：每次探针在仓库 `.dev` 中创建独立临时 CODEX_HOME，不修改用户全局配置，不复制用户认证。
- 验证：schema 导出与真实握手均通过，探针没有记录原始 stderr。
- 规则：隔离实验目录；警告与实际失败分别记录，不能凭 stderr 有内容判断 RPC 失败。

## ERR-002：默认系统 TEMP 使测试及构建失败

- 日期/阶段：2026-10-23，DEV-001 初轮回归与构建。
- 触发：首次 13 项测试中，篡改 manifest 测试创建临时文件时 PermissionError；12 项通过、1 项错误。同样的默认目录导致首次 pip wheel 的 build tracker 无法写入。
- 根因：当前 Windows 沙箱生成的系统临时目录不保证可写；测试与 pip 默认依赖该目录。
- 修复：测试显式使用 `.dev` 下的 TemporaryDirectory；检查脚本先创建该目录。构建进程使用指向 `.dev/tmp` 的 TMPDIR，且关闭版本检查、使用 `--no-index`，不写全局环境设置。
- 验证：修复后原 13 项测试全部通过，离线 wheel 构建成功；后续新增用例结果以 DEV-001 证据为准。
- 规则：沙箱工具的临时目录必须落在已确认可写的任务目录；权限失败先调整局部路径，不默认升级权限或修改全局配置。

## ERR-003：首轮进程清理路径使测试迟缓

- 日期/阶段：2026-10-23，DEV-001 首轮回归。
- 触发：每个合成 peer 都直接走 taskkill，首轮 13 项测试约 77 秒。
- 分析：忽略了正常 peer 可通过 stdin EOF 自行退出；不必要地走外部进程清理回退路径。
- 修复：无阻塞写线程时先关闭 stdin 并短暂等待；无响应再清理进程。写线程阻塞时必须先终止进程，避免关闭持锁缓冲流死锁。
- 验证：修复后同一组 13 项测试约 13 秒通过；补充不读取 stdin 的测试覆盖写超时及清理。
- 规则：读与写都要有超时；清理缓冲流前确认读写线程已解除阻塞。该改进不等于生产进程树监督已完成。

## ERR-004：直接子进程退出使后代清理被跳过

- 日期/阶段：2026-10-23，DEV-002 代码审查发现；此前未覆盖该场景。
- 触发条件：目标创建持有 stdout/stderr 的后代后先退出。旧实现只在 Popen 仍存活时调用 taskkill，无法可靠清理后代，读线程可能仍阻塞。
- 修复：用不可继承的 kill-on-close Job 管理生命周期，关闭不依赖直接子进程存活；加启动门控，避免归入 Job 前目标已创建后代。分配失败时禁止执行。
- 验证：四项新进程树测试通过，包括父进程退出及 Host 强制退出；全量 20 项测试通过。
- 规则：必须先建立生命周期归属再放行执行；直接子进程退出不能代替后代全部退出的证据。

## ERR-005：普通沙箱中的真实回合无法访问上游

- 日期/阶段：2026-10-23，DEV-003 首次真实受限执行。
- 触发：已登录账号与空测试仓库中，普通沙箱运行探针得到失败终态、无审批及无目标文件。
- 证据：同环境 `Test-NetConnection api.openai.com -Port 443` 无法解析域名；允许外网访问的本机执行环境重跑同一场景得到真实命令审批与正常终态。上游原始错误码未保存，网络限制是强相关因素，不能写成已证实的唯一根因。
- 处理：保留失败的脱敏报告，仅对显式授权的真实探针使用可访问上游的执行环境；不修改全局 DNS、代理或用户配置。后续仍须区分网络故障与协议故障。

## ERR-006：允许探针的初始白名单拒绝真实只读命令

- 日期/阶段：2026-10-23，DEV-003 单次允许探针。
- 触发：`--approval accept` 初始运行仍回复 `decline`，无命令退出码；未产生文件改动。
- 证据：真实审批含导出 schema 未声明的 `availableDecisions`，Codex 用随附 PowerShell 包装预定命令，另携带执行环境标识和建议的 execpolicy 修订。初始白名单只接受裸命令且把建议字段视为已扩权。
- 修复：本地审查包装内容后，仅匹配预定只读命令及固定包装的完整字符串；按协议仅发送单次 `accept`，不选择应用策略修订的决定。网络权限和其他命令继续拒绝；诊断报告仅保留字段名、相等标志和实际决定。
- 验证：真实重跑对应命令项退出码 0，27 项测试通过；临时审查文件已删除。此结论只覆盖只读命令，不能外推到文件写入或生产审批状态机。

## ERR-007：写入命令的 PowerShell 包装增加 NoProfile 参数

- 日期/阶段：2026-10-23，DEV-003 受控写入探针。
- 触发：预定写入命令出现在真实审批中，但初始白名单未匹配，探针拒绝、文件未生成。
- 证据：本地临时审查发现，同一个 Codex 随附 PowerShell 可执行文件在写入请求中增加 `-NoProfile`。命令正文仍为预定的单文件写入操作；审查文件随即删除。
- 修复：仅将这个精确包装加入白名单，继续拒绝附加命令、其他可执行文件、网络权限和未知字段。
- 验证：真实重跑发送一次 `accept`，对应命令退出码 0，目标文件 16 字节完全匹配；28 项测试通过。此结果不构成独立文件修改审批验证。

## ERR-008：待审批中断探针过早停止接收请求结束通知

- 日期/阶段：2026-10-24，DEV-003 待审批中断探针。
- 触发：首次真实运行已收到 `turn/completed: interrupted`，但报告记为未观察到对应 `serverRequest/resolved`。
- 原因：探针在回合终态后立即结束事件收集；请求结束通知可能晚于终态。
- 修复：在 thread 恢复后增加两秒有界接收窗口，并按请求 ID 匹配通知；窗口内仍无通知时保留“未观察到”，不推断上游永久不发送。非白名单待审批请求也改为失败关闭，避免针对非预定操作开展探针。
- 验证：真实重跑见终态后到达的对应请求结束通知；14 份 schema 校验、34 项测试通过。尚未验证通知丢失或审批决定与中断并发。

## ERR-009：匹配的 RPC 错误使探针无法继续读取终态

- 日期/阶段：2026-10-24，DEV-003 中断时序验证。
- 触发：真实回合已经 `completed` 后发送迟到中断，收到脱敏 RPC 错误；旧 `StdioClient` 把它与传输故障一同标为连接不可用，因此未尝试恢复 thread，也无法用同一连接继续读取后续事件。
- 原因：`RpcError` 继承 `TransportError`，请求循环的统一异常处理误把已匹配的 RPC 错误响应视为传输损坏。
- 修复：仅对匹配的 RPC 错误保留连接，继续禁止自动重试该 RPC；超时、格式异常和写入失败仍使传输不可用。中断探针在该错误后继续观察回合终态。
- 验证：合成 peer 在 RPC 错误后仍发送终态，客户端成功读取并再次请求；真实命令完成后延迟 5 秒的探针收到中断 RPC 错误、随后读到 `completed` 并成功 `thread/resume`。全量 52 项测试与 14 份 schema 校验通过。该结果不证明所有上游 RPC 错误都没有副作用。

## ERR-010：本机 CLI 更新触发版本门控

- 日期/阶段：2026-10-24，DEV-003 脏工作区探针。
- 触发：已准备可丢弃 Git 夹具后，首次真实运行在模型调用前返回 `codex_version_mismatch`；旧锁为 `0.163.0-alpha.11.2`，本机 CLI 为 `0.164.0-alpha.11.1`。
- 原因：本机 CLI 已更新，精确版本锁按设计拒绝未知版本；未发生本轮模型调用或审批。
- 处理：在无登录的隔离目录重新导出并逐份审查 14 份 schema；12 份字节相同，两份仅改变图像生成 thread item。更新版本锁及清单，重跑契约测试、隔离握手和真实脏工作区探针，不绕过校验。
- 验证：14 份 schema 校验、53 项离线测试和隔离握手通过；真实单次文件审批与字节核对通过。旧版的其他行为尚未在新版逐项复验，见 [审查记录](evidence/DEV-003-schema-review.md)。

## ERR-011：已完成命令的非零退出码被回合运行状态遮蔽

- 日期/阶段：2026-10-24，DEV-003 多项验证汇总测试。
- 触发：一项命令已提供完整结构化完成项和非零退出码，另一必需项仍在执行；汇总错误地显示 `running`，未按协议优先显示 `failed`。
- 原因：单项证据判定沿用单命令终态探针约束，在回合尚未结束时把已完成项标为 `unknown`，从而失去已确认的失败结果。
- 修复：命令项的开始与完成时间、身份、命令、cwd 和整数退出码齐全时立即确定该单项结果；回合状态仍独立记录。缺失或冲突的证据继续为 `unknown`。
- 验证：新增“失败项与执行中项混合”行为测试，全量测试及契约校验结果见 [双命令记录](evidence/DEV-003-two-required-verifications.json)。真实双命令探针只覆盖两项均通过，非零混合为本地测试证据。

## ERR-012：本机沙箱无法创建符号链接测试夹具

- 日期/阶段：2026-10-24，DEV-003 Git 产物路径边界测试。
- 触发：测试尝试在 `.dev` 可丢弃目录创建符号链接，Windows 返回 `OSError`，该子场景跳过；73 项测试运行中 72 项通过、1 项跳过。
- 原因：本机测试环境未提供创建符号链接所需能力；未据此推断所有 Windows 环境都不可用。
- 处理：保留捕获器对路径中每一段的符号链接/junction 拒绝逻辑和该实际环境测试；路径穿越、绝对路径、容量边界及夹具篡改测试继续执行。未通过放宽路径限制规避环境问题。
- 后续验证：在可创建符号链接与 junction 的首发 Host 环境重跑真实路径边界用例。当前不能声称真实链接拒绝已在本机验证。

## ERR-013：未知审批文件核对可越过工作区

- 日期/阶段：2026-10-24，DEV-003 SQLite 切片复查。
- 触发：`inspect_unknown_file` 在检查审批状态前读取调用方给出的任意路径，仅检查目标自身是否为符号链接；父目录链接可指向工作区外。
- 根因：核对接口没有工作区根与相对路径约束，状态检查晚于文件读取。
- 修复：先拒绝非 `unknown` 状态；要求工作区和相对路径，拒绝绝对/上级路径及逐段链接，读取最多预期长度加一。
- 验证：路径越界与状态检查回归通过；本机无法创建符号链接，链接父目录回归跳过，待具备链接能力的 Host 环境复验。文件系统并发替换风险仍未解决。
- 规则：只读核对也必须先验证授权状态与工作区边界；不把本地快照当成上游副作用归因。

## ERR-014：本机无法创建 junction 测试夹具

- 日期/阶段：2026-10-24，DEV-003 路径边界复验。
- 触发：在仓库 `.dev` 下用 PowerShell `New-Item -ItemType Junction` 创建目录 junction，系统返回拒绝访问。
- 结论：本机既不能创建符号链接，也不能创建 junction，无法借另一种目录链接补齐实际路径拒绝测试；不能据此断言产品检查已在真实链接上通过。
- 处理：保留链接路径检查和跳过标记；在具备创建权限的首发 Host 环境复验。未更改全局权限或关闭检查。
- 后续：2026-10-24 在获准的本地执行环境可创建 junction；新增真实回归确认两个读取入口均拒绝链接父目录，见 [复验](evidence/DEV-003-windows-junction.md)。符号链接创建测试仍跳过，此记录保留原始失败经过。

## ERR-015：干净 Git 工作区的空状态被判为查询失败

- 日期/阶段：2026-10-24，DEV-003 文件移动夹具准备。
- 触发：提交完固定基线后，`git status --porcelain=v1 -z` 正常返回空字节；既有 `_git_status_entries` 要求输出以 NUL 结尾，误报 `fixture_git_status_failed`。
- 根因：解析器只考虑有状态条目的输出，遗漏干净工作区的合法空结果。
- 修复：返回码为零且输出为空时返回空集合；非空输出仍要求 NUL 结尾。
- 验证：新增干净基线夹具测试，`python scripts/check.py` 77 项运行（75 通过、2 项本机链接权限限制跳过）；真实只读移动拒绝探针通过。
- 规则：Git `-z` 输出为空时应按命令退出码区分干净状态与查询失败。

## ERR-016：二进制命令的双引号 PowerShell 包装不在白名单

- 日期/阶段：2026-10-24，DEV-003 固定二进制写入探针。
- 触发：真实命令审批含预定 Python 写入正文，但随附 PowerShell 使用 `-Command "..."` 并转义正文内部双引号；初始精确白名单拒绝，文件未改。
- 根因：此前只观察到单引号包装或 `-NoProfile` 形式，未纳入这种完整双引号包装。
- 修复：在临时只拒绝诊断中核对完整命令后，仅增加这个固定包装的精确匹配；附加命令、不同可执行文件或目标路径继续拒绝。
- 验证：包装正反例行为测试通过；新的真实可丢弃仓库获得一次允许、结构化退出码 0、2 KiB 目标字节和 Git 二进制标记。全量 79 项测试运行（77 通过、2 项链接权限限制跳过）。
- 规则：遇到上游包装偏差先拒绝并在可丢弃仓库核对原文；不得改为包含匹配或泛化 shell 解析。

## ERR-017：PATH 缺失被误判为 Android SDK 未安装

- 日期/阶段：2026-10-24，DEV-004 工具链盘点。
- 触发：首次命令输出显示 `adb`、`sdkmanager`、`gradle` 不在 PATH，格式化的路径检查输出未正确读取，误写成本机未安装 Android SDK/Studio。
- 根因：把命令不可直接调用与文件不存在混为一谈。
- 修复：逐个绝对路径复核 Android SDK/Studio，直接运行 SDK 内 adb，修正工具链记录和进度；SDK 与 Studio 均存在，但没有工程、wrapper 或设备。
- 验证：读到 Studio `product-info.json`、SDK platforms/build-tools 列表和 adb 版本；允许本地 daemon 后 `adb devices -l` 返回空设备列表。
- 规则：工具不在 PATH 时必须复核常见安装目录，再下结论；环境可用性和项目构建成功分开记录。

## ERR-018：沙箱内 Gradle daemon 无法连接本地端口

- 日期/阶段：2026-10-24，DEV-004 wrapper 生成。
- 触发：使用本机缓存 Gradle 8.13 生成 wrapper 时，单次构建 daemon 在沙箱内无法连接其本地监听端口。
- 处理：将 `GRADLE_USER_HOME` 设在仓库忽略的 `.dev` 目录，按权限流程重试本地构建；wrapper 生成成功，JAR 哈希与官方校验值一致。未修改全局 Gradle 缓存。
- 验证：wrapper 的 `--version` 返回 Gradle 8.13，随后 `:app:assembleDebug` 成功。

## ERR-019：只读依赖缓存目录层级与离线依赖不全

- 日期/阶段：2026-10-24，DEV-004 首次构建。
- 触发：首次将 `GRADLE_RO_DEP_CACHE` 指向缓存中的 `modules-2` 子目录，Gradle 拒绝该层级；改为父 `caches` 后，离线构建又因部分 Android/Compose 传递依赖尚未缓存而失败。
- 修复：将变量指向缓存根目录，按固定版本下载缺失依赖至仓库忽略的本地 Gradle home；随后离线重建成功。
- 验证：标准 wrapper 命令 `:app:assembleDebug` 成功；APK 经 `aapt` 和 SHA-256 核对。网络依赖首次获取仍需干净环境复验。

## ERR-020：沙箱内 adb daemon 无法启动

- 日期/阶段：2026-10-24，DEV-004 模拟器安装验证。
- 触发：普通沙箱中的 `adb devices -l` 尝试启动本地 5037 端口 daemon，返回 `could not read ok from ADB Server` 与 `cannot connect to daemon`。
- 处理：按权限流程启动只读 AVD 实例并在获准的本地执行环境运行 adb；没有修改全局 SDK、AVD 配置或关闭验证。
- 验证：模拟器 `sys.boot_completed=1`，APK 安装、启动及冒烟脚本均通过。此为执行环境端口限制，不能据此判断 Android 工程故障。

## ERR-021：`pip --target` 安装掩盖命令入口缺失包路径

- 日期/阶段：2026-10-24，Q-04 当前 Host 探针包复验。
- 触发：当前 wheel 用 `pip install --target` 安装成功、模块可经手工插入 `sys.path` 导入，但从仓库外直接运行目标目录的 `bin/runvori.exe` 报 `ModuleNotFoundError: No module named 'runvori'`。
- 根因：`--target` 不会把目标目录自动加入该命令入口的模块搜索路径；旧验证只检查了手工导入，没有检查已安装入口。
- 修复：使用一次性虚拟环境正常安装 wheel，从仓库外执行 `runvori.exe verify-contracts --workspace`；新增 `scripts/smoke_package.ps1` 固定此流程。
- 验证：已安装 CLI 返回 16 份契约校验通过，当前新增模块均能从虚拟环境导入。产品 Host 部署仍未验证。

## ERR-022：隔离虚拟环境首次创建受默认临时目录限制

- 日期/阶段：2026-10-24，Q-04 包复验。
- 触发：首次 `python -m venv` 的 `ensurepip` 返回非零；该次虚拟环境内没有 pip。
- 处理：把进程局部 `TEMP/TMP` 与 pip 缓存放在仓库忽略的 `.dev`，重新创建新的虚拟环境；未更改全局临时目录或 Python 安装。
- 验证：新虚拟环境创建、离线安装、仓库外入口均成功；自动冒烟脚本完整通过并清理夹具。默认目录失败的底层权限点未单独确认。

## ERR-023：普通沙箱阻止 TLS 回环连接

- 日期/阶段：2026-10-24，M0/Q-05 TLS 身份探针。
- 触发：测试服务器已在 `127.0.0.1` 监听，但普通沙箱中的客户端 `socket.create_connection` 返回 Windows `PermissionError`（10013）。
- 处理：只将这一明确的回环权限错误标为 `loopback_unavailable`，离线检查在该环境跳过真实握手；按权限流程在获准本地环境复验，不降低证书校验。
- 验证：获准环境中真实握手与指纹拒绝路径通过；普通沙箱中用例明确跳过，其余测试继续运行。

## ERR-024：两张测试信任锚主题相同导致首个握手失败

- 日期/阶段：2026-10-24，M0/Q-05 测试证书准备。
- 触发：两张不同公钥的自签名证书均用 `CN=localhost` 加入同一测试信任集合时，首个本应受信任的握手返回 `self-signed certificate` 验证错误。
- 分析：相同主题名使信任路径选择存在歧义；本轮没有单独证明 OpenSSL 内部选择细节。
- 修复：测试证书使用不同主题名，仍都用 `localhost` SAN 做同一端点主机名核对；保持链和主机名校验开启。
- 验证：匹配指纹通过，轮换公钥、错误主机名和未受信任证书的拒绝路径均通过。

## ERR-025：进程退出后 PID 条目仍短暂存在

- 日期/阶段：2026-10-26，DEV-005 上游进程身份切片。
- 触发：测试先 `kill()` 并 `wait()` 一个子进程，然后期待 `OpenProcess` 失败；Windows 在仍有句柄或内核对象引用时保留 PID 条目，`OpenProcess` 仍成功，探测返回 `exited` 而不是 `absent`。
- 分析：进程「已退出」与「PID 已回收」不是同一事实；`GetExitCodeProcess` 返回真实退出码，`STILL_ACTIVE`（259）才是存活判据。不能以 `OpenProcess` 失败作为唯一判据。
- 修复：探测按创建时间区分 PID 复用，用退出码区分已退出与存活；门禁接受 `absent/exited/replaced` 三种结果。测试改为等待「不再是 running」，而不是等待 PID 消失。
- 验证：跨崩溃回归验证了 Job 内启动器被杀后放行、Job 外遗留进程被杀前拒绝；见 [证据](evidence/DEV-005-upstream-process-identity.md)。
- 规则：已提炼到 AGENTS.md「进程身份判定不以 OpenProcess 失败为唯一依据」。

## ERR-026：TLS 回环探针发送后立即关闭导致丢包（已修复）

- 日期/阶段：2026-10-26，运行完整离线检查时观察到；与本轮进程身份改动无关（该脚本不导入 `runvori`）。
- 触发：`python scripts/probe_tls_identity.py` 偶发返回 `matching_identity_not_accepted` 或 `explicit_new_pin_not_accepted`；连续 20 次中失败 5 次。
- 定位：单独复现握手 12 次，其中 1 次客户端 `sendall` 正常返回、服务端 `recv` 返回空字节且两侧都没有异常——客户端在发送后立即关闭 TLS 连接，触发的 RST 早于服务端读取，应用数据被丢弃。不是证书、指纹或主机名判定错误。
- 修复：`connect_pinned` 发送后等待对端关闭（`recv` 到空）再退出，把「握手与指纹通过」与「套接字拆除时序」分开；超时明确报 `probe_ack_timeout`，不重试、不掩盖。
- 验证：修复后探针连续 30 次全部通过；关联的 `scripts/probe_https_pairing.py` 连续 40 次通过（该脚本本就用 `HTTPResponse` 读完响应再关闭，未受影响）；`python scripts/check.py` 连续 4 次 294 项通过。
- 规则：一次性握手探针必须等到对端确认或读到响应再关闭，不能把「发送成功」当作「对端收到」。

## ERR-027：测试后代继承协议管道掩盖上游退出（测试装置已修复）

- 日期/阶段：2026-10-26，DEV-005 Host 关闭与重启闭环。
- 触发：上游故障用例中，fake app-server 在回复 `turn/start` 后退出，但 Host 的事件循环一直读不到 EOF，15 秒后仍保持运行；`run_result` 未写出。
- 定位：fake app-server 为进程树验证派生的 leaf 进程继承了 fake app-server 的 stdout/stdin 管道。fake app-server 退出后，leaf 仍持有写端，Host 的读取线程看不到 EOF；Job 只在 Host 死亡时兜底，不能覆盖“上游父进程正常退出”。
- 修复：测试 leaf 使用 `DEVNULL`，不继承协议管道；崩溃用例的进程树验证不变。
- 验证：上游故障用例通过，Host 在恢复后退出；合成五切点崩溃测试与全量检查通过，后续真实 Codex 五切点也通过。
- 未确认风险：真实 Codex app-server 若派生继承 stdio 的后代，Host 的上游退出检测同样可能被推迟。真实 Codex 崩溃测试中观测到 `codex-code-mode-host.exe` 与 `conhost.exe` 后代均被同一 Job 收敛，但没有测试“上游正常退出且后代仍持有协议管道”的 EOF 检测；该风险继续开放，不能把 Job 的 Host 死亡兜底当成上游退出检测。

## ERR-028：新建 Codex thread 在首回合前无法 resume（已修复）

- 日期/阶段：2026-10-26，DEV-005 真实 Codex Host 崩溃测试首次运行。
- 触发：等待审批切点的真实 Host 在 `run_start_fault` 后恢复并退出；直接调用真实 app-server 复现，`thread/resume` 返回 `no rollout found for thread id ...`。
- 定位：Codex 新建 thread 在首个 `turn` 产生 rollout 之前不能被新连接 resume。Host 的 `start_run_once` 在每次首次派发前都无条件调用 `thread/resume`，因此“新建会话后立即开始第一回合”在真实上游失败；此前合成 peer 与直接播种 thread 掩盖了该路径。
- 修复：`StdioClient` 记录本连接创建的 thread；产品 `thread/start` 成功后标记；`resume_session_thread` 对同一连接新建的 thread 跳过 resume，直接允许首次 `turn/start`。标记只在内存中，Host 重启后消失，旧 thread 仍必须成功 resume，保持失败关闭。
- 验证：真实 Codex 通过产品 `thread/start` 创建 thread 后成功开始首回合；五个真实崩溃切点通过；新增单元测试覆盖 fresh thread 跳过 resume 与 thread/start 标记。证据见 [真实 Codex 五切点](evidence/DEV-005-host-runtime-restart-real-codex.md)。
- 规则：不得对刚刚由当前上游连接创建、尚未产生 rollout 的 thread 无条件 resume；也不能把“跳过首次 resume”扩大到重启后的旧 thread。

## ERR-029：TLS 握手在 worker 线程交错导致客户端看到 EOF

- 日期/阶段：2026-10-27，DEV-007 设备验收联调。
- 触发：模拟器连接 `https://10.0.2.2:<port>/v1` 时，客户端偶发在握手后立刻读到 EOF；Host 侧无 HTTP 记录，客户端表现为握手失败。单客户端串行请求不出现。
- 定位：`ThreadingHTTPServer` 把已 accept 的原始 socket 交给工作线程，`wrap_socket` 在工作线程执行。并发时 accept 循环继续返回新连接，两个 TLS 握手可以在同一对 socket 上交错，一方收到对方的 `ClientHello`/`ServerHello` 后报 `SSLV3_ALERT_CERTIFICATE_UNKNOWN` 并断开。不是证书、指纹或主机名判定错误，也不是防火墙。
- 修复：`_ThreadedHttps.get_request` 在 accept 线程完成 `wrap_socket`，握手失败的连接就地关闭，不进入请求线程。
- 验证：设备验收九个场景连续三轮全绿；`test_api_handlers.py`、`test_https_host_flow.py` 与全量 374 项检查通过。
- 规则：TLS 必须在 accept 线程完成握手，不能把未包装的原始 socket 交给工作线程。

## ERR-030：同步成功抹掉了紧接着的拒绝原因

- 日期/阶段：2026-10-27，DEV-007 设备验收场景 `b`。
- 触发：在已有活动回合的会话里发起新回合时，用户看不到 `active_run_exists` 的提示；`startRun` 明明设置了 `error`。
- 定位：`startRun` 的 `ProtocolError` 分支先写 `error`，紧接着调用 `syncSelectedSession()`；`applySync` 无条件 `error = null`，于是刚写的拒绝信息在渲染前就被清掉。这不是测试时序问题——真实用户同样看不到。
- 修复：`applySync` 只清除网络类瞬时错误（`TRANSIENT_ERRORS` 里的“网络不可达…”），其余错误保留；同时把 `dismissError` 改为同时清 `error` 与 `notice`，修掉“知道了”按钮只清错误而让提示横幅留在屏幕上的问题。
- 验证：重装 APK 后重跑设备验收，九个场景仍全绿（`startRun` 在成功路径上断言 `error == null`，说明收紧后的清除规则没有误留错误）。但**拒绝路径仍无自动化断言**：现有场景不会在已有活动回合时再次发起，`active_run_exists` 的提示实际呈现未被测试覆盖，已记入 [DEV-007 未完成项](evidence/DEV-007-device-acceptance.md)。
- 规则：同步可以推翻“连不上”，不能推翻“Host 拒绝了这次操作”；错误横幅与提示横幅共用一个关闭入口时必须一起清除。

## ERR-031：真实模式最初复用合成场景与断言（准备阶段发现，已修复）

- 日期/阶段：2026-10-27，DEV-007 真实 Codex 设备验收准备。
- 触发：`scripts/accept_device.py --real` 只切换上游进程，默认仍运行九个合成场景；提示词含 `[expire-request]`、`[file-change]`、`[interrupt]`，断言依赖 `python --version` 字面量、输出 `3.12` 和“合成完成”。launcher 未传 `expected_upstream_version`，也未校验 Codex 绝对路径或专用 `CODEX_HOME`。
- 定位：真实模式缺少场景白名单、版本门、真实提示词传递和真实断言分支；`c` 还会额外启动一个 run，使一次“真实验收”变成多次模型调用。直接加 `--real` 会把合成时序误称为真实证据，并可能跳过版本门。
- 修复：真实模式增加 Codex 绝对路径/版本/`CODEX_HOME` 校验与敏感环境清理、一次性 fixture 工作区、线程绑定预检、`s/a/b/c/g/e` 白名单、`ACCEPT_PROMPT` base64 传递、真实断言分支与 240 秒超时、`--dry-run`、`--report` 和 `runs` 计数校验；`d/f/h` 在真实模式被拒绝。
- 验证：`--real --dry-run` 输出 `expected_model_turns=1`；改后合成九场景全绿；随后真实设备验收 1 次通过（`observed_runs=1`），见 [真实设备验收](evidence/DEV-007-device-acceptance-real.md)。
- 规则：真实模式必须有独立场景集、独立提示词与断言，并先过版本门；不能把合成时序、合成文本或“加了 `--real`”本身当作真实证据。

## 记录模板

新记录使用连续编号，包含日期/任务、触发条件、实际结果、根因或尚未确认的假设、修复、回归证据、是否转为规则。不能把尚未验证的假设称为根因；未修复问题保留开放状态。
