package dev.runvori.app.ui

import dev.runvori.app.data.ApprovalView
import dev.runvori.app.data.RunArtifact
import dev.runvori.app.data.RunCommand
import dev.runvori.app.data.RunResults
import dev.runvori.app.domain.PairingInfo
import dev.runvori.app.domain.TimelineItem

enum class Screen { Pairing, Sessions, Timeline, Approval, Results, Artifact }

data class ActiveRunView(val runId: String, val status: String, val version: Long)

data class SessionRow(
    val sessionId: String,
    val title: String,
    val workspaceName: String?,
    val status: String?,
    val pendingApprovals: Int,
    val updatedAt: String?,
    val syncedAt: Long,
    val active: Boolean,
)

data class LastDecision(
    val requestId: String,
    val decision: String,
    val recordedAt: Long,
)

data class AppState(
    val screen: Screen = Screen.Pairing,
    val connection: String = "正在连接",
    val hostName: String = "",
    val hostId: String = "",
    val busy: Boolean = false,
    val loading: Boolean = false,
    val writable: Boolean = false,
    val sessions: List<SessionRow> = emptyList(),
    val selectedSessionId: String? = null,
    val workspaceName: String? = null,
    val timeline: List<TimelineItem> = emptyList(),
    val results: RunResults? = null,
    val resultsRunId: String? = null,
    val resultsLoading: Boolean = false,
    val commands: List<RunCommand> = emptyList(),
    val commandsCursor: String? = null,
    val commandsHasMore: Boolean = false,
    val commandsLoading: Boolean = false,
    val artifacts: List<RunArtifact> = emptyList(),
    val artifactsCursor: String? = null,
    val artifactsHasMore: Boolean = false,
    val artifactsLoading: Boolean = false,
    val artifact: RunArtifact? = null,
    val artifactText: String = "",
    val artifactLoadedBytes: Long = 0,
    val artifactNextOffset: Long? = null,
    val artifactLoading: Boolean = false,
    val artifactRepresentation: String = "unknown",
    val artifactTruncated: Boolean = false,
    val artifactDroppedBytes: Long = 0,
    val artifactError: String? = null,
    val continuousSeq: Long = 0,
    val latestSeq: Long = 0,
    val syncGap: Boolean = false,
    val sessionVersion: Long = 0,
    val activeRun: ActiveRunView? = null,
    val approval: ApprovalView? = null,
    val approvalStale: Boolean = false,
    val approvalStaleReason: String? = null,
    val pairingReview: PairingInfo? = null,
    val pairingError: String? = null,
    val error: String? = null,
    val notice: String? = null,
    val lastSync: String? = null,
    val composerText: String = "",
    val startInFlight: Boolean = false,
    val interruptConfirm: Boolean = false,
    val interruptInFlight: Boolean = false,
    val decisionInFlight: String? = null,
    val pendingDecision: String? = null,
    val decisionNotice: String? = null,
    val decisionUnresolved: Boolean = false,
    val lastDecision: LastDecision? = null,
    val unresolvedInformed: Boolean = false,
)
