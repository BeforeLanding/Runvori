package dev.runvori.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp

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
internal fun Banner(message: String, isError: Boolean, onDismiss: () -> Unit) {
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
