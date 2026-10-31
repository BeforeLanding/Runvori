package dev.runvori.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp

@Composable
internal fun PairingScreen(state: AppState, actions: Actions) {
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
