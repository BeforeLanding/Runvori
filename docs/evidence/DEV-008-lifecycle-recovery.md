# DEV-008 Android 生命周期恢复专项

日期：2026-10-28。需求关联：FR-01、FR-03、FR-04、FR-07；路线图：M2 主流程与恢复。状态：**实现与合成设备验收通过；未使用真实模型回合；未覆盖真机与性能**。

## 范围

本专项在不调用模型的前提下验证手机端的生命周期恢复边界：

- 审批页切后台、Activity 旋转、进程被系统杀死或强制停止后重开；
- 已提交决定但回执丢失，重开页面不得再次提交；
- Host 重启后，旧审批的按钮必须失效；
- 产物详情快速关闭/重开/切换时，旧请求不得覆盖当前页面；
- 断言“不重复发起决定、不自动重跑回合、未知不显示为成功、游标与内容恢复一致”。

## 实现

- `MainActivity` 用 `ViewModelProvider` 保留 ViewModel；配置文件变化不会重建网络状态和当前屏幕。
- 新增 `NavigationRouteStore`：只持久化 `host_id`、屏幕名、`session_id`、`approval_id`、`run_id`，不保存正文、命令或令牌。进程重建时按该指针恢复位置，随后从 Host 与 SQLite 缓存重新读取事实；host identity 改变或重新配对时清除。
- `RunvoriViewModel` 增加 `pause()`/`resume()`：`ON_STOP` 停止轮询，`ON_START` 按当前屏幕重新同步；恢复不自动发起新回合。
- 决定在发网络前写入 `operations` 缓存（`resource_id=approval_id`）。打开审批时按 `(approval, approval_id)` 找回先前的请求，只查询其状态；`unknown`/`not_found`/网络失败保持“结果待确认”并禁用按钮，不重新提交。用户再次点击也不会生成第二个操作。
- Host 重启、审批不存在或已过期时，`openApproval`/刷新把屏幕标为 stale 或显示终态并移除允许/拒绝按钮；直接向 Host 提交旧审批由服务端拒绝。
- 产物分块继续用请求代际和当前屏幕校验；关闭/切换后返回的块被丢弃。

## 场景与结果

命令（Windows，API 37 x86_64 模拟器，合成上游，0 个模型回合）：

```text
ANDROID_HOME=<Android SDK> python scripts/accept_device.py \
  --serial emulator-5554 --only o,q,w,x,t,y,z,s2 \
  --report .dev/dev008-lifecycle.json
```

| 场景 | 结果 | 断言 |
|---|---|---|
| `o` | passed | 审批页暂停轮询后保持屏幕；重建新 ViewModel 从持久位置恢复同一审批，按钮仍可拒绝；期间没有新操作。 |
| `q` | passed | 先写入一个只有本地回执、Host 不存在的决定；进程重建后审批页显示“结果待确认”，允许/拒绝均禁用，操作数不变；再次点击不新建操作。清理用一次独立 Host 决定，未重试。 |
| `w` | passed | 不存在的审批打开后显示 stale 文案，不渲染允许/拒绝按钮；返回时间线正常。 |
| `x` | passed | 连续打开 A→B，以及 A→关闭→B；最终内容始终等于 B 的 Host 内容，旧块没有追加。 |
| `t` | passed | 生产 `MainActivity` 的 ViewModel 在真实 Activity 旋转后仍在审批页；拒绝按钮可用、允许按钮禁用；拒绝后回合结束。 |
| `y` | passed | 切后台后 `am kill` 杀死缓存进程；重开仍恢复审批与“结果待确认”；Host `runs` 与 `approval_operations` 计数不变，决定未重发。 |
| `z` | passed | `am force-stop` 单独记录；进程消失后重开同样恢复审批与未确认状态；计数不变。系统杀死与强制停止没有被合并成一个结论。 |
| `s2` | passed | 先留下一个待审批回合，Host 在同一端口/数据目录/证书重启；epoch 前进、待审批在重启后不再 pending；旧审批显示终态/stale，按钮不可用，直接决定被 Host 拒绝。 |

系统 `am kill` 只终止缓存进程；为了让“系统杀进程”真实生效，验收先回桌面并等待约 5 秒，再 `am kill`，随后用 `pidof` 断言进程消失。`force-stop` 走独立路径并单独记录。两者都验证 UI 恢复和 Host 计数不变。

## 离线与构建

- Android JVM 单测通过；Debug APK 与 instrumentation APK 构建、安装通过。
- 合成场景 `o/q/w/x`、`t`、`y/z`、`s2` 分别通过，全套 22 个合成场景复跑通过。
- 未降低版本门，未访问模型；真实 Codex 的 `l/m/v` 结果见 [第三切片证据](DEV-008-real-approval-slice.md)。

## 限制

- 仅 API 37 模拟器；没有真机、200% 字体、TalkBack、长列表性能与连续十次演示。
- `am kill` 的生效依赖系统把应用置为缓存进程；验收记录了等待和 `pidof` 判据，但不同 OS 版本的缓存回收时机可能不同。
- 持久位置只在重新配对或 Host 身份变化时清除；用户主动停用应用并重开后也会回到上次页面，这是产品选择，不是系统恢复的证明。
- 产物快速切换用 Host 的实际小块内容验证；没有注入人工延迟制造必然在途的响应，回归价值来自代际守卫与最终内容/来源断言。
- 没有真实 Codex 模型回合；真实文件审批 `l/m`、验证结论 `v` 与重启读回 `r` 已在第三切片单独完成并通过（[ERR-038](../ERRORS.md) 已关闭），本专项仍只在合成上游上验证生命周期。

## 关联

- [第三切片证据](DEV-008-real-approval-slice.md)
- [ERR-039](../ERRORS.md)：旋转/进程重建与未确认决定恢复
- [协议与状态](../PROTOCOL.md)、[测试计划](../TEST_PLAN.md)
