# DEV-003：Codex CLI 版本迁移审查

日期：2026-10-24，Windows / Python 3.12.10。原锁定版本 `codex-cli 0.163.0-alpha.11.2`；本机 CLI 更新为 `codex-cli 0.164.0-alpha.11.1`。第一次脏工作区探针在模型调用前被 `codex_version_mismatch` 拦截，未绕过版本门控。

使用隔离、无登录的进程局部 `CODEX_HOME` 运行 `codex app-server generate-json-schema`，逐一对照既有的 14 份契约。12 份字节相同；`v2/ThreadStartResponse.json` 与 `v2/TurnCompletedNotification.json` 仅在图像生成 thread item 增加 `ImageGenerationInputImage`、`inputImages`，并允许 `fileId` 或 `result` 形式。命令/文件审批请求和响应、turn 启动与中断契约未变。仓库当前探针不处理图像生成 item；这不构成其他能力在新版本上的兼容证明。

审查后更新精确版本锁，执行 `python scripts/runvori.py export-schema --replace`，重新生成清单 SHA-256；`python scripts/runvori.py doctor` 的隔离握手通过。`python scripts/check.py`：14 份 schema 校验、53 项离线测试通过。随后使用专用登录目录重跑一次真实脏工作区探针，结果见 [脱敏观察](DEV-003-dirty-workspace.json)。历史 0.163 版本探针记录保持原版本标注，不视作 0.164 回归。

同日后续开发为验证命令的结构化时间与身份关联，将 `v2/ItemStartedNotification.json` 和 `v2/ItemCompletedNotification.json` 加入精确锁定清单；当前共 16 份契约。两份文件直接从同一 0.164 CLI 导出，未修改既有 14 份内容。`python scripts/check.py` 随后通过 16 份契约和 61 项测试；真实命令观察见 [记录](DEV-003-required-verification.json)。
