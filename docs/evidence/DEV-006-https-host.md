# DEV-006 可通过 HTTPS 操作的最小 Host（合成上游闭环）

日期：2026-10-27。需求关联：FR-01、FR-04、FR-05、FR-06、FR-08；路线图：M1 最小协议。项目仍处 M0，AT-16 剩余缺口未关闭。

## 本轮交付

- **审批详情与禁止盲批**：审批请求的 `command / cwd / reason / itemId / startedAtMs` 与文件 item 的 `path / kind / move_path / diff` 现落库为有界详情（≤32 KiB，diff ≤16 KiB）。`details_status` 为 `complete` 且内容未截断、cwd 在登记工作区内、`kind` 为 `command` 时才提供 `allow_once`；否则只允许拒绝，允许决定返回 `approval_details_insufficient`。
- **事件库 v14**：`approvals` 新增 `turn_id / item_id / upstream_approval_id / requested_at / details_json / details_status`，新增 `approval_items` 承接 `item/started|item/completed` 的文件改动摘要（支持审批先到或 item 先到两种顺序）。v13 旧记录的详情不可回填，迁移为 `insufficient`，只能拒绝。
- **HTTPS 入口**：标准库 `ThreadingHTTPServer` + `ssl`，TLS 必需（TLS ≥1.2、SPKI 指纹由实际证书计算），并发 16、连接超时 30 s、请求体 ≤64 KiB、单页 ≤256 KiB。仅当显式 `--listen host:port` 与 `--tls-cert/--tls-key` 同时给出时监听。
- **设备认证与限流**：`Authorization: Bearer` 令牌由服务端解析并映射 `device_id`；请求体不能指定 actor。协议头不符在任何业务处理前拒绝。配对交换按来源 5 次/分、全局 20 次/分限流，并返回 `Retry-After`。
- **最小业务接口**：`/v1/host`、`/v1/sessions/{id}/snapshot`、`/v1/sessions/{id}/events`、`/v1/sessions/{id}/runs`、`/v1/approvals/{id}`、`/v1/approvals/{id}/decision`、`/v1/runs/{id}/interrupt`、`/v1/operations/{request_id}`。写请求 `202` 只表示已持久受理。
- **本地管理入口**：Windows 命名管道（每安装唯一名字 + `admin.key`）承载 `status / issue-pairing / revoke-device / register-workspace / create-session / start-thread`，全部经命令邮箱在 SQLite 所有者线程执行。CLI 新增同名子命令；`revoke-device` 优先走运行中的 Host，Host 未运行时回落离线撤销并标注 `mode`。
- 停止顺序：先停 HTTPS 与管道（取消在途长轮询），再关邮箱、隔离上游、关库；上游恢复退出同样先停入口。

## 验证

`python scripts/probe_https_host.py`：对真实 `HostRuntime` 子进程（合成 app-server）执行 15 步独立 HTTPS 客户端闭环，全部通过：

- 错误 SPKI 指纹不发出任何请求；配对成功；同一秘密重放 409；第 6 次交换 429 且带 `Retry-After`。
- `/v1/host`、快照（含工作区别名）、新回合受理 202。
- 长轮询读到 `approval.requested`；审批详情 `details_status=complete`、命令存在、cwd 在登记工作区内、`allowed_decisions=[allow_once,deny]`。
- 决定受理 202；操作最终 `confirmed/request_closed`；回合 `run.completed`。
- 断线补拉：从 `after_seq=0` 取 12 条事件，seq 连续、无重复。
- 文件审批缺少 item 证据时 `details_status=insufficient`；`allow_once` 返回 409 `approval_details_insufficient`；`deny` 受理 202。
- 运行中撤销：在途长轮询被终止，后续请求 401。

`host/tests/test_api_handlers.py` 覆盖 Bearer 解析、协议门、限流窗口、actor 只来自令牌、超大输入不进入邮箱、命名管道往返/错密钥/未运行与配对身份解析；`host/tests/test_approval_details.py` 覆盖归一化边界（工作区外 cwd、`writeStdin`、绝对/越界路径、截断 diff、损坏记录）、v13→v14 迁移、item 补齐与闸门。`host/tests/test_https_host_flow.py` 把上述闭环作为 unittest。

## 限制

- 本记录时点未实现会话列表、命令/产物明细与 Android 联调；会话列表由 DEV-007 补齐，命令明细与产物由 DEV-008 补齐，见 [DEV-008 产物证据](DEV-008-artifacts.md)。
- 标准库 HTTP 服务为单进程有界实现，非生产级；替换为其他框架不得改变邮箱边界与协议。
- 命名管道依赖同一 Windows 用户会话；`admin.key` 的访问控制依赖数据目录本身。
- 长轮询在客户端断开时最迟占用一个连接至 `wait_ms` 上限（25 s），受 16 并发限制保护。
- TLS 证书轮换仍需本地重新配对；本轮不做自动信任新公钥。
- 真实上游正常退出且后代继承 stdio 的 EOF 检测（ERR-027）、首发 OS 与 relay 注入时序仍是 AT-16 未闭合项。