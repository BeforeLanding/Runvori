package dev.runvori.app.ui

import dev.runvori.app.data.RunArtifact

// User-facing wording for protocol states; kept literal to the protocol.

internal fun verificationSummaryText(status: String): String = when (status) {
    "passed" -> "通过"
    "failed" -> "失败"
    "unknown" -> "证据不足，无法判定"
    "not_run" -> "未运行"
    "pending" -> "等待执行"
    "running" -> "执行中"
    else -> "未知状态：" + status
}

internal fun verificationItemStatusText(status: String): String =
    verificationSummaryText(status)

internal fun exitCodeText(exitCode: Int?, present: Boolean): String =
    if (!present || exitCode == null) "退出码缺失"
    else "退出码 " + exitCode

internal fun outputStateText(state: String, dropped: Long): String = when (state) {
    "complete" -> "输出完整"
    "truncated" -> "输出被截断（丢弃 " + dropped + " 字节）"
    "empty" -> "无输出"
    "pending" -> "等待输出"
    else -> "输出状态未知"
}

internal fun artifactKindText(kind: String): String = when (kind) {
    "command_output" -> "命令输出"
    "file_diff" -> "文件差异"
    else -> "产物：" + kind
}

internal fun artifactSourceText(source: String): String = when (source) {
    "command_execution" -> "命令执行"
    "approval_proposal" -> "审批提案"
    "upstream_report" -> "上游报告"
    "git_worktree_snapshot" -> "Git 工作区快照"
    else -> "来源：" + source
}

internal fun artifactOperationText(operation: String): String = when (operation) {
    "add" -> "新增"
    "delete" -> "删除"
    "update" -> "修改"
    "move" -> "移动"
    "copy" -> "复制"
    "type_change" -> "类型变化"
    else -> operation
}

internal fun artifactAttributionText(attribution: String): String = when (attribution) {
    "proposed" -> "审批提案，尚未归因于执行结果"
    "reported" -> "上游执行项报告"
    "unknown" -> "未知，可能包含回合前已有改动"
    "not_applicable" -> "不适用"
    else -> attribution
}

internal fun artifactReasonText(reason: String): String = when (reason) {
    "command_pending" -> "命令尚未结束"
    "command_incomplete" -> "命令没有完成"
    "output_complete" -> "输出完整"
    "output_truncated" -> "输出被预算截断"
    "output_empty" -> "命令没有输出"
    "diff_unavailable" -> "差异内容不可用"
    "diff_truncated" -> "差异被截断"
    "unsafe_path" -> "路径不安全或缺失"
    "binary_metadata_only" -> "二进制仅保留元信息"
    "untracked_content_not_captured" -> "未跟踪文件未捕获内容"
    "not_a_git_worktree" -> "不是 Git 工作区"
    "no_changes" -> "没有检测到改动"
    "entry_limit" -> "改动条目超过上限"
    "artifact_size_limit" -> "内容超过产物上限"
    else -> reason
}

internal fun artifactMetaText(artifact: RunArtifact): String =
    buildString {
        append(artifact.sizeBytes).append(" 字节")
        if (artifact.truncated) append(" · 已截断")
        if (artifact.droppedBytes > 0) append(" · 丢弃 ").append(artifact.droppedBytes)
        if (artifact.representation == "binary") append(" · 二进制")
        if (!artifact.contentAvailable) append(" · 内容不可用")
    }

internal fun verificationReasonText(reason: String): String = when (reason) {
    "exit_zero" -> "退出码为 0"
    "nonzero_exit" -> "退出码非 0"
    "command_failed" -> "命令项失败"
    "missing_or_ambiguous_evidence" -> "缺少或无法唯一匹配证据"
    "no_execution_evidence" -> "没有执行证据"
    "executing" -> "正在执行"
    "awaiting_execution" -> "等待执行"
    else -> reason
}

fun runStatusText(status: String?): String = when (status) {
    "starting" -> "正在启动"
    "running" -> "执行中"
    "waiting_approval" -> "等待审批"
    "interrupting" -> "正在中断"
    "completed" -> "回合已完成"
    "failed" -> "回合失败"
    "interrupted" -> "回合已中断"
    null -> "无活动回合"
    else -> "状态：" + status
}

internal fun runEventText(type: String, reason: String?): String = when (type) {
    "run.created" -> "新回合已受理"
    "run.started" -> "回合开始执行"
    "run.completed" -> "回合已完成"
    "run.failed" -> "回合失败"
    "run.interrupted" -> "回合已中断" + (reason?.let { "（$it）" } ?: "")
    else -> type
}

internal fun approvalStatusText(status: String): String = when (status) {
    "pending" -> "等待决定"
    "decision_recorded" -> "决定已收到，等待发送"
    "dispatching" -> "等待 Codex 处理"
    "resolved" -> "请求已处理，请查看执行结果"
    "expired" -> "请求已失效"
    "unknown" -> "决定可能已送达，正在核对"
    else -> "状态：" + status
}
