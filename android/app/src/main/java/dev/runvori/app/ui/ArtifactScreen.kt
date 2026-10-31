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
import androidx.compose.material3.Card
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

@Composable
internal fun ArtifactScreen(state: AppState, actions: Actions) {
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
