# DEV-003：Windows junction 路径边界复验

日期：2026-10-24，Asia/Shanghai。对应 Q-01/Q-07、FR-07/FR-09 的本地文件读取前提。

在 Windows 11、Python 3.12.10 的仓库 `.dev` 一次性夹具中，PowerShell `New-Item -ItemType Junction` 可在获准的本地执行环境创建目录 junction。夹具中 `workspace/linked` 指向工作区外、仍位于一次性夹具内的目录，目标 `secret.txt` 为固定 `b"secret"`。测试结束删除 junction 与夹具，不读取用户工作区内容。

新增 `host/tests/test_windows_junction.py`，使用进程环境变量传递固定夹具路径给 PowerShell，不拼接路径到命令文本。真实 `Path.is_junction()` 为真；`capture_worktree_file(workspace, "linked/secret.txt")` 返回 `artifact_link_not_allowed`；审批派发记录进入 `unknown` 后，`inspect_unknown_file` 对同一路径仅记录 `unsafe_path`，外部目标文件字节保持不变。

在获准的本地执行环境运行 `python scripts/check.py`：16 份契约校验通过；80 项测试运行，78 通过、2 项因本机不能创建符号链接而跳过。普通沙箱此前创建 junction 也受限；本次成功只证明 junction 分支在该环境的实际行为。文件检查与打开之间的并发替换风险仍在，且未验证真实符号链接与生产 Host 的执行权限边界。
