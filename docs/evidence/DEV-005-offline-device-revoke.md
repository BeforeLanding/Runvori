# DEV-005 离线本地设备撤销命令

日期：2026-10-26。需求关联：FR-01、FR-06；路线图：M1 Host 本地管理入口前提。项目仍处 M0。

## 行为

- 新增 `runvori revoke-device --data-dir <绝对目录>`，只处理现有 `pairing.db`。命令先取得与 Host 相同的 Windows `host.lock`，锁冲突则拒绝，不绕过运行中的读取网关取消机制。
- 数据目录及配对库拒绝符号链接和 junction；配对库不存在时不创建空库。命令不启动 Codex、不访问模型、不输出令牌或配对秘密。成功时仅返回固定的撤销状态。
- 这是 Host 停止后的离线管理路径。运行中撤销仍需将上一轮的 `DeviceReadGateway.revoke_local_device()` 接入未来可信本地管理入口。

## 验证与限制

`python scripts/check.py`：16 份契约通过；269 项测试运行，264 通过、5 项因本环境链接创建或回环连接限制跳过。新增 3 项 Windows 子进程测试验证撤销已配对设备、Host 锁占用时令牌保持有效、缺失配对库不被创建。`.\scripts\smoke_package.ps1`：离线 wheel 构建、隔离安装、已安装 CLI 契约校验及模块导入通过。

该命令只支持 Windows 单实例锁和当前独立配对库切片；尚无产品 Host 启动、运行中本地管理通道、HTTPS API 或 Android 配对。外部进程若未遵守同一锁，无法靠此命令证明它已停止。旧设备上已缓存的数据由客户端清理流程处理，不由撤销命令远程删除。
