package dev.runvori.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import dev.runvori.app.domain.TimelineItem

@Composable
internal fun TimelineScreen(state: AppState, actions: Actions) {
    Column(Modifier.fillMaxSize().padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedButton(onClick = actions.backToSessions,
                modifier = Modifier.testTag(Tag.BACK)) { Text("返回") }
            Spacer(Modifier.width(8.dp))
            Text(
                buildString {
                    append(runStatusText(state.activeRun?.status))
                    state.workspaceName?.let { append(" · "); append(it) }
                },
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.weight(1f).testTag(Tag.RUN_STATUS),
            )
        }
        if (state.syncGap) {
            Banner("事件同步存在缺口，写操作已暂停，正在重新同步。",
                isError = true) { actions.dismissError() }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = actions.loadEarlier,
                modifier = Modifier.testTag(Tag.LOAD_EARLIER)) {
                Text("加载更早")
            }
            if (state.activeRun != null || state.timeline.any { it.runId != null }) {
                OutlinedButton(onClick = actions.openResults,
                    modifier = Modifier.testTag(Tag.OPEN_RESULTS)) {
                    Text("结果")
                }
            }
            if (state.activeRun != null) {
                Button(
                    onClick = actions.interrupt,
                    enabled = state.writable && !state.interruptInFlight,
                    modifier = Modifier.testTag(Tag.INTERRUPT),
                ) {
                    Text(if (state.interruptInFlight) "正在中断…" else "中断当前回合")
                }
            }
        }
        LazyColumn(
            Modifier.weight(1f).fillMaxWidth().testTag(Tag.TIMELINE),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            items(state.timeline, key = { it.key }) { item ->
                TimelineRow(item, onApproval = actions.openApproval)
            }
        }
        if (state.activeRun == null && state.writable) {
            OutlinedTextField(
                value = state.composerText,
                onValueChange = actions.updateComposer,
                label = { Text("继续此会话（发送新的回合）") },
                modifier = Modifier.fillMaxWidth().testTag(Tag.COMPOSER),
                minLines = 2,
            )
            Button(
                onClick = { actions.startRun(state.composerText) },
                enabled = state.composerText.isNotBlank() && !state.startInFlight,
                modifier = Modifier.fillMaxWidth().testTag(Tag.SEND_RUN),
            ) { Text(if (state.startInFlight) "提交中…" else "发送新回合") }
        } else if (state.activeRun != null) {
            Text("活动回合进行中；不提供中途追加输入。",
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.testTag(Tag.ACTIVE_NOTICE))
        }
    }
}

@Composable
private fun TimelineRow(item: TimelineItem, onApproval: (String) -> Unit) {
    when (item) {
        is TimelineItem.UserMessage -> Card(
            Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(
                containerColor = MaterialTheme.colorScheme.surfaceVariant)) {
            Column(Modifier.padding(12.dp)) {
                Text("你", style = MaterialTheme.typography.labelMedium)
                SelectionContainer { Text(item.text) }
            }
        }
        is TimelineItem.AgentMessage -> Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(12.dp)) {
                Text("Agent", style = MaterialTheme.typography.labelMedium)
                SelectionContainer {
                    Text(
                        item.text.ifEmpty { if (item.complete) "（无文本）" else "…" },
                    )
                }
                if (item.textOmitted) {
                    Text("部分输出超出记录上限，未保存。",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.error)
                }
            }
        }
        is TimelineItem.Command -> Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(12.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text("命令", style = MaterialTheme.typography.labelMedium)
                // The exact command string is shown; on Windows it may include
                // a shell wrapper, and simplifying it would misstate what ran.
                SelectionContainer {
                    Text(item.command ?: "（命令内容不可用）",
                        fontFamily = FontFamily.Monospace,
                        style = MaterialTheme.typography.bodySmall)
                }
                item.cwd?.let {
                    Text("cwd: " + it, fontFamily = FontFamily.Monospace,
                        style = MaterialTheme.typography.labelSmall)
                }
                Text(
                    buildString {
                        append("状态：")
                        append(item.status ?: "未知")
                        item.exitCode?.let { append(" · 退出码 "); append(it) }
                        item.durationMs?.let { append(" · "); append(it); append(" ms") }
                    },
                    style = MaterialTheme.typography.labelSmall,
                    modifier = Modifier.testTag(Tag.commandStatus(item.key)),
                )
                if (item.commandTruncated) {
                    Text("命令文本被截断，见审批详情。",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.error)
                }
                if (item.output.isNotEmpty()) {
                    SelectionContainer {
                        Text(item.output, fontFamily = FontFamily.Monospace,
                            style = MaterialTheme.typography.bodySmall)
                    }
                } else if (item.outputOmitted) {
                    Text("输出未保存（超出记录上限或未收到）。",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.error)
                }
            }
        }
        is TimelineItem.Approval -> Card(
            onClick = { onApproval(item.approvalId) },
            modifier = Modifier.fillMaxWidth().testTag(
                if (item.status == "pending") Tag.pendingApproval(item.approvalId)
                else Tag.approvalItem(item.approvalId)),
            colors = CardDefaults.cardColors(
                containerColor = MaterialTheme.colorScheme.tertiaryContainer),
        ) {
            Column(Modifier.padding(12.dp),
                verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text("审批请求", style = MaterialTheme.typography.labelMedium)
                Text(
                    (if (item.kind == "file_change") "文件修改" else "命令执行") +
                        " · " + approvalStatusText(item.status),
                    style = MaterialTheme.typography.bodyMedium,
                )
                if (item.detailsStatus == "insufficient") {
                    Text("详情不足，只能拒绝",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.error)
                }
                Text("查看详情", style = MaterialTheme.typography.labelMedium,
                    fontWeight = FontWeight.SemiBold)
            }
        }
        is TimelineItem.RunEvent -> {
            Text(runEventText(item.type, item.reason),
                style = MaterialTheme.typography.labelMedium,
                modifier = Modifier.fillMaxWidth().padding(4.dp))
        }
        is TimelineItem.Notice -> Text(item.text,
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.error,
            modifier = Modifier.fillMaxWidth().padding(4.dp))
    }
}
