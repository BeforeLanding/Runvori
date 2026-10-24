# DEV-001/Q-04：当前 Host 探针包复验

日期：2026-10-24，Asia/Shanghai。范围是当前源码的离线 wheel 与已安装命令入口；不代表产品 Host 可部署。

环境：Windows 11、Python 3.12.10、pip 25.0.1、setuptools 81.0.0；项目 `pyproject.toml` 限定 Python `>=3.12,<3.13`，无第三方运行依赖。构建、安装的临时文件均放在仓库忽略的 `.dev`，不用模型账号或网络。

执行 `powershell -File scripts/smoke_package.ps1`：离线 `pip wheel` 成功生成 `runvori_host-0.0.1-py3-none-any.whl`；新虚拟环境离线安装成功；从仓库外运行已安装的 `runvori.exe verify-contracts --workspace <checkout>` 返回 `status=passed, schema_count=16`；新增的审批记录、产物和执行探针模块均可由已安装包导入。脚本打印 `PASS offline_wheel isolated_install installed_cli module_imports`，并清理一次性虚拟环境。

发现旧的 `pip install --target` 路径只能在手工插入目标目录到 `sys.path` 后导入模块；直接运行该目标目录生成的 `bin/runvori.exe` 会报 `ModuleNotFoundError: No module named 'runvori'`。本轮改用正常虚拟环境验证命令入口，未修改产品运行依赖。初次创建虚拟环境的 `ensurepip` 在默认临时目录失败；设置进程局部 `TEMP/TMP` 到 `.dev` 后成功。

本轮证据仅关闭当前源码的 wheel 构建、隔离安装及 CLI 入口复验。命令仍依赖 checkout 中的 `contracts/codex` 和显式 `--workspace`；当时单实例锁尚未建立，持久化业务 Host、服务依赖与部署包仍未建立，Q-04 保持开放。

2026-10-24 后续复验：新增独立 Windows 单实例锁切片后，`scripts/smoke_package.ps1` 再次通过，新模块也能由已安装 wheel 导入。锁的跨进程行为见 [M0/Q-04 证据](M0-Q04-instance-lock.md)；该锁尚未接入产品 Host 启动。

2026-10-24 启动切片补充：加入持锁打开审批 SQLite 的模块后，再次运行同一包冒烟命令，wheel 构建、隔离安装、16 份契约校验和新模块导入均通过；见 [启动顺序证据](M0-Q04-locked-journal.md)。

2026-10-24 配对切片补充：加入独立一次性配对 SQLite 模块后，包冒烟命令再次通过；它尚非 HTTPS Host 接口，见 [配对记录证据](M0-Q05-pairing-journal.md)。
