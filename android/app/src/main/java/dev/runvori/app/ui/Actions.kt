package dev.runvori.app.ui

import dev.runvori.app.data.RunArtifact

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
