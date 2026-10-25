# DEV-005 上游回合确认持久切片

日期：2026-10-25。需求关联：FR-02、FR-08、FR-09；协议关联：`run.started`、操作 `confirmed` 与进程代次。当前仍处 M0，本切片不是产品 Host 与 Codex 的联调证据。

## 实现与边界

- 事件库 schema 从 v3 升至 v4。派发领取记录调用方提供的正整数进程代次；`upstream_turns` 按 `run_id` 保存 thread/turn 与代次，并对同代次同 thread/turn 设置唯一约束。打开 v3 数据库保留既有操作、回合和活动槽。
- `confirm_run_started` 只接受已领取的 `dispatching`，或收到迟到证据的 `unknown` 操作；代次必须与派发领取一致，回合仍为 `starting` 且占用活动槽。单事务内追加 `run.started` 与 `operation.updated`、保存关联、更新回合为 `running`、操作为 `confirmed`（`outcome=turn_started`）以及快照/版本。
- 同一关联重复确认返回原操作，不再增加事件；不同 turn 或将同一上游 turn 归给另一个回合均拒绝。任一事件写入失败时，两条事件和全部状态变化回滚。普通事件追加入口不能自行写 `run.started`。

## 验证

执行 `python scripts/check.py`：16 份契约通过；122 项测试运行，117 通过、5 项因本环境链接创建或回环连接限制跳过。新增 7 项行为测试覆盖匹配确认和重复通知、未知状态的迟到证据、错代次/冲突关联、上游 turn 重复归因、第一及第二条事件写入失败回滚，以及 v3 数据升级。

`process_epoch` 目前由可信调用方提供，模块未生成或校验受管进程身份。尚未将真实 Codex `turn/start` 响应/通知接入此方法，也未完成旧进程隔离、重启核对、审批或正式 API。合成关联通过只证明本地事务语义，不能宣称真实上游回合已得到产品级确认。
