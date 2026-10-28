package dev.runvori.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import dev.runvori.app.data.HostSession
import dev.runvori.app.data.KeystoreCredentialStore
import dev.runvori.app.data.RunvoriCache
import dev.runvori.app.ui.Actions
import dev.runvori.app.ui.RunvoriApp
import dev.runvori.app.ui.RunvoriViewModel

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val session = HostSession(
            credentialStore = KeystoreCredentialStore(applicationContext),
            cache = RunvoriCache(applicationContext),
            deviceName = deviceLabel(),
        )
        val viewModel = RunvoriViewModel(session)
        setContent {
            val dark = isSystemInDarkTheme()
            MaterialTheme(colorScheme = if (dark) darkColorScheme() else lightColorScheme()) {
                Surface(modifier = Modifier.fillMaxSize()) {
                    val state by viewModel.state.collectAsState()
                    DisposableEffect(Unit) {
                        viewModel.start()
                        onDispose { viewModel.close() }
                    }
                    RunvoriApp(state, remember(viewModel) {
                        Actions(
                            beginPairing = viewModel::beginPairing,
                            confirmPairing = viewModel::confirmPairing,
                            cancelPairing = viewModel::cancelPairing,
                            refreshSessions = viewModel::refreshSessions,
                            openSession = viewModel::openSession,
                            backToSessions = viewModel::backToSessions,
                            loadEarlier = viewModel::loadEarlier,
                            openApproval = viewModel::openApproval,
                            closeApproval = viewModel::closeApproval,
                            decide = viewModel::decide,
                            startRun = viewModel::startRun,
                            updateComposer = viewModel::updateComposer,
                            interrupt = viewModel::interrupt,
                            confirmInterrupt = viewModel::confirmInterrupt,
                            cancelInterrupt = viewModel::cancelInterrupt,
                            openResults = viewModel::openResults,
                            closeResults = viewModel::closeResults,
                            refreshResults = viewModel::refreshResults,
                            loadMoreCommands = viewModel::loadMoreCommands,
                            loadMoreArtifacts = viewModel::loadMoreArtifacts,
                            openArtifact = viewModel::openArtifact,
                            closeArtifact = viewModel::closeArtifact,
                            loadMoreArtifact = viewModel::loadMoreArtifact,
                            dismissError = viewModel::dismissError,
                        )
                    })
                }
            }
        }
    }

    private fun deviceLabel(): String =
        (android.os.Build.MODEL ?: "android").take(64)
}
