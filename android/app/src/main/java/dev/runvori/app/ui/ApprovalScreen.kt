package dev.runvori.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import dev.runvori.app.data.ApprovalView

@Composable
internal fun ApprovalScreen(state: AppState, actions: Actions) {
    val approval = state.approval
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedButton(onClick = actions.closeApproval,
                modifier = Modifier.testTag(Tag.APPROVAL_BACK)) { Text("返回") }
            Spacer(Modifier.width(8.dp))
            Text("审批详情", style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.weight(1f))
            Text("版本 " + (approval?.version ?: 0),
                style = MaterialTheme.typography.labelSmall)
        }
        if (approval == null) {
            if (state.approvalStale) {
                Text(state.approvalStaleReason ?: "审批已失效，不能再提交决定。",
                    modifier = Modifier.testTag(Tag.APPROVAL_STALE),
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodyMedium)
            } else {
                Text("正在读取审批…", modifier = Modifier.testTag(Tag.LOADING))
            }
            return@Column
        }
        ApprovalBody(approval)
        val status = approval.status
        val actionable = state.writable && status == "pending" &&
            state.decisionInFlight == null && !state.approvalStale
        Text("审批状态：" + approvalStatusText(status),
            modifier = Modifier.testTag(Tag.APPROVAL_STATUS),
            style = MaterialTheme.typography.bodyMedium)
        if (state.approvalStale) {
            Text(state.approvalStaleReason ?: "审批已失效，不能再提交决定。",
                modifier = Modifier.testTag(Tag.APPROVAL_STALE),
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodyMedium)
        }
        state.decisionNotice?.let {
            Text(it, modifier = Modifier.testTag(Tag.DECISION_NOTICE),
                style = MaterialTheme.typography.bodyMedium,
                color = if (state.decisionUnresolved)
                    MaterialTheme.colorScheme.error
                else MaterialTheme.colorScheme.primary)
        }
        state.lastDecision?.let {
            Text("你刚才的选择：" +
                (if (it.decision == "allow_once") "允许本次" else "拒绝"),
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.testTag(Tag.LAST_DECISION))
        }
        if (!approval.canAllow) {
            Text(
                "此审批不提供“允许”：" + missingText(approval),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
                modifier = Modifier.testTag(Tag.INSUFFICIENT),
            )
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                onClick = { actions.decide("allow_once") },
                enabled = actionable && approval.canAllow,
                modifier = Modifier.weight(1f).testTag(Tag.ALLOW),
            ) { Text("允许本次") }
            OutlinedButton(
                onClick = { actions.decide("deny") },
                enabled = actionable && approval.canDeny,
                modifier = Modifier.weight(1f).testTag(Tag.DENY),
            ) { Text("拒绝") }
        }
        if (!state.writable) {
            Text("离线或状态未同步完成时不能提交决定。",
                style = MaterialTheme.typography.bodySmall)
        }
        // A user who cannot or will not decide still needs a way out of the
        // waiting turn without leaving the approval page. The confirmation
        // dialog is shared with the timeline action.
        if (state.activeRun != null) {
            OutlinedButton(
                onClick = actions.interrupt,
                enabled = state.writable && !state.interruptInFlight,
                modifier = Modifier.fillMaxWidth().testTag(Tag.INTERRUPT),
            ) {
                Text(if (state.interruptInFlight) "正在中断…" else "中断当前回合")
            }
        }
    }
}

@Composable
private fun ApprovalBody(approval: ApprovalView) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(
                if (approval.kind == "file_change") "文件修改审批" else "命令执行审批",
                fontWeight = FontWeight.SemiBold,
            )
            approval.workspaceName?.let { Text("工作区：$it") }
            approval.reason?.let { Text("原因：$it") }
            approval.command?.let { command ->
                Text("实际命令", style = MaterialTheme.typography.labelMedium)
                SelectionContainer {
                    Text(command, fontFamily = FontFamily.Monospace,
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.testTag(Tag.APPROVAL_COMMAND))
                }
            }
            approval.cwd?.let {
                Text("cwd：" + it, fontFamily = FontFamily.Monospace,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.testTag(Tag.APPROVAL_CWD))
            }
            if (approval.command != null && !approval.cwdInsideWorkspace) {
                Text("cwd 无法证明在登记工作区内，因此不能允许。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error)
            }
            if (approval.commandTruncated) {
                Text("命令文本被截断，不能据此判断完整范围。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error)
            }
            if (approval.changes.isNotEmpty()) {
                HorizontalDivider()
                approval.changes.forEach { change ->
                    Text(
                        buildString {
                            append(change.path ?: "（路径不安全或缺失）")
                            change.changeType?.let { append(" · "); append(it) }
                        },
                        fontFamily = FontFamily.Monospace,
                        style = MaterialTheme.typography.bodySmall,
                    )
                    change.movePath?.let {
                        Text("移动到 " + it, fontFamily = FontFamily.Monospace,
                            style = MaterialTheme.typography.bodySmall)
                    }
                    change.diff?.let { diff ->
                        SelectionContainer {
                            Text(diff, fontFamily = FontFamily.Monospace,
                                style = MaterialTheme.typography.bodySmall)
                        }
                    }
                    if (change.diffTruncated) {
                        Text("差异被截断。",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.error)
                    }
                }
            }
        }
    }
}

private fun missingText(approval: ApprovalView): String =
    when {
        approval.detailsStatus != "complete" -> "详情不完整"
        approval.missingDetails.isNotEmpty() -> approval.missingDetails.joinToString("、")
        approval.reviewTruncated -> "内容被截断"
        else -> "当前状态不接受允许"
    }
