package dev.runvori.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
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
import dev.runvori.app.data.RunArtifact
import dev.runvori.app.data.RunCommand
import dev.runvori.app.data.RunResults
import dev.runvori.app.data.VerificationItem
import dev.runvori.app.data.VerificationView

@Composable
internal fun ResultsScreen(state: AppState, actions: Actions) {
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
