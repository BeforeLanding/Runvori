package dev.runvori.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import dev.runvori.app.data.ApprovalView
import dev.runvori.app.data.RunArtifact
import dev.runvori.app.data.RunCommand
import dev.runvori.app.data.RunResults
import dev.runvori.app.data.VerificationItem
import dev.runvori.app.data.VerificationView
import dev.runvori.app.domain.TimelineItem

/**
 * The four screens of the first mobile loop.
 *
 * Copy follows the protocol rather than optimising for reassurance: a `202` is
 * "accepted", an unresolved decision is "result unconfirmed", and an approval
 * whose details are incomplete can only be denied. Every button carries a text
 * label and a stable test tag; colour is never the only signal.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RunvoriApp(state: AppState, actions: Actions) {
    Scaffold(
        topBar = {
            TopAppBar(title = {
                Column {
                    Text("Runvori", style = MaterialTheme.typography.titleLarge)
                    Text(state.connection,
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.testTag(Tag.CONNECTION))
                }
            })
        },
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            state.error?.let { message ->
                Banner(message, isError = true) { actions.dismissError() }
            }
            if (state.notice != null) {
                Banner(state.notice, isError = false) { actions.dismissError() }
            }
            when (state.screen) {
                Screen.Pairing -> PairingScreen(state, actions)
                Screen.Sessions -> SessionsScreen(state, actions)
                Screen.Timeline -> TimelineScreen(state, actions)
                Screen.Approval -> ApprovalScreen(state, actions)
                Screen.Results -> ResultsScreen(state, actions)
                Screen.Artifact -> ArtifactScreen(state, actions)
            }
        }
    }
    if (state.interruptConfirm) {
        AlertDialog(
            onDismissRequest = actions.cancelInterrupt,
            title = { Text("中断当前回合？") },
            text = {
                Text("停止当前回合；已产生的文件改动保留，不会回滚。")
            },
            confirmButton = {
                TextButton(onClick = actions.confirmInterrupt,
                    modifier = Modifier.testTag(Tag.CONFIRM_INTERRUPT)) {
                    Text("停止当前回合")
                }
            },
            dismissButton = {
                TextButton(onClick = actions.cancelInterrupt) { Text("取消") }
            })
    }
}

@Composable
private fun Banner(message: String, isError: Boolean, onDismiss: () -> Unit) {
    Row(
        Modifier.fillMaxWidth()
            .background(
                if (isError) MaterialTheme.colorScheme.errorContainer
                else MaterialTheme.colorScheme.secondaryContainer)
            .padding(horizontal = 12.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = message,
            modifier = Modifier.weight(1f).testTag(
                if (isError) Tag.ERROR else Tag.NOTICE),
            style = MaterialTheme.typography.bodyMedium,
            color = if (isError) MaterialTheme.colorScheme.onErrorContainer
            else MaterialTheme.colorScheme.onSecondaryContainer,
        )
        TextButton(onClick = onDismiss) { Text("知道了") }
    }
}

@Composable
private fun PairingScreen(state: AppState, actions: Actions) {
    var text by remember { mutableStateOf("") }
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState().let {
            rememberScrollState()
        }).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("在电脑上运行 host 管理入口的 pairing-info，把输出粘贴到这里。",
            style = MaterialTheme.typography.bodyMedium)
        Text("配对前请核对身份指纹与电脑屏幕一致；指纹不符时不会继续。",
            style = MaterialTheme.typography.bodySmall)
        OutlinedTextField(
            value = text,
            onValueChange = { text = it },
            label = { Text("配对信息") },
            modifier = Modifier.fillMaxWidth().height(180.dp)
                .testTag(Tag.PAIRING_INPUT),
        )
        Button(
            onClick = { actions.beginPairing(text) },
            modifier = Modifier.fillMaxWidth().testTag(Tag.PAIRING_PARSE),
        ) { Text("解析并核对") }
        state.pairingError?.let {
            Text(it, color = MaterialTheme.colorScheme.error,
                modifier = Modifier.testTag(Tag.PAIRING_ERROR),
                style = MaterialTheme.typography.bodyMedium)
        }
        state.pairingReview?.let { info ->
            Card(Modifier.fillMaxWidth().testTag(Tag.PAIRING_REVIEW)) {
                Column(Modifier.padding(12.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text("Host：" + info.hostName, fontWeight = FontWeight.SemiBold)
                    Text("地址：" + info.httpsUrl,
                        fontFamily = FontFamily.Monospace,
                        style = MaterialTheme.typography.bodySmall)
                    Text("身份指纹：" + info.spkiSha256,
                        fontFamily = FontFamily.Monospace,
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.testTag(Tag.PAIRING_PIN))
                    Text("请与电脑屏幕上的指纹逐字核对。",
                        style = MaterialTheme.typography.bodySmall)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(
                            onClick = actions.confirmPairing,
                            enabled = !state.busy,
                            modifier = Modifier.testTag(Tag.PAIRING_CONFIRM),
                        ) { Text("指纹一致，配对") }
                        OutlinedButton(onClick = actions.cancelPairing) {
                            Text("取消")
                        }
                    }
                }
            }
        }
        if (state.busy) {
            Text("正在配对…", modifier = Modifier.testTag(Tag.BUSY))
        }
    }
}

@Composable
private fun SessionsScreen(state: AppState, actions: Actions) {
    Column(Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("会话", style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.weight(1f))
            OutlinedButton(onClick = actions.refreshSessions,
                modifier = Modifier.testTag(Tag.REFRESH_SESSIONS)) {
                Text("刷新")
            }
        }
        Text(state.hostName.ifBlank { "Runvori Host" },
            style = MaterialTheme.typography.bodySmall)
        if (state.sessions.isEmpty() && state.loading) {
            Text("正在加载…", modifier = Modifier.testTag(Tag.LOADING))
        } else if (state.sessions.isEmpty()) {
            Text("请在电脑上通过 Runvori 启动首个会话。",
                modifier = Modifier.testTag(Tag.EMPTY_SESSIONS))
        }
        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(state.sessions, key = { it.sessionId }) { row ->
                Card(
                    onClick = { actions.openSession(row.sessionId) },
                    modifier = Modifier.fillMaxWidth()
                        .testTag(Tag.sessionRow(row.sessionId)),
                ) {
                    Column(Modifier.padding(12.dp),
                        verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text(row.title, fontWeight = FontWeight.SemiBold)
                        Text(row.workspaceName ?: "未命名工作区",
                            style = MaterialTheme.typography.bodySmall)
                        Text(
                            buildString {
                                append(runStatusText(row.status))
                                if (row.pendingApprovals > 0) {
                                    append(" · 待审批 ")
                                    append(row.pendingApprovals)
                                }
                                if (row.active) append(" · 活动回合")
                            },
                            style = MaterialTheme.typography.bodySmall,
                            modifier = Modifier.testTag(
                                Tag.sessionStatus(row.sessionId)),
                        )
                        row.updatedAt?.let {
                            Text("更新于 " + it, style = MaterialTheme.typography.labelSmall)
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun TimelineScreen(state: AppState, actions: Actions) {
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
            modifier = Modifier.fillMaxWidth(),
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

@Composable
private fun ApprovalScreen(state: AppState, actions: Actions) {
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
            Text("正在读取审批…", modifier = Modifier.testTag(Tag.LOADING))
            return@Column
        }
        ApprovalBody(approval)
        val status = approval.status
        val actionable = state.writable && status == "pending" &&
            state.decisionInFlight == null
        Text("审批状态：" + approvalStatusText(status),
            modifier = Modifier.testTag(Tag.APPROVAL_STATUS),
            style = MaterialTheme.typography.bodyMedium)
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

@Composable
private fun ResultsScreen(state: AppState, actions: Actions) {
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedButton(onClick = actions.closeResults,
                modifier = Modifier.testTag(Tag.RESULTS_BACK)) { Text("返回") }
            Spacer(Modifier.width(8.dp))
            Text("回合结果", style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.weight(1f))
            OutlinedButton(onClick = actions.refreshResults,
                modifier = Modifier.testTag(Tag.REFRESH_RESULTS)) { Text("刷新") }
        }
        if (state.resultsLoading && state.results == null) {
            Text("正在读取结果…", modifier = Modifier.testTag(Tag.LOADING))
            return@Column
        }
        val results = state.results
        if (results == null) {
            Text("结果不可用，请刷新后重试。",
                modifier = Modifier.testTag(Tag.RESULT_UNAVAILABLE),
                color = MaterialTheme.colorScheme.error)
            return@Column
        }
        RunStatusCard(results)
        VerificationCard(results.verification)
        Text("命令（${results.commandCount}）",
            style = MaterialTheme.typography.titleSmall)
        if (state.commands.isEmpty()) {
            Text("没有命令记录。", style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.testTag(Tag.RESULT_COMMANDS_EMPTY))
        } else {
            state.commands.forEach { command ->
                CommandResultCard(command)
            }
        }
        if (state.commandsHasMore) {
            OutlinedButton(
                onClick = actions.loadMoreCommands,
                enabled = !state.commandsLoading,
                modifier = Modifier.fillMaxWidth()
                    .testTag(Tag.LOAD_MORE_COMMANDS),
            ) {
                Text(if (state.commandsLoading) "正在加载…" else "加载更多命令")
            }
        }
        Text("产物（${results.artifactCount}）",
            style = MaterialTheme.typography.titleSmall)
        if (state.artifacts.isEmpty()) {
            Text("没有产物记录。", style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.testTag(Tag.RESULT_ARTIFACTS_EMPTY))
        } else {
            state.artifacts.forEach { artifact ->
                ArtifactCard(artifact, actions)
            }
        }
        if (state.artifactsHasMore) {
            OutlinedButton(
                onClick = actions.loadMoreArtifacts,
                enabled = !state.artifactsLoading,
                modifier = Modifier.fillMaxWidth()
                    .testTag(Tag.LOAD_MORE_ARTIFACTS),
            ) {
                Text(if (state.artifactsLoading) "正在加载…" else "加载更多产物")
            }
        }
    }
}

@Composable
private fun ArtifactCard(artifact: RunArtifact, actions: Actions) {
    Card(Modifier.fillMaxWidth().testTag(Tag.artifactItem(artifact.artifactId))) {
        Column(Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(artifactKindText(artifact.kind) + " · " +
                artifactSourceText(artifact.source),
                fontWeight = FontWeight.SemiBold)
            artifact.path?.let {
                SelectionContainer {
                    Text(it, fontFamily = FontFamily.Monospace,
                        style = MaterialTheme.typography.bodySmall)
                }
            }
            artifact.movePath?.let {
                Text("移动到 " + it, fontFamily = FontFamily.Monospace,
                    style = MaterialTheme.typography.labelSmall)
            }
            artifact.operation?.let {
                Text("操作：" + artifactOperationText(it),
                    style = MaterialTheme.typography.labelSmall)
            }
            Text(artifactMetaText(artifact),
                style = MaterialTheme.typography.labelSmall,
                modifier = Modifier.testTag(Tag.artifactMetadata(artifact.artifactId)))
            if (artifact.source == "git_worktree_snapshot") {
                Text("来源为当前 Git 工作区快照，可能包含回合前已有改动；归因未知。",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.error)
            }
            artifact.reason?.let {
                Text("状态：" + artifactReasonText(it),
                    style = MaterialTheme.typography.labelSmall)
            }
            if (artifact.contentAvailable) {
                OutlinedButton(
                    onClick = { actions.openArtifact(artifact) },
                    modifier = Modifier.testTag(Tag.artifactOpen(artifact.artifactId)),
                ) {
                    Text(if (artifact.kind == "command_output") "查看输出"
                    else "查看差异")
                }
            } else {
                Text("内容不可用" +
                    (artifact.reason?.let { "：" + artifactReasonText(it) } ?: "。"),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.error,
                    modifier = Modifier.testTag(Tag.artifactUnavailable(
                        artifact.artifactId)))
            }
        }
    }
}

@Composable
private fun ArtifactScreen(state: AppState, actions: Actions) {
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedButton(onClick = actions.closeArtifact,
                modifier = Modifier.testTag(Tag.ARTIFACT_BACK)) { Text("返回结果") }
            Spacer(Modifier.width(8.dp))
            Text("产物详情", style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.weight(1f))
        }
        val artifact = state.artifact
        if (artifact == null) {
            Text("产物不可用。", modifier = Modifier.testTag(Tag.ARTIFACT_UNAVAILABLE),
                color = MaterialTheme.colorScheme.error)
            return@Column
        }
        Card(Modifier.fillMaxWidth().testTag(Tag.ARTIFACT_METADATA)) {
            Column(Modifier.padding(12.dp),
                verticalArrangement = Arrangement.spacedBy(3.dp)) {
                Text(artifactKindText(artifact.kind) + " · " +
                    artifactSourceText(artifact.source),
                    fontWeight = FontWeight.SemiBold)
                artifact.path?.let {
                    SelectionContainer {
                        Text(it, fontFamily = FontFamily.Monospace,
                            style = MaterialTheme.typography.bodySmall)
                    }
                }
                artifact.operation?.let {
                    Text("操作：" + artifactOperationText(it),
                        style = MaterialTheme.typography.labelSmall)
                }
                Text("归因：" + artifactAttributionText(artifact.attribution),
                    style = MaterialTheme.typography.labelSmall)
                Text("大小：" + artifact.sizeBytes + " 字节" +
                    " · 哈希：" + (artifact.sha256 ?: "不可用"),
                    style = MaterialTheme.typography.labelSmall)
                if (artifact.truncated) {
                    Text("内容被截断" +
                        (if (artifact.droppedBytes > 0)
                            "（丢弃 ${artifact.droppedBytes} 字节）" else ""),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.error,
                        modifier = Modifier.testTag(Tag.ARTIFACT_TRUNCATED))
                }
            }
        }
        val error = state.artifactError
        if (error != null) {
            Text(error, color = MaterialTheme.colorScheme.error,
                modifier = Modifier.testTag(Tag.ARTIFACT_UNAVAILABLE))
        }
        if (!artifact.contentAvailable) {
            if (error == null) {
                Text("内容不可用。", color = MaterialTheme.colorScheme.error,
                    modifier = Modifier.testTag(Tag.ARTIFACT_UNAVAILABLE))
            }
            return@Column
        }
        if (artifact.representation == "binary") {
            Text("二进制内容仅显示元信息。",
                modifier = Modifier.testTag(Tag.ARTIFACT_BINARY))
            return@Column
        }
        Text("内容（${state.artifactLoadedBytes} / ${artifact.sizeBytes} 字节）",
            style = MaterialTheme.typography.titleSmall)
        if (artifact.sizeBytes == 0L) {
            Text("无内容。", modifier = Modifier.testTag(Tag.ARTIFACT_EMPTY))
        } else {
            SelectionContainer {
                Text(state.artifactText, fontFamily = FontFamily.Monospace,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.testTag(Tag.ARTIFACT_CONTENT))
            }
        }
        if (state.artifactLoading) {
            Text("正在读取…", modifier = Modifier.testTag(Tag.LOADING))
        }
        val next = state.artifactNextOffset
        if (next != null && next < artifact.sizeBytes) {
            OutlinedButton(
                onClick = actions.loadMoreArtifact,
                enabled = !state.artifactLoading,
                modifier = Modifier.fillMaxWidth()
                    .testTag(Tag.LOAD_MORE_ARTIFACT_CONTENT),
            ) {
                Text(if (state.artifactLoading) "正在加载…" else "加载更多内容")
            }
        }
    }
}

@Composable
private fun RunStatusCard(results: RunResults) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("回合状态", style = MaterialTheme.typography.labelMedium)
            Text(runStatusText(results.status),
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.testTag(Tag.RESULT_RUN_STATUS))
            Text(if (results.terminal) "已结束" else "尚未结束",
                style = MaterialTheme.typography.labelSmall,
                modifier = Modifier.testTag(Tag.RESULT_RUN_TERMINAL))
            results.createdAt?.let {
                Text("受理于 " + it, style = MaterialTheme.typography.labelSmall)
            }
            results.terminalAt?.let {
                Text("结束于 " + it, style = MaterialTheme.typography.labelSmall)
            }
        }
    }
}

@Composable
private fun VerificationCard(verification: VerificationView) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("验证结论", style = MaterialTheme.typography.labelMedium)
            Text(
                if (!verification.configured) "未配置必需验证项"
                else verificationSummaryText(verification.summary),
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.testTag(Tag.RESULT_VERIFICATION_SUMMARY))
            if (!verification.configured) {
                Text("工作区没有声明必需验证项，这不是“全部通过”。",
                    style = MaterialTheme.typography.bodySmall)
            } else if (verification.items.isEmpty()) {
                Text("已配置验证项，但结果中没有证据记录。",
                    style = MaterialTheme.typography.bodySmall)
            }
            verification.items.forEach { item ->
                VerificationItemRow(item)
            }
        }
    }
}

@Composable
private fun VerificationItemRow(item: VerificationItem) {
    Column(
        Modifier.fillMaxWidth().testTag(Tag.verificationItem(item.key)),
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        HorizontalDivider()
        Text(
            item.key + " · " + verificationItemStatusText(item.status),
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.testTag(Tag.verificationStatus(item.key)),
        )
        item.command?.let {
            SelectionContainer {
                Text(it, fontFamily = FontFamily.Monospace,
                    style = MaterialTheme.typography.bodySmall)
            }
        }
        if (item.commandTruncated) {
            Text("命令文本被截断，不能作为完整证据。",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.error)
        }
        Text(
            exitCodeText(item.exitCode, item.exitCodePresent),
            style = MaterialTheme.typography.labelSmall,
            modifier = Modifier.testTag(Tag.verificationExit(item.key)),
        )
        Text(
            outputStateText(item.outputState, item.outputDroppedBytes),
            style = MaterialTheme.typography.labelSmall,
            modifier = Modifier.testTag(Tag.verificationOutput(item.key)),
        )
        item.startedAt?.let {
            Text("开始 " + it, style = MaterialTheme.typography.labelSmall)
        }
        item.completedAt?.let {
            Text("结束 " + it, style = MaterialTheme.typography.labelSmall)
        }
        item.reason?.let {
            Text("判定：" + verificationReasonText(it),
                style = MaterialTheme.typography.labelSmall)
        }
    }
}

@Composable
private fun CommandResultCard(command: RunCommand) {
    Card(Modifier.fillMaxWidth().testTag(Tag.runCommand(command.itemId))) {
        Column(Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(3.dp)) {
            SelectionContainer {
                Text(command.command ?: "（命令内容不可用）",
                    fontFamily = FontFamily.Monospace,
                    style = MaterialTheme.typography.bodySmall)
            }
            if (command.commandTruncated) {
                Text("命令文本被截断。",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.error)
            }
            command.cwd?.let {
                Text("cwd: " + it, fontFamily = FontFamily.Monospace,
                    style = MaterialTheme.typography.labelSmall)
            }
            Text("状态：" + (command.status ?: "未知") +
                " · " + exitCodeText(command.exitCode, command.exitCodePresent),
                style = MaterialTheme.typography.labelSmall)
            Text(outputStateText(command.outputState,
                command.outputDroppedBytes),
                style = MaterialTheme.typography.labelSmall,
                modifier = Modifier.testTag(Tag.commandOutput(command.itemId)))
            command.startedAt?.let {
                Text("开始 " + it, style = MaterialTheme.typography.labelSmall)
            }
            command.completedAt?.let {
                Text("结束 " + it, style = MaterialTheme.typography.labelSmall)
            }
        }
    }
}

private fun verificationSummaryText(status: String): String = when (status) {
    "passed" -> "通过"
    "failed" -> "失败"
    "unknown" -> "证据不足，无法判定"
    "not_run" -> "未运行"
    "pending" -> "等待执行"
    "running" -> "执行中"
    else -> "未知状态：" + status
}

private fun verificationItemStatusText(status: String): String =
    verificationSummaryText(status)

private fun exitCodeText(exitCode: Int?, present: Boolean): String =
    if (!present || exitCode == null) "退出码缺失"
    else "退出码 " + exitCode

private fun outputStateText(state: String, dropped: Long): String = when (state) {
    "complete" -> "输出完整"
    "truncated" -> "输出被截断（丢弃 " + dropped + " 字节）"
    "empty" -> "无输出"
    "pending" -> "等待输出"
    else -> "输出状态未知"
}

private fun artifactKindText(kind: String): String = when (kind) {
    "command_output" -> "命令输出"
    "file_diff" -> "文件差异"
    else -> "产物：" + kind
}

private fun artifactSourceText(source: String): String = when (source) {
    "command_execution" -> "命令执行"
    "approval_proposal" -> "审批提案"
    "upstream_report" -> "上游报告"
    "git_worktree_snapshot" -> "Git 工作区快照"
    else -> "来源：" + source
}

private fun artifactOperationText(operation: String): String = when (operation) {
    "add" -> "新增"
    "delete" -> "删除"
    "update" -> "修改"
    "move" -> "移动"
    "copy" -> "复制"
    "type_change" -> "类型变化"
    else -> operation
}

private fun artifactAttributionText(attribution: String): String = when (attribution) {
    "proposed" -> "审批提案，尚未归因于执行结果"
    "reported" -> "上游执行项报告"
    "unknown" -> "未知，可能包含回合前已有改动"
    "not_applicable" -> "不适用"
    else -> attribution
}

private fun artifactReasonText(reason: String): String = when (reason) {
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

private fun artifactMetaText(artifact: RunArtifact): String =
    buildString {
        append(artifact.sizeBytes).append(" 字节")
        if (artifact.truncated) append(" · 已截断")
        if (artifact.droppedBytes > 0) append(" · 丢弃 ").append(artifact.droppedBytes)
        if (artifact.representation == "binary") append(" · 二进制")
        if (!artifact.contentAvailable) append(" · 内容不可用")
    }

private fun verificationReasonText(reason: String): String = when (reason) {
    "exit_zero" -> "退出码为 0"
    "nonzero_exit" -> "退出码非 0"
    "command_failed" -> "命令项失败"
    "missing_or_ambiguous_evidence" -> "缺少或无法唯一匹配证据"
    "no_execution_evidence" -> "没有执行证据"
    "executing" -> "正在执行"
    "awaiting_execution" -> "等待执行"
    else -> reason
}

private fun missingText(approval: ApprovalView): String =
    when {
        approval.detailsStatus != "complete" -> "详情不完整"
        approval.missingDetails.isNotEmpty() -> approval.missingDetails.joinToString("、")
        approval.reviewTruncated -> "内容被截断"
        else -> "当前状态不接受允许"
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

private fun runEventText(type: String, reason: String?): String = when (type) {
    "run.created" -> "新回合已受理"
    "run.started" -> "回合开始执行"
    "run.completed" -> "回合已完成"
    "run.failed" -> "回合失败"
    "run.interrupted" -> "回合已中断" + (reason?.let { "（$it）" } ?: "")
    else -> type
}

private fun approvalStatusText(status: String): String = when (status) {
    "pending" -> "等待决定"
    "decision_recorded" -> "决定已收到，等待发送"
    "dispatching" -> "等待 Codex 处理"
    "resolved" -> "请求已处理，请查看执行结果"
    "expired" -> "请求已失效"
    "unknown" -> "决定可能已送达，正在核对"
    else -> "状态：" + status
}

/** Stable test tags shared with the instrumented acceptance suite. */
object Tag {
    const val CONNECTION = "connection"
    const val ERROR = "error"
    const val NOTICE = "notice"
    const val BUSY = "busy"
    const val LOADING = "loading"
    const val PAIRING_INPUT = "pairing_input"
    const val PAIRING_PARSE = "pairing_parse"
    const val PAIRING_ERROR = "pairing_error"
    const val PAIRING_REVIEW = "pairing_review"
    const val PAIRING_PIN = "pairing_pin"
    const val PAIRING_CONFIRM = "pairing_confirm"
    const val REFRESH_SESSIONS = "refresh_sessions"
    const val EMPTY_SESSIONS = "empty_sessions"
    const val BACK = "back"
    const val RUN_STATUS = "run_status"
    const val TIMELINE = "timeline"
    const val LOAD_EARLIER = "load_earlier"
    const val INTERRUPT = "interrupt"
    const val CONFIRM_INTERRUPT = "confirm_interrupt"
    const val COMPOSER = "composer"
    const val SEND_RUN = "send_run"
    const val ACTIVE_NOTICE = "active_notice"
    const val APPROVAL_BACK = "approval_back"
    const val APPROVAL_STATUS = "approval_status"
    const val APPROVAL_COMMAND = "approval_command"
    const val APPROVAL_CWD = "approval_cwd"
    const val DECISION_NOTICE = "decision_notice"
    const val LAST_DECISION = "last_decision"
    const val INSUFFICIENT = "insufficient"
    const val ALLOW = "allow"
    const val DENY = "deny"
    const val OPEN_RESULTS = "open_results"
    const val RESULTS_BACK = "results_back"
    const val REFRESH_RESULTS = "refresh_results"
    const val RESULT_UNAVAILABLE = "result_unavailable"
    const val RESULT_RUN_STATUS = "result_run_status"
    const val RESULT_RUN_TERMINAL = "result_run_terminal"
    const val RESULT_VERIFICATION_SUMMARY = "result_verification_summary"
    const val RESULT_COMMANDS_EMPTY = "result_commands_empty"
    const val LOAD_MORE_COMMANDS = "load_more_commands"
    const val RESULT_ARTIFACTS_EMPTY = "result_artifacts_empty"
    const val LOAD_MORE_ARTIFACTS = "load_more_artifacts"
    const val ARTIFACT_BACK = "artifact_back"
    const val ARTIFACT_METADATA = "artifact_metadata"
    const val ARTIFACT_CONTENT = "artifact_content"
    const val ARTIFACT_EMPTY = "artifact_empty"
    const val ARTIFACT_BINARY = "artifact_binary"
    const val ARTIFACT_UNAVAILABLE = "artifact_unavailable"
    const val ARTIFACT_TRUNCATED = "artifact_truncated"
    const val LOAD_MORE_ARTIFACT_CONTENT = "load_more_artifact_content"

