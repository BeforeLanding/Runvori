# M0/Q-05：HTTPS 配对回环联动探针

日期：2026-10-24，Asia/Shanghai。此夹具只在 `127.0.0.1` 的临时端口监听，用一次性证书、秘密和 SQLite 数据库验证技术边界；不是可部署的 Host API。

执行 `python scripts/probe_https_pairing.py`。客户端先用测试信任证书完成 TLS 链与 `localhost` 主机名核对，再比较本地测试证书的 SPKI SHA-256 指纹；通过后才向固定路径 `POST /v1/pairings/exchange` 发送 JSON 请求体中的秘密。临时 HTTPS 处理器关闭请求日志、限定路径与请求大小，并调用独立 `PairingJournal`；证书私钥、秘密与数据库均在仓库忽略的 `.dev` 一次性目录，结束后删除。

真实回环观察：错误指纹在 HTTP 请求发出前被拒，服务端请求计数保持零；正确指纹的首次兑换在 SQLite 中提交后，夹具故意丢弃 HTTP 响应；随后相同秘密重放返回 `409 pairing_unavailable`，没有第二个令牌。Host 本地模拟撤销后生成新秘密，再次 HTTPS 兑换成功，新令牌可被持久记录核对。报告只包含状态标签，不输出秘密、指纹或令牌。

在获准的本地执行环境运行 `python scripts/check.py`：16 份契约校验通过；91 项测试运行，89 通过、2 项因本机符号链接创建权限跳过。普通沙箱禁止回环连接时，该真实握手用例明确跳过。之前的单独 [TLS 身份](M0-Q05-tls-identity.md) 和 [SQLite 配对](M0-Q05-pairing-journal.md) 证据仍各自保留。

限制：该 HTTP 处理器只是定向测试夹具，没有来源/全局限流、产品身份与设备代次、持久 Host 生命周期、Android 客户端、正式证书配置、诊断日志审计或生产负载边界。它不能作为远端服务运行；Q-05 和 AT-01 仍未完成。
