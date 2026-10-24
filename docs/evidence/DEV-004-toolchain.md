# DEV-004：Android 构建基线

日期：2026-10-24，Asia/Shanghai。对应 M0/Q-03。以下证明最小占位工程可编译并在一台模拟器上安装、启动；没有 Host 连接、实机验证或产品验收。

## 固定组合

- Windows 11、本机 JDK/Javac 21.0.12；Android Studio `AI-261.23567.138.2611.15503007`。
- Gradle wrapper 8.13、Android Gradle Plugin 8.13.2、Kotlin 与 Compose Compiler Plugin 2.2.21。
- compileSdk/targetSdk 36、build-tools 36.0.0、minSdk 26；Compose BOM 2025.06.01、Activity Compose 1.11.0。
- Gradle wrapper JAR 的 SHA-256 为 `81a82aaea5abcc8ff68b3dfcb58b3c3c429378efd98e7433460610fecd7ae45f`，与 [Gradle 官方校验表](https://gradle.org/release-checksums/)一致；分发包 SHA-256 `20f1b1176237254a6fc204d8434196fa11a4cfb387567519c61556e8710aed78` 已写入 wrapper 配置。[AGP 8.13 官方兼容表](https://developer.android.com/build/releases/agp-8-13-0-release-notes)列出的最低 Gradle 为 8.13、JDK 为 17，最高 API 为 36.1；[Compose Compiler 官方配置](https://developer.android.com/develop/ui/compose/setup-compose-dependencies-and-compiler)要求 Kotlin 2.0+ 使用同版本的 Compose 编译插件。

## 实际命令与结果

在仓库 `android/` 下，以进程局部 `ANDROID_HOME` 指向已安装 SDK，执行 `gradlew.bat --no-daemon :app:assembleDebug`：**BUILD SUCCESSFUL**，37 项任务中 1 项执行、36 项缓存。之前使用同一固定版本联网补齐缺失的公开依赖，首次完整构建 **BUILD SUCCESSFUL in 52s**；补齐后离线重建也 **BUILD SUCCESSFUL**。`gradlew.bat --version` 报 Gradle 8.13 和 JDK 21.0.12。

产物为被 Git 忽略的 `android/app/build/outputs/apk/debug/app-debug.apk`，大小 10,108,432 字节，SHA-256 `eea2948691f626b3570df0392b28e1f317defee053a9e6789fb0ee709b123127`。SDK build-tools 36.0.0 的 `aapt dump badging` 报包名 `dev.runvori.app`、versionCode 1、versionName 0.0.1、minSdk 26、targetSdk 36。

占位 Activity 只显示“尚未连接 Host”，没有网络或审批入口。`python scripts/check.py` 同时运行 79 项 Host 探针测试（77 通过、2 项因本机链接创建权限跳过），16 份契约校验通过；`git diff --check` 通过。

## 模拟器安装与启动复验

发现已有 `Medium_Phone` AVD 后，使用 Android Emulator 36.6.11.0 以 `-read-only -no-window -no-audio -no-snapshot` 启动临时实例。`adb` 报 `emulator-5554` 为 `device`，`sys.boot_completed=1`，设备为 API 37、x86_64。执行 `adb install -r` 返回 `Success`；`am start -W -n dev.runvori.app/.MainActivity` 返回 `Status: ok`、冷启动 `TotalTime: 2985` 毫秒；`pidof` 返回进程 ID，`dumpsys activity activities` 中 MainActivity 为 `topResumedActivity`。截图显示预期的三个占位文案，最近的过滤日志未见该包崩溃。截图：[DEV-004 模拟器屏幕](DEV-004-emulator.png)，SHA-256 `23c1542f805310fb8382b1d1ea7e3eff5872e114efd98936492e6010cc5239ae`。

新增可重复的 `scripts/smoke_android.ps1`：以进程局部 `ANDROID_HOME` 指向 SDK，执行 `powershell -File scripts/smoke_android.ps1 -Serial emulator-5554`，脚本固定安装当前 Debug APK、启动固定包名的 MainActivity，并核对进程和前台 Activity。实测输出 `PASS serial=emulator-5554 api=37 abi=x86_64 package=dev.runvori.app activity=.MainActivity`（进程 ID 每次不同）。

## 未验证

本机 SDK/Studio 已安装，但最初不在 PATH；首次 `adb devices -l` 返回空列表，之后发现已有 AVD 并完成上述模拟器验证。没有实机、API 26 基准设备或干净环境验证；`minSdk 26` 仍是暂定编译配置。冷启动时间是单次模拟器观测，不是性能基线。未运行界面可访问性、断线恢复或产品功能测试；Host/TLS/配对与 Android 数据层未建立，AT-01～AT-17 均未因此通过。
