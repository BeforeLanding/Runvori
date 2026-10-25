# DEV-005 上游回合终态身份映射切片

日期：2026-10-25。需求关联：FR-02、FR-04、FR-09；路线图：M0 兼容映射与 M1 回合状态。项目仍处 M0。

## 实现与依据

- 既有真实探针使用 `turn/completed` 的 `params.threadId`、`params.turn.id` 和 `params.turn.status` 识别 `completed / failed / interrupted`，见 [DEV-003 记录](DEV-003.md)。真实样本来自旧 CLI 版本；当前固定版本仍需联调。
- 新适配器校验通知形状与终态值；持锁单库入口以当前 epoch、thread 和 turn 查询已绑定回合，在同一事务内写 `run.completed / run.failed / run.interrupted`、更新快照并释放活动槽。重复相同终态返回原事件，冲突终态或错误身份拒绝。终态事件写入失败时，回合及活动槽保持原状态。
- 回合终态仅代表上游回合报告，不推断命令退出码、验证结果、文件副作用或审批请求是否已清理；后者仍由独立通知处理。

## 验证与限制

`python scripts/check.py`：16 份契约通过；160 项测试运行，155 通过、5 项因本环境链接创建或回环连接限制跳过。合成 JSONL peer 经 `StdioClient.next_event` 到单库终态事务；覆盖错误 thread/epoch、重复和冲突终态、三种状态、事件插入失败回滚及畸形消息拒绝。

`scripts/smoke_package.ps1`：离线 wheel 构建、隔离安装、已安装 CLI 的 16 份契约核对及新增模块导入通过。

尚无产品事件循环自动调用该映射；当前锁定 CLI 的真实终态联调、终态时未结束审批的清理策略和进程隔离证明仍待完成。合成 peer 不能代替真实联调。
