# M0/Q-05：一次性配对记录验证切片

日期：2026-10-24，Asia/Shanghai。范围为独立 SQLite 行为验证，不是已开放的 HTTPS 配对接口。

新增 `PairingJournal`：仅可信 Host 本地调用方可生成或撤销配对。使用 32 字节 `secrets.token_bytes` 随机秘密，固定有效期 300 秒；SQLite 仅保存 SHA-256 摘要和到期时间。再次本地生成会替换旧的未消费秘密，已有有效设备时拒绝生成。兑换在 `BEGIN IMMEDIATE` 事务中检查摘要、到期、消费状态和设备槽，原子标记已消费并登记唯一有效设备；返回 32 字节随机设备令牌，库内只保存其摘要，核对使用 `hmac.compare_digest`。

行为测试覆盖：提交成功后模拟响应丢失并重开数据库，旧秘密无法重放；到期边界拒绝；重新本地生成使旧秘密失效；两个连接同时兑换只有一个成功；本地撤销使旧令牌失效，之后才能登记新设备。令牌和秘密只存在一次性 `.dev` 测试夹具及进程内，不写入文档或日志。

在获准的本地执行环境运行 `python scripts/check.py`：16 份契约校验通过；90 项测试运行，88 通过、2 项因本机符号链接创建权限跳过。`scripts/smoke_package.ps1` 通过，确认新模块可从当前 wheel 的隔离安装中导入。

限制：没有 HTTP/API、来源与全局限流、TLS 身份绑定、Host 本地展示、Android 凭据存储或设备代次集成；系统时钟回拨和崩溃前未提交事务的客户端未知结果仍需产品级处理。客户端在交换结果不明时不得自动重试，应返回电脑本地核对/撤销后重新配对。Q-05 与 AT-01 仍开放。

2026-10-24 后续：已用仅限本机回环的测试 HTTPS 处理器调用本切片，覆盖指纹门控与实际丢弃交换响应，见 [联动证据](M0-Q05-https-pairing.md)；仍无产品网络入口。

随机数与比较函数参考 [Python `secrets`](https://docs.python.org/3.12/library/secrets.html)、[Python `hmac`](https://docs.python.org/3.12/library/hmac.html)；显式 SQLite 事务控制参考 [Python `sqlite3`](https://docs.python.org/3.12/library/sqlite3.html)。
