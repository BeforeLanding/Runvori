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
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import dev.runvori.app.data.HostSession
import dev.runvori.app.data.KeystoreCredentialStore
import dev.runvori.app.data.NavigationRouteStore
import dev.runvori.app.data.RunvoriCache
import dev.runvori.app.ui.Actions
import dev.runvori.app.ui.RunvoriApp
import dev.runvori.app.ui.RunvoriViewModel

class MainActivity : ComponentActivity() {
    private lateinit var viewModel: RunvoriViewModel

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val context = applicationContext
        val factory = object : ViewModelProvider.Factory {
            @Suppress("UNCHECKED_CAST")
            override fun <T : ViewModel> create(modelClass: Class<T>): T =
                RunvoriViewModel(
                    session = HostSession(
                        credentialStore = KeystoreCredentialStore(context),
                        cache = RunvoriCache(context),
                        deviceName = deviceLabel(),
                    ),
                    routes = NavigationRouteStore(context),
                ) as T
        }
        // A configuration change keeps this view model (and its screen), while
        // a new process restores the durable navigation pointer inside it.
        viewModel = ViewModelProvider(this, factory)[RunvoriViewModel::class.java]
        setContent {
            val dark = isSystemInDarkTheme()
            MaterialTheme(colorScheme = if (dark) darkColorScheme() else lightColorScheme()) {
                Surface(modifier = Modifier.fillMaxSize()) {
                    val state by viewModel.state.collectAsState()
                    val lifecycleOwner = LocalLifecycleOwner.current
                    DisposableEffect(lifecycleOwner) {
                        val observer = LifecycleEventObserver { _, event ->
                            when (event) {
                                Lifecycle.Event.ON_START -> viewModel.resume()
                                Lifecycle.Event.ON_STOP -> viewModel.pause()
                                else -> Unit
                            }
                        }
                        lifecycleOwner.lifecycle.addObserver(observer)
                        onDispose {
                            lifecycleOwner.lifecycle.removeObserver(observer)
                        }
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