    fun sessionRow(sessionId: String) = "session_row_$sessionId"

    fun sessionStatus(sessionId: String) = "session_status_$sessionId"

    fun commandStatus(key: String) = "command_status_$key"

    fun verificationItem(key: String) = "verification_item_$key"

    fun verificationStatus(key: String) = "verification_status_$key"

    fun verificationExit(key: String) = "verification_exit_$key"

    fun verificationOutput(key: String) = "verification_output_$key"

    fun runCommand(itemId: String) = "run_command_$itemId"

    fun commandOutput(itemId: String) = "command_output_$itemId"

    fun artifactItem(artifactId: String) = "artifact_item_$artifactId"

    fun artifactMetadata(artifactId: String) = "artifact_metadata_$artifactId"

    fun artifactOpen(artifactId: String) = "artifact_open_$artifactId"

    fun artifactUnavailable(artifactId: String) = "artifact_unavailable_$artifactId"
}

/** Actions the screens invoke; wired to the view model by the activity. */
class Actions(
    val beginPairing: (String) -> Unit,
    val confirmPairing: () -> Unit,
    val cancelPairing: () -> Unit,
    val refreshSessions: () -> Unit,
    val openSession: (String) -> Unit,
    val backToSessions: () -> Unit,
    val loadEarlier: () -> Unit,
    val openApproval: (String) -> Unit,
    val closeApproval: () -> Unit,
    val decide: (String) -> Unit,
    val startRun: (String) -> Unit,
    val updateComposer: (String) -> Unit,
    val interrupt: () -> Unit,
    val confirmInterrupt: () -> Unit,
    val cancelInterrupt: () -> Unit,
    val openResults: () -> Unit,
    val closeResults: () -> Unit,
    val refreshResults: () -> Unit,
    val loadMoreCommands: () -> Unit,
    val loadMoreArtifacts: () -> Unit,
    val openArtifact: (RunArtifact) -> Unit,
    val closeArtifact: () -> Unit,
    val loadMoreArtifact: () -> Unit,
    val dismissError: () -> Unit,
)