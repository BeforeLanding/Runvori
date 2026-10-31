package dev.runvori.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp

@Composable
internal fun SessionsScreen(state: AppState, actions: Actions) {
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
