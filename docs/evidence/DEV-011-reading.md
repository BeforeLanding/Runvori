# DEV-011 时间线阅读模型与浏览模式设备验收

日期：2026-10-31。状态：**阅读模型已实现；浏览模式合成设备场景 `rd` 在模拟器通过；未做帧耗时与真机测量**。

关联：FR-03、FR-06、NFR-03；AT-11。模型回合：**0**（全部使用合成上游）。

## 1. 交付

- `domain/ReadingModel.kt`：跟随最新 / 浏览历史两种模式，未读数按条目键计算；向上加载的旧条目、同条目增长不计为新内容；锚点条目消失时回到跟随。JVM 单测 11 项。
- `ui/TimelineScreen.kt`：打开会话停在最新；只有用户拖动能离开跟随，滚动停下时按末尾是否可见决定恢复；浏览时显示“回到最新 · N 条新内容”。每个时间线条目带稳定标签 `timeline_item_<key>`。
- 合成上游 `[stream]`：约 9 秒内逐条追加 30 条 Agent 消息（`STREAM_ITEMS`、`STREAM_INTERVAL_S`）。
- 设备场景 `rd`（`rd_browsing_keeps_position_while_output_streams`），加入默认合成集与 `device-baseline`，只能在合成上游下运行。

## 2. 场景断言

1. 跟随：最新的流式条目在时间线可视区内，没有“回到最新”。
2. 浏览：`swipeDown` 手势后出现“回到最新”；取一个完全在可视区内的条目，记录其屏幕坐标。
3. 至少 3 条新消息到达后，该条目仍在屏幕上且位移 ≤ 8 dp（本机 21 px），按钮文案含“条新内容”。
4. 点击“回到最新”后按钮消失；30 条全部到达、回合结束时最后一条在可视区内。

可见性一律按屏幕坐标（`boundsInRoot` 与时间线视口相交）判断，组合但滚出视口的节点不算可见——这正是 ERR-041 的教训。

## 3. 结果

| 运行 | 设备 | 结果 | 报告 |
|---|---|---|---|
| 默认合成全套 | `Medium_Phone` API 37 x86_64 模拟器 | 23/23 通过（含 `rd`） | [DEV-011-reading-all.json](DEV-011-reading-all.json) |
| `--profile device-baseline` | 同上 | 14/14 通过（含 `rd`） | [DEV-011-reading-baseline.json](DEV-011-reading-baseline.json) |
| 变异检查：浏览时仍强制滚到底 | 同上，`--only s,rd` | `rd` 失败：被阅读条目离开屏幕（位移读为 NaN） | [DEV-011-reading-mutation.json](DEV-011-reading-mutation.json) |

变异只用于证明场景能变红，源码已还原并重新构建后再跑上表前两行。

## 4. 未覆盖

- 同一长消息在原地增长时的内部偏移、向前分页后的锚点、进程重建后按条目键恢复（当前按下标与偏移）。
- TalkBack、帧耗时 / 内存 / 恢复耗时（Q-06，PRD NFR-04）与实际手机。
