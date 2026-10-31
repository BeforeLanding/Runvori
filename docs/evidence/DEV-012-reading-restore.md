# DEV-012 进程恢复后的阅读位置

日期：2026-10-31～11-01。状态：**进程恢复设备场景 `rk` 在模拟器通过；按条目键恢复锚点经变异实测不必要，未纳入实现**。

关联：FR-06、NFR-03；AT-10、AT-11。模型回合：**0**（合成上游）。

## 1. 场景 `rk`

`rk_reading_anchor_survives_process_restore`：

1. 跑完一次 `[stream]` 回合（30 条消息），两次 `swipeDown` 进入浏览，记录一个完全可见条目相对时间线顶部的位置。
2. 进程恢复：关闭旧 ViewModel，用同一路由存储与缓存新建 ViewModel；用 `StateRestorationTester` 从保存状态重建组合。重建的组合在重组时才读取新 ViewModel，保证“先保存旧界面、再以新进程状态组合”的顺序。
3. 断言：仍处于浏览（有“回到最新”），该条目在可视区内，相对列表顶部位移 ≤ 8 dp。

位置相对时间线顶部测量：恢复后列表上方出现横幅会使整个列表下移，属于布局变化，不计入阅读位移（TEST_PLAN §3）。

## 2. 排查中发现并修复的问题（ERR-042）

首轮 `rk` 恢复到了错误条目。临时日志显示：第二次滑动的拖动结束时 `isScrollInProgress` 短暂为 false，阅读模型在此刻按手势结束结算；随后的惯性滑动又继续移动了列表，但手势标记已清，最终停止位置没有被结算。修复：浏览期间每次滚动停止都结算（`TimelineScreen` 中的停止判定）。

## 3. 按条目键恢复：实现后撤回

本轮先实现了按 `(item key, offset)` 保存并恢复锚点。变异检查（关闭按键恢复，只留 Compose 按下标＋偏移的默认保存）后 `rk` **仍然通过**：恢复后的时间线由完整本地缓存重新投影，列表与恢复前一致，按下标即可回到原条目。没有可证明的作用，因此撤回该实现，保留场景 `rk` 作为回归，并在 UX.md 记录修订与重新引入的条件。

第一次变异尝试因变异代码未能编译而无效（跑的是旧 APK），已丢弃，未计入结论。

## 4. 结果

| 运行 | 设备 | 结果 | 报告 |
|---|---|---|---|
| 默认合成全套 | `Medium_Phone` API 37 x86_64 模拟器 | 24/24 通过（含 `rd`、`rk`） | [DEV-012-restore-all.json](DEV-012-restore-all.json) |
| `--profile device-baseline` | 同上 | 15/15 通过（含 `rd`、`rk`） | [DEV-012-restore-baseline.json](DEV-012-restore-baseline.json) |
| 变异：关闭按键恢复 | 同上，`--only s,rk` | `rk` 通过（证明按键恢复在当前数据流中不必要） | [DEV-012-restore-mutation.json](DEV-012-restore-mutation.json) |

Android JVM 单测 30 项通过。

## 5. 未覆盖

- 真实 Activity 在 `am kill` 后的保存状态恢复（本场景用 `StateRestorationTester` 等价模拟）。
- 同一长消息原地增长时的内部偏移、向前分页后的锚点、TalkBack、帧耗时。
