# M0/Q-04：持锁打开审批记录的启动切片

日期：2026-10-24，Asia/Shanghai。对应 Host 单实例与持久化启动顺序的技术前提；不是可运行的产品 Host 服务。

新增 `LockedApprovalJournal`：调用方提供已存在、由 Host 管理的绝对数据目录；先持有 `host.lock`，再打开同目录的 `approvals.db`。若锁不可得，SQLite 构造器不会被调用；若打开数据库失败，立即释放锁。正常关闭先关数据库再释放锁。该切片故意不调用 `start_epoch_after_isolation`，因为取得 Host 锁并不能证明旧 app-server 已终止。

真实跨进程测试中，子进程持有锁与 SQLite 记录并写入就绪标记；第二进程启动被拒绝。终止持锁子进程后，新进程重新打开记录，`current_epoch()` 仍为 0，没有伪造恢复完成。另用故障夹具使 SQLite 路径成为目录，打开失败后可重新获取锁并正常建库；补充构造器注入测试确认争锁失败时不会触及 SQLite。

在获准的本地执行环境运行 `python scripts/check.py`：16 份契约通过；85 项测试运行，83 通过、2 项因本机符号链接创建权限跳过，真实 junction 回归通过。`scripts/smoke_package.ps1` 再次通过，确认新模块包含在当前 wheel 并可从隔离安装中导入。

尚未验证：产品 Host 的实际启动/退出生命周期、旧 app-server 的进程隔离、审批 epoch 推进、活动槽/事件事务及 TLS/API。该切片只保证参与同一锁协议的 Host 进程按序打开这份记录；Q-04 和 M0 仍开放。
