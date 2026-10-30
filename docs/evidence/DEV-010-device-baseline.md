# DEV-010 真机地址配置与零模型验收入口

日期：2026-10-30。状态：**入口与离线恢复实现完成；模拟器零模型基线通过；实际手机基线未执行**（本轮没有连接物理设备，`adb devices` 只有 `emulator-5554`）。这不是“真机基础闭环通过”的证据，也不代表 AT-09/AT-11 完成。

关联：FR-01、FR-03、FR-04、FR-06、NFR-02、NFR-03；AT-01、AT-04、AT-09、AT-10、AT-11。模型回合：**0**。

## 1. 本轮交付

### 1.1 地址与绑定分开配置

`scripts/accept_device.py` 新增：

| 参数 | 作用 |
|---|---|
| `--serial` | adb 设备 serial；与 Host 地址解耦 |
| `--host-address` | 手机实际拨号的局域网/VPN IP 或 DNS 名；物理设备缺省时拒绝运行 |
| `--listen-address` | Host 本地绑定地址；模拟器/回环默认 `127.0.0.1`，LAN IP 默认绑定同一字面地址，DNS 必须显式指定 |
| `--host-port` | 可选固定 TLS 端口，便于物理设备路径上的防火墙规则；缺省仍由系统分配 |

行为约束：

- 模拟器保持历史路径：`10.0.2.2` 端点、`127.0.0.1` 绑定、证书 `IP:10.0.2.2`。
- 物理设备：`--host-address` 可以是 IPv4、IPv6 或 DNS 名；证书 SAN 分别写 `IP:` 或 `DNS:`，客户端仍执行证书链、主机名和 SPKI 固定指纹三项校验。没有任何关闭校验的分支。
- 默认绑定不会扩散到所有接口：LAN IP 默认只绑该地址；只有显式 `--listen-address 0.0.0.0` 才会监听全部接口，报告记为 `non_loopback`。
- 报告不含物理设备 serial、LAN/VPN 地址或 DNS 名；只保留设备型号/API/ABI、地址种类和端点摘要。模拟器保留 `10.0.2.2` 与 `emulator-5554` 便于复现。

### 1.2 安装与构建核对

`--apk` / `--test-apk` 在运行前：

- 校验文件存在，计算 SHA-256 和大小；
- 安装 App APK 与 instrumentation APK（`adb install -r -t`）；
- 查询 `dev.runvori.app` 的已安装版本，并与 APK 的 `output-metadata.json` 版本核对；
- 要求 `dev.runvori.app.test` instrumentation 包已安装。

### 1.3 零模型入口

`--profile device-baseline` 显式选择以下 14 个场景，且拒绝 `--real`：

`s、a、b、c、o、y、z、i、j、k、fs、off、rec、e`

全部使用合成 app-server；`expected_model_turns=0`。`b`/`fs` 会创建本地合成回合，但不访问模型，也不读取真实 Codex 认证。

### 1.4 离线缓存与恢复

Android 客户端现在在 Host 不可达时：

- 从本地 SQLite 展示上次同步的会话列表与时间线；
- 保持只读：`writable=false`，不显示发送新回合的输入区；
- `startRun` 的编程入口也再次检查活连接，离线时拒绝并保留错误提示；
- 会话列表刷新、时间线轮询在离线时持续尝试重连；重连成功后从缓存游标补拉；
- 写入口不在离线时排队，也不产生新的 run/approval 操作。

## 2. 模拟器零模型基线

### 2.1 环境与构建

- 主机：Windows 11 Home China 10.0.26200；Python 3.12.10。
- 设备：`Medium_Phone` 模拟器，API 37，x86_64，Android 17；serial `emulator-5554`。
- App APK：`0.0.2`（versionCode 2），SHA-256 `0dc4a01e3af0160384349b8cf12c174c83d4833c17e2bcb112fe7fbc09be5341`。
- Instrumentation APK：SHA-256 `40c299cae240e0b5a66d797036ba4cb3ac10baf806c41bbdc1b585377d2a189e`。
- 传输：真实 APK → 固定指纹 HTTPS → 真实 `HostRuntime` → 合成 app-server；端点 `10.0.2.2`，Host 只绑定 `127.0.0.1`。
- 报告：[DEV-010-device-baseline.json](DEV-010-device-baseline.json)，总体 `status=passed`，`expected_model_turns=0`，退出码 0。

### 2.2 场景结果

