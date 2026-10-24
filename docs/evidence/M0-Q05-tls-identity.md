# M0/Q-05：回环 TLS 与 Host 指纹验证探针

日期：2026-10-24，Asia/Shanghai。范围为一次性回环连接和测试证书，不是产品 HTTPS Host、Android 客户端或配对实现。

在 Windows 11、Python 3.12.10、OpenSSL 3.0.16 上运行 `python scripts/probe_tls_identity.py`。脚本在仓库忽略的 `.dev` 一次性目录生成两组不同公钥、相同 `localhost` SAN 的测试证书；私钥不写入 Git，测试后删除。OpenSSL 从证书提取公钥并输出 DER，脚本计算 SPKI SHA-256；指纹来自本地证书文件，不从未知网络端点默认信任。客户端使用 `ssl.create_default_context(cafile=...)`，保持 `CERT_REQUIRED` 与主机名检查；握手后、发送任何应用字节前核对预期指纹。

真实回环握手结果：受信任证书且匹配指纹时发送固定 `probe`；同一地址换成第二把公钥但仍被测试信任链接受时，旧指纹拒绝，服务器没有收到应用字节；错误主机名和不受信任证书在 TLS 验证阶段拒绝，也没有应用字节；显式改用本地第二把公钥的指纹后才通过。输出只记录这些状态，不含密钥或实际指纹。

在获准的本地执行环境运行 `python scripts/check.py`：16 份契约校验通过；86 项测试运行，84 通过、2 项因本机符号链接创建权限跳过。普通沙箱拒绝本地回环连接，因此该 TLS 用例在普通沙箱标记跳过；获准环境中真实握手通过。

此探针只验证“信任链/主机名＋预期 SPKI”这一条技术路径。本探针不含 Android 专用信任、证书到期、配对信息本地展示、一次性秘密、设备令牌、证书轮换操作或产品 Host 监听；Q-05 保持开放。设计边界见 [SECURITY.md](../../SECURITY.md)。Python 客户端验证行为参考 [Python `ssl` 文档](https://docs.python.org/3/library/ssl.html)，公钥输出选项参考 [OpenSSL x509](https://docs.openssl.org/3.3/man1/openssl-x509/) 与 [OpenSSL pkey](https://docs.openssl.org/3.5/man1/openssl-pkey/) 文档。

2026-10-24 后续：已增加独立 SQLite 一次性配对记录切片，见 [配对证据](M0-Q05-pairing-journal.md)；尚未连接本 TLS 探针或产品接口。

2026-10-24 联动补充：定向回环 HTTPS 夹具复用了本探针的证书与指纹核对，再调用独立配对记录，见 [联动证据](M0-Q05-https-pairing.md)；产品接口仍未建立。
