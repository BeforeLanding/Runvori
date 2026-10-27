# Codex 实验兼容基线

生成工具版本以 manifest.json 为准：`codex-cli 0.164.0-alpha.11.1`。

当前提交 14 份由该 CLI 生成的关键 JSON schema（含自身 definitions），覆盖初始化、thread/turn 请求与主要审批消息。它们是上游契约快照，不是 Runvori API，也不代表对应能力已执行验收。完整生成目录位于被忽略的 `.dev` 临时环境。

- `python scripts/runvori.py verify-contracts` 校验所选文件集合、版本及 SHA-256。
- `python scripts/runvori.py export-schema` 首次导出；已有基线时拒绝覆盖。
- 需要更换基线时先核对 CLI 版本、更新源码版本常量与测试；使用 `--replace` 显式重新导出，评审字段差异后才更新兼容记录。

哈希仅检测意外变动，不是上游签名或安全证明。合成 peer 在 host/tests 中，真实执行夹具尚未取得。不得提交真实 HOME、认证、未脱敏会话或完整 stderr。
