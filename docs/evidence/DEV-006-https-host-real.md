# DEV-006 真实 Codex HTTPS 验收（显式 1 次）

日期：2026-10-27。需求关联：FR-01、FR-04、FR-05、FR-09；路线图：M1 最小协议。项目仍处 M0。

## 环境

- OS：Windows 11 Home China 10.0.26200；Python 3.12.10。
- Codex：锁定版本 `codex-cli 0.164.0-alpha.11.1`，绝对路径，专用已登录 `CODEX_HOME`（仓库 `.dev/codex-home`，不入库）。
- Host：真实 `HostRuntime` 子进程，`expected_upstream_version` 生效（启动前版本门），TLS 回环证书、命名管道管理入口、HTTPS 监听 `127.0.0.1:0`。
- 客户端：`scripts/accept_https_host_real.py` 使用与合成闭环相同的独立标准库 HTTPS 客户端，只走 `/v1` 协议；固定 SPKI 指纹。
- 命令：`RUNVORI_REAL_HOST_TESTS=1 RUNVORI_REAL_CODEX=<绝对路径> RUNVORI_REAL_CODEX_HOME=<.dev/codex-home> python scripts/accept_https_host_real.py`
- 运行次数：**1 次**，未自动重试，未修改夹具后重跑。

## 结果

`status: passed`，退出码 0：

- Host 启动（epoch 1），版本门通过。
- 本地管道登记工作区、创建会话并排队首个 `thread/start`；产品路径创建真实 thread 并绑定（`snapshot_seq=0`）。
- 配对交换成功；HTTPS 客户端携带设备令牌。
- 新回合受理（202），真实模型回合开始。
- 长轮询读到 `approval.requested`；审批详情 `details_status=complete`、`command_present=true`、`cwd_inside_workspace=true`、`allowed_decisions=[allow_once, deny]`。
- 允许一次（202）；操作最终 `confirmed/request_closed`；回合 `run.completed`；新回合操作 `confirmed/turn_started`。
- 事件序列（12 条）同时含 `approval.requested`、`approval.updated` 与终态事件。

## 与预期的差异

- `command_matches_probe=false`：上游显示的命令串不是裸 `python --version`。本机 Windows 上 Codex 以 shell 包装形式发起命令（见 [ERR-007](../ERRORS.md) 与 [ERR-016](../ERRORS.md)），本次未保存命令正文，因此只能确认"命令存在且 cwd 在登记工作区内"，不能宣称展示串与探针字面量一致。这是审批详情映射仍需覆盖的形态，已计入未完成项。
- 未捕获审批 `expires_at`（协议允许 null）；文件审批的 insufficient 路径由合成闭环覆盖，未在本轮真实运行中触发，以免追加模型回合。

## 限制与未覆盖

- 只跑了 1 次真实回合；不复现批量稳定性，也不代表其他审批类型。
- AT-16 仍未整体通过：首发 OS 未定；starting/决定派发/中断切点的杀点由测试 relay 注入；真实上游正常退出且后代继承 stdio 的 EOF 检测（ERR-027）未覆盖。
- 本次未验证证书轮换、Android 信任链与实机联调。
- 运行产生一次真实模型调用费用；复跑需再次确认。