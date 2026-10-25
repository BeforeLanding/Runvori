# DEV-005 上游审批请求身份映射切片

日期：2026-10-25。需求关联：FR-02、FR-04、FR-09；路线图：M0 兼容映射与 M1 审批状态。项目仍处 M0。

## 实现与依据

- 已保存的命令和文件审批样本见 [命令拒绝](DEV-003-command-declined.json) 与 [文件拒绝](DEV-003-file-change-declined.json)。它们来自 CLI `0.163.0-alpha.11.2`；当前固定版本为 `0.164.0-alpha.11.1`，尚须重新联调。
- `record_approval_request_event` 识别 `item/commandExecution/requestApproval` 与 `item/fileChange/requestApproval`，校验 JSON-RPC ID、threadId、turnId。整数与字符串请求号使用独立命名空间；共享库按当前 epoch 和已绑定的上游回合定位 run，在一个事务中写审批、事件及快照。
- 对方法、类型化请求号和完整参数的规范化 JSON 计算 SHA-256，仅持久保存摘要。同一身份及内容重放返回原审批，不追加事件；请求内容或种类变化拒绝。旧审批表自动增加可空 `request_digest` 列，旧记录保留。事件库 schema 版本升至 v6。摘要只用于重放一致性，不替代命令或文件内容安全审查。

## 验证与限制

`python scripts/check.py`：16 份契约通过；157 项测试运行，152 通过、5 项因本环境链接创建或回环连接限制跳过。测试使用合成 JSONL peer 经 `StdioClient.next_event` 到共享 SQLite，覆盖重复请求、整数/字符串同号隔离、错误回合/代次、请求内容冲突、关闭后重放及旧表加列保留原行。

`scripts/smoke_package.ps1`：离线 wheel 构建、隔离安装、已安装 CLI 的 16 份契约核对及模块导入通过。

尚无产品事件循环自动调用本适配器；文件变更内容的 item 关联及权限范围审查仍待实现。合成 peer 不能代替当前锁定 CLI 的真实审批联调。