| 场景 | 结果 | 本轮验证 |
|---|---|---|
| `s` | passed | Keystore 凭据往返，磁盘无令牌明文 |
| `a` | passed | 错误指纹在请求前拒绝，不存储凭据 |
| `b` | passed | 配对、列会话、读时间线、完整审批、命令/验证/产物读取 |
| `c` | passed | 丢弃内存连接后从磁盘游标重同步，无重复/缺口 |
| `o` | passed | 切后台与进程重建恢复审批；恢复后可操作并结束回合 |
| `y` / `z` | passed | 系统 `am kill` 与 `force-stop` 后恢复审批，run/决定计数不变 |
| `i` | passed | 回合 `completed` 与验证 `failed` 同时显示 |
| `j` / `k` | passed | 完整文件审批允许/拒绝与差异/提案读取 |
| `fs` | passed | 系统字体 2.0（200%）下会话、时间线、审批页可滚动并可拒绝 |
| `off` | passed | Host 不可达时显示缓存会话与时间线；不显示发送入口；编程写入被拒；无新操作 |
| `rec` | passed | Host 同端口重启后从游标恢复，seq 连续、无重复，终态仍在 |
| `e` | passed | 撤销后旧令牌读取被拒 |

Host 侧离线证据：

- `offline_state.runs_unchanged=true`、`offline_state.decisions_unchanged=true`：离线与恢复检查期间没有新增 run 或审批操作。
- `offline_restart.port_same=true`、`offline_restart.epoch_advanced=true`：Host 在同一端口、同一数据目录以新 epoch 重启，没有放宽身份校验。
- `font_scale.requested=2.0`、`font_scale.observed=2.0`，运行后恢复到 `1.0`。

### 2.3 中间失败与修正

模拟器基线不是一次通过；失败均定位为验收器/场景代码问题，未发现产品离线路径失败：

1. 首次全量：`fs` 在 `APPROVAL_COMMAND` 上调用 `performScrollTo()` 失败。该节点位于 `SelectionContainer`，不适合作滚动锚点。
2. 第二次：`fs` 改为断言 `assertExists()` 仍失败——合成文件审批根本没有 command 字段。改为断言审批状态与可操作的拒绝按钮。
3. 第三次：`off/rec` 通过，但 `i/j/k` 连续报“已有活动回合”。定位为既有 `o` 场景在进程恢复后、写门尚未打开时点击拒绝，点击被忽略而等待条件只看 `activeRun==null`；修复为等待 `writable` 并等待该 run 的终态事件。
4. 第四次：编排器新增“场景开始前活动槽必须为空”的守卫后，`e` 被误拦；`u/y/z` 本来会保留一个等待中的审批给撤销检查。守卫改为只检查会启动/驱动回合的场景。
5. 最终全量 14 场景通过。失败报告保留在 `.dev`，未改写为通过。

## 3. 实际手机：未执行

本轮调用 `adb devices -l` 只有模拟器，没有物理设备；因此没有生成真机报告，也没有伪造真机证据。以下命令是下一轮接入手机后的零模型入口，先 dry-run，再实际运行：

```powershell
$apk = "android/app/build/outputs/apk/debug/app-debug.apk"
$test = "android/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk"
python scripts/accept_device.py --serial <phone-serial> `
  --profile device-baseline `
  --host-address <LAN-or-VPN-address-or-DNS> `
  --apk $apk --test-apk $test --dry-run
```

确认 dry-run 的 `transport.kind`、`transport.bind`、`transport.tls` 与场景集后去掉 `--dry-run`，加 `--host-port <port>`（如需要防火墙规则）和 `--keep --report .dev/<name>.json`。如果使用 DNS 名，必须同时给出本机可达的 `--listen-address`；如果使用 LAN/VPN IP，缺省绑定同一 IP，不会监听全部接口。手机与电脑必须在同一可达网络，Windows 防火墙需要允许该固定端口。

## 4. 非声明

- **不证明真机可连接**：本轮没有物理设备，LAN/VPN 只由离线单元测试与 dry-run 配置覆盖。
- **不是真实断网 30 秒**：`off` 通过停止 Host 制造不可达；Android 侧看到的是连接失败，但没有切换 Wi-Fi/移动数据或飞行模式。AT-09 的 30 秒真实网络切断仍未完成。
- **不证明 200% 字体在所有页面/所有设备可用**：只在模拟器 API 37 上检查了会话、时间线、审批页的关键控件。
- **不降低 TLS 校验**：证书链、主机名、SPKI 固定指纹仍全部启用；本轮没有 `TrustAll`、跳过主机名或明文回退。
- **不代表 AT-07 完成**：`starting` 中断、真实并发完成竞争、RPC 失败、确认丢失和重复中断服务端计数仍未覆盖。
- **没有真实模型回合**：`expected_model_turns=0`，未启动真实 Codex，未产生模型费用。
