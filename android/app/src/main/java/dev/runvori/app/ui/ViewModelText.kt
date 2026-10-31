package dev.runvori.app.ui

import dev.runvori.app.data.ApprovalView
import dev.runvori.app.data.OperationReceipt
import dev.runvori.app.data.RunArtifact
import dev.runvori.app.domain.PairingInfo
import dev.runvori.app.domain.ProtocolError

// Messages the view model shows for Host error codes and receipts.

internal fun staleApprovalMessage(code: String): String = when (code) {
    "approval_not_found" -> "审批已不存在，Host 可能已重启；按钮已失效"
    "approval_expired" -> "审批已失效，不能再允许或拒绝"
    "resource_changed" -> "审批状态已变化，按钮已失效；请返回刷新"
    else -> "审批不可用，按钮已失效"
}

internal fun resultMessage(code: String): String = when (code) {
    "run_not_found" -> "回合不存在或已被清理"
    "invalid_cursor" -> "游标已失效，请刷新"
    "session_not_found" -> "会话不存在"
    "artifact_not_found" -> "产物不存在或已被清理"
    "artifact_unavailable" -> "产物内容不可用"
    "invalid_artifact_id" -> "产物标识无效"
    "invalid_range" -> "读取范围无效，请刷新"
    else -> "结果读取失败：" + code
}

internal fun artifactMessage(code: String): String = when (code) {
    "artifact_not_found" -> "产物不存在或已被清理"
    "artifact_unavailable" -> "产物内容不可用"
    "invalid_artifact_id" -> "产物标识无效"
    "invalid_range" -> "分块范围无效，请重新打开产物"
    "request_cancelled" -> "读取已取消"
    else -> "产物读取失败：" + code
}

internal fun artifactUnavailableText(artifact: RunArtifact): String =
    when (artifact.reason) {
        "command_pending" -> "命令尚未结束，输出还不可用。"
        "command_incomplete" -> "命令没有完成，输出不完整。"
        "diff_unavailable" -> "差异内容不可用。"
        "unsafe_path" -> "路径不安全或缺失，不能查看内容。"
        "binary_metadata_only" -> "二进制内容仅显示元信息。"
        "untracked_content_not_captured" -> "未跟踪文件只记录路径，未捕获内容。"
        "not_a_git_worktree" -> "工作区不是 Git 仓库。"
        "no_changes" -> "Git 工作区没有检测到改动。"
        "entry_limit" -> "改动文件过多，只记录了部分条目。"
        "artifact_size_limit" -> "内容超过产物上限。"
        else -> "产物内容不可用。"
    }

internal fun decisionNotice(receipt: OperationReceipt): String =
    when (receipt.status) {
        "accepted" -> "Host 已受理，等待发送与处理"
        "dispatching" -> "正在发送，等待 Codex 处理"
        "confirmed" -> "已确认处理"
        "failed" -> "操作未完成，请查看原因"
        "unknown" -> "结果不确定，正在核对"
        else -> "状态：" + receipt.status
    }

internal fun pairingMessage(code: String): String = when (code) {
    "pairing_url_not_https" -> "配对地址必须是 HTTPS"
    "pairing_url_invalid" -> "配对地址格式无效"
    "invalid_pairing_fingerprint" -> "配对信息里的身份指纹无效"
    "invalid_pairing_text" -> "无法解析配对信息，请粘贴电脑输出的完整内容"
    "invalid_pairing_secret" -> "配对秘密无效"
    "pairing_identity_mismatch" -> "Host 返回的身份与配对信息不一致，已拒绝"
    "pairing_unavailable" -> "配对秘密已过期、已使用或已有其他设备"
    "rate_limited" -> "尝试过于频繁，请稍后再试"
    "unauthorized" -> "配对凭据被拒绝"
    "protocol_incompatible" -> "Host 协议版本不兼容"
    else -> "配对失败：" + code
}

internal fun approvalMessage(error: ProtocolError): String = when (error.code) {
    "resource_changed" -> "状态已变化，请刷新"
    "approval_expired" -> "审批已失效"
    "approval_not_found" -> "审批不存在"
    "operation_not_found" -> "操作不存在，可能未被执行"
    "host_stopped" -> "Host 正在停止"
    else -> "操作失败：" + error.code
}

internal fun runMessage(code: String): String = when (code) {
    "active_run_exists" -> "已有活动回合"
    "thread_unavailable" -> "会话线程不可用"
    "execution_unavailable" -> "执行环境不可用"
    "idempotency_conflict" -> "请求标识冲突"
    "payload_too_large" -> "输入过长"
    else -> "提交失败：" + code
}

internal fun interruptMessage(code: String): String = when (code) {
    "resource_changed" -> "回合状态已变化，请刷新"
    "active_run_exists" -> "已有其他活动回合"
    else -> "中断失败：" + code
}

internal fun transportCode(error: Exception, info: PairingInfo): String =
    when (error) {
        is dev.runvori.app.data.PinMismatch -> error.code
        is javax.net.ssl.SSLException -> "tls_handshake_failed"
        is java.net.SocketTimeoutException -> "connection_timeout"
        is java.net.UnknownHostException -> "host_unreachable"
        is java.net.ConnectException -> "host_unreachable"
        else -> error.javaClass.simpleName
    }

internal fun approvalStatusText(view: ApprovalView): String =
    when (view.status) {
        "pending" -> "等待决定"
        "decision_recorded" -> "决定已收到，等待发送"
        "dispatching" -> "等待 Codex 处理"
        "resolved" -> "请求已处理，请查看执行结果"
        "expired" -> "请求已失效"
        "unknown" -> "决定可能已送达，正在核对"
        else -> "状态：" + view.status
    }
