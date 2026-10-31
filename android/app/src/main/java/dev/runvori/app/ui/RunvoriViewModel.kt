package dev.runvori.app.ui

import dev.runvori.app.data.ApprovalView
import dev.runvori.app.data.CachedSession
import dev.runvori.app.data.CachedSessionView
import dev.runvori.app.data.ConnectionState
import dev.runvori.app.data.HostSession
import dev.runvori.app.data.NavigationRoute
import dev.runvori.app.data.NavigationRouteStore
import dev.runvori.app.data.RunArtifact
import dev.runvori.app.data.SessionSync
import dev.runvori.app.domain.PairingError
import dev.runvori.app.domain.PairingInfo
import dev.runvori.app.domain.ProtocolError
import dev.runvori.app.domain.TimelineItem
import dev.runvori.app.domain.TimelineProjector
import androidx.lifecycle.ViewModel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import java.io.IOException
import java.util.UUID

/**
 * Screen-level state and actions.
 *
 * The rule that shapes this class: a submitted write is never retried with a
 * fresh request id. The request id is created once, stored locally, and reused
 * for status queries. When the response is lost the UI shows "result
 * unconfirmed" and offers a query, not a second attempt.
 */
class RunvoriViewModel(
    private val session: HostSession,
    private val routes: NavigationRouteStore? = null,
) : ViewModel() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var pollJob: Job? = null
    private var started = false
    private var routeRestored = false
    private val artifactBuffer = java.io.ByteArrayOutputStream()
    // Bumped whenever the artifact screen changes; a chunk that returns after
    // the user moved on must not append itself to the next artifact.
    private var artifactGeneration = 0
    // A file-review item can arrive after the request; refresh the open
    // approval only when its timeline version or status advanced.
    private var approvalRefreshKey: String? = null
    private var approvalRefreshInFlight = false

    private val _state = MutableStateFlow(AppState())
    val state: StateFlow<AppState> = _state.asStateFlow()

    fun start() {
        if (started) {
            resume()
            return
        }
        started = true
        scope.launch { reconnect() }
    }

    /**
     * The Activity is visible again. Keep the current screen and bring its
     * data up to date; a rotation must not fall back to the session list.
     */
    fun resume() {
        if (!started) {
            start()
            return
        }
        if (_state.value.connection != "已连接") {
            // Returning to the foreground is also a recovery opportunity: an
            // offline screen keeps its cached projection while a fresh
            // connection attempt runs.
            if (_state.value.connection in OFFLINE_MESSAGES) {
                scope.launch { reconnect() }
            }
            return
        }
        when (_state.value.screen) {
            Screen.Sessions -> {
                refreshSessions()
                refreshOperations()
            }
            Screen.Timeline, Screen.Approval -> {
                val sessionId = _state.value.selectedSessionId ?: return
                startPolling(sessionId)
                syncSelectedSession()
            }
            Screen.Results -> refreshResults()
            Screen.Artifact, Screen.Pairing -> Unit
        }
    }

    /** Stop background polling without discarding the screen or its data. */
    fun pause() {
        pollJob?.cancel()
        pollJob = null
    }

    fun close() {
        pollJob?.cancel()
        pollJob = null
        scope.cancel()
    }

    override fun onCleared() {
        close()
        super.onCleared()
    }

    private suspend fun reconnect() {
        _state.value = _state.value.copy(
            connection = "正在连接", busy = true, writable = false)
        when (val result = runCatching { session.connect() }.getOrElse {
            ConnectionState.Offline(it.javaClass.simpleName)
        }) {
            is ConnectionState.Online -> {
                _state.value = _state.value.copy(
                    connection = "已连接", busy = false,
                    hostName = result.credential.hostName,
                    hostId = result.credential.hostId)
                if (!routeRestored) {
                    routeRestored = true
                    if (restoreRoute(result.credential.hostId)) {
                        refreshOperations()
                        return
                    }
                }
                when (_state.value.screen) {
                    Screen.Timeline, Screen.Approval -> {
                        val sessionId = _state.value.selectedSessionId
                        if (sessionId != null) {
                            startPolling(sessionId)
                            syncSelectedSession()
                        }
                        refreshOperations()
                    }
                    Screen.Results -> {
                        refreshOperations()
                        _state.value.resultsRunId?.let(::loadResults)
                    }
                    Screen.Artifact -> refreshOperations()
                    else -> {
                        _state.value = _state.value.copy(screen = Screen.Sessions)
                        persistRoute(Screen.Sessions)
                        refreshSessions()
                        refreshOperations()
                    }
                }
            }
            ConnectionState.NotPaired -> {
                routes?.clear()
                _state.value = _state.value.copy(
                    connection = "未配对", busy = false, screen = Screen.Pairing)
            }
            ConnectionState.Unauthorized -> {
                routes?.clear()
                _state.value = _state.value.copy(
                    connection = "配对凭据已失效，请在电脑重新配对", busy = false,
                    writable = false, screen = Screen.Pairing)
            }
            ConnectionState.Incompatible -> _state.value = _state.value.copy(
                connection = "版本不兼容，请更新后重连", busy = false,
                writable = false)
            ConnectionState.IdentityChanged -> {
                routes?.clear()
                _state.value = _state.value.copy(
                    connection = "Host 身份已改变，请重新配对", busy = false,
                    writable = false, screen = Screen.Pairing)
            }
            is ConnectionState.Offline -> {
                // The cache is the only source while the Host is unreachable.
                // It is read-only: `writable` stays false and every write
                // entry point re-checks the live connection.
                val credential = session.credential()
                val hostId = credential?.hostId.orEmpty()
                val cached = if (hostId.isEmpty()) emptyList()
                else session.cachedSessions().map(::row)
                val screen = if (_state.value.screen == Screen.Pairing)
                    Screen.Sessions else _state.value.screen
                _state.value = _state.value.copy(
                    connection = OFFLINE_MESSAGE, busy = false,
                    writable = false, hostId = hostId,
                    hostName = credential?.hostName ?: _state.value.hostName,
                    sessions = if (cached.isEmpty()) _state.value.sessions
                    else cached,
                    screen = screen)
                if (screen == Screen.Sessions) persistRoute(Screen.Sessions)
            }
        }
    }

    /**
     * Restore the screen a previous process was reading. Only the location is
     * restored; every visible fact is re-read from the Host and cache, and an
     * approval that no longer exists is shown as stale, never as actionable.
     */
    private suspend fun restoreRoute(hostId: String): Boolean {
        val route = routes?.load() ?: return false
        if (route.hostId != hostId) {
            routes.clear()
            return false
        }
        when (route.screen) {
            Screen.Timeline.name -> {
                val sessionId = route.sessionId ?: return false
                _state.value = _state.value.copy(
                    screen = Screen.Timeline, selectedSessionId = sessionId,
                    timeline = emptyList(), approval = null)
                syncSelectedSession()
                startPolling(sessionId)
            }
            Screen.Approval.name -> {
                val approvalId = route.approvalId ?: return false
                _state.value = _state.value.copy(
                    screen = Screen.Approval, selectedSessionId = route.sessionId,
                    approval = null, busy = true)
                route.sessionId?.let {
                    startPolling(it)
                    syncSelectedSession()
                }
                openApproval(approvalId)
            }
            Screen.Results.name -> {
                val runId = route.runId ?: return false
                _state.value = _state.value.copy(
                    screen = Screen.Results, selectedSessionId = route.sessionId)
                openResultsFor(runId)
            }
            else -> {
                _state.value = _state.value.copy(screen = Screen.Sessions)
                refreshSessions()
            }
        }
        return true
    }

    private fun persistRoute(screen: Screen, approvalId: String? = null,
                             runId: String? = null) {
        val store = routes ?: return
        val hostId = _state.value.hostId
        if (hostId.isEmpty()) return
        store.save(NavigationRoute(
            hostId = hostId,
            screen = screen.name,
            sessionId = _state.value.selectedSessionId,
            approvalId = approvalId ?: _state.value.approval?.approvalId,
            runId = runId ?: _state.value.resultsRunId,
        ))
    }

    // -- pairing ---------------------------------------------------------
    fun beginPairing(text: String) {
        val info = try {
            PairingInfo.parse(text)
        } catch (error: PairingError) {
            _state.value = _state.value.copy(pairingError = pairingMessage(error.code))
            return
        }
        val endpoint = try {
            PairingInfo.parseHttpsEndpoint(info.httpsUrl)
        } catch (error: PairingError) {
            _state.value = _state.value.copy(pairingError = pairingMessage(error.code))
            return
        }
        _state.value = _state.value.copy(
            pairingReview = info, pairingError = null, busy = false)
    }

    fun cancelPairing() {
        _state.value = _state.value.copy(pairingReview = null, pairingError = null)
    }

    fun confirmPairing() {
        val info = _state.value.pairingReview ?: return
        val endpoint = try {
            PairingInfo.parseHttpsEndpoint(info.httpsUrl)
        } catch (error: PairingError) {
            _state.value = _state.value.copy(pairingError = pairingMessage(error.code))
            return
        }
        _state.value = _state.value.copy(busy = true, pairingError = null)
        scope.launch {
            try {
                session.pair(info, endpoint)
                routes?.clear()
                routeRestored = true
                _state.value = _state.value.copy(
                    busy = false, pairingReview = null)
                reconnect()
            } catch (error: ProtocolError) {
                _state.value = _state.value.copy(
                    busy = false, pairingError = pairingMessage(error.code))
            } catch (error: Exception) {
                // A TLS refusal or a network failure: the secret was not used
                // successfully, and the user must not be told to retry blindly.
                // The exception class is included because the fix differs
                // between a pin refusal and an unreachable endpoint.
                _state.value = _state.value.copy(
                    busy = false,
                    pairingError = pairingMessage(transportCode(error, info)) +
                        "（" + error.javaClass.simpleName +
                        (error.message?.let { ":" + it } ?: "") + "）")
            }
        }
    }

    fun unpair() {
        pause()
        session.forget()
        routes?.clear()
        _state.value = AppState(
            connection = "未配对", screen = Screen.Pairing,
            pairingError = "已在手机解除配对；电脑上的设备仍在，需要时请在电脑撤销")
    }

    // -- sessions --------------------------------------------------------
    fun refreshSessions() {
        scope.launch {
            _state.value = _state.value.copy(loading = true, error = null)
            if (!ensureConnected()) {
                applyCachedSessions()
                return@launch
            }
            try {
                val sessions = session.syncSessions()
                _state.value = _state.value.copy(
                    loading = false, sessions = sessions.map(::row),
                    lastSync = "刚刚")
            } catch (error: ProtocolError) {
                handle(error)
            } catch (error: IOException) {
                applyCachedSessions()
            }
        }
    }

    fun openSession(sessionId: String) {
        pollJob?.cancel()
        _state.value = _state.value.copy(
            screen = Screen.Timeline, selectedSessionId = sessionId,
            timeline = emptyList(), syncGap = false, writable = false,
            approval = null, approvalStale = false,
            approvalStaleReason = null, error = null, notice = null)
        persistRoute(Screen.Timeline)
        if (!session.isConnected()) {
            session.cachedSession(sessionId)?.let(::applyCachedSession)
        }
        syncSelectedSession()
        startPolling(sessionId)
    }

    fun backToSessions() {
        pause()
        _state.value = _state.value.copy(
            screen = Screen.Sessions, selectedSessionId = null,
            approval = null, approvalStale = false, approvalStaleReason = null,
            timeline = emptyList())
        persistRoute(Screen.Sessions)
        refreshSessions()
    }

    /**
     * Re-open the Host if the previous attempt failed. Returns false only when
     * the caller must stay on the cached projection; the state already names
     * the reason.
     */
    private suspend fun ensureConnected(): Boolean {
        if (session.isConnected()) return true
        return when (val result = runCatching { session.connect() }.getOrElse {
            ConnectionState.Offline(it.javaClass.simpleName)
        }) {
            is ConnectionState.Online -> {
                _state.value = _state.value.copy(
                    connection = "已连接", busy = false,
                    hostName = result.credential.hostName,
                    hostId = result.credential.hostId)
                true
            }
            ConnectionState.Unauthorized -> {
                routes?.clear()
                _state.value = _state.value.copy(
                    connection = "配对凭据已失效，请在电脑重新配对",
                    busy = false, writable = false, screen = Screen.Pairing)
                false
            }
            ConnectionState.IdentityChanged -> {
                routes?.clear()
                _state.value = _state.value.copy(
                    connection = "Host 身份已改变，请重新配对", busy = false,
                    writable = false, screen = Screen.Pairing)
                false
            }
            ConnectionState.Incompatible -> {
                _state.value = _state.value.copy(
                    connection = "版本不兼容，请更新后重连", busy = false,
                    writable = false)
                false
            }
            ConnectionState.NotPaired -> {
                routes?.clear()
                _state.value = _state.value.copy(
                    connection = "未配对", busy = false,
                    writable = false, screen = Screen.Pairing)
                false
            }
            is ConnectionState.Offline -> {
                _state.value = _state.value.copy(
                    connection = OFFLINE_MESSAGE, busy = false,
                    writable = false)
                false
            }
        }
    }

    private fun applyCachedSessions() {
        val hostId = session.credentialHostId()
        // No credential means the pairing screen owns the state; do not
        // relabel an unpaired client as an offline one.
        if (hostId == null) {
            _state.value = _state.value.copy(loading = false)
            return
        }
        _state.value = _state.value.copy(
            loading = false,
            connection = OFFLINE_MESSAGE,
            writable = false,
            hostId = hostId,
            sessions = session.cachedSessions().map(::row),
            error = OFFLINE_HINT)
    }

    /** Project one cached session without claiming it is current. */
    private fun applyCachedSession(view: CachedSessionView) {
        val row = view.session
        val active = row.activeRunId?.let {
            ActiveRunView(it, row.activeRunStatus ?: "unknown",
                row.activeRunVersion)
        }
        _state.value = _state.value.copy(
            loading = false,
            connection = OFFLINE_MESSAGE,
            writable = false,
            timeline = TimelineProjector.project(view.events),
            continuousSeq = view.continuousSeq,
            latestSeq = row.snapshotSeq,
            syncGap = view.hasGap,
            sessionVersion = row.sessionVersion,
            workspaceName = row.workspaceName,
            activeRun = active,
            error = OFFLINE_HINT)
    }

    private fun syncSelectedSession() {
        val sessionId = _state.value.selectedSessionId ?: return
        scope.launch {
            _state.value = _state.value.copy(loading = true)
            if (!ensureConnected()) {
                val cached = session.cachedSession(sessionId)
                if (cached != null) applyCachedSession(cached)
                else _state.value = _state.value.copy(
                    loading = false, connection = OFFLINE_MESSAGE,
                    writable = false, error = OFFLINE_HINT)
                return@launch
            }
            try {
                applySync(session.syncSession(sessionId))
                refreshOperations()
            } catch (error: ProtocolError) {
                handle(error)
            } catch (error: IOException) {
                val cached = session.cachedSession(sessionId)
                if (cached != null) applyCachedSession(cached)
                else _state.value = _state.value.copy(
                    loading = false, connection = OFFLINE_MESSAGE,
                    writable = false, error = OFFLINE_HINT)
            }
        }
    }

    private fun startPolling(sessionId: String) {
        pollJob?.cancel()
        pollJob = scope.launch {
            var backoff = 1_000L
            while (true) {
                delay(backoff)
                // The approval screen sits on top of the timeline; polling
                // must keep running there, otherwise the run's output and
                // terminal state would be stale the moment the user returns.
                if (_state.value.screen == Screen.Sessions) return@launch
                if (_state.value.selectedSessionId != sessionId) return@launch
                if (!session.isConnected() && !ensureConnected()) {
                    // A revoked or incompatible Host is terminal; a network
                    // failure is retried without leaving the cached timeline.
                    if (_state.value.connection !in OFFLINE_MESSAGES) {
                        return@launch
                    }
                    backoff = (backoff * 2).coerceAtMost(30_000L)
                    continue
                }
                try {
                    val sync = session.syncSession(sessionId)
                    applySync(sync)
                    backoff = 1_000L
                } catch (error: ProtocolError) {
                    if (error.code == "unauthorized") {
                        _state.value = _state.value.copy(
                            connection = "配对凭据已失效，请在电脑重新配对",
                            writable = false)
                        return@launch
                    }
                    backoff = (backoff * 2).coerceAtMost(30_000L)
                } catch (error: IOException) {
                    _state.value = _state.value.copy(
                        connection = "正在重新连接", writable = false)
                    backoff = (backoff * 2).coerceAtMost(30_000L)
                }
            }
        }
    }

    private fun applySync(sync: SessionSync) {
        val items = TimelineProjector.project(sync.events)
        val active = sync.snapshot.activeRun
        val writable = !sync.hasGap
        _state.value = _state.value.copy(
            loading = false, connection = "已连接",
            timeline = items,
            continuousSeq = sync.continuousSeq,
            latestSeq = sync.latestSeq,
            syncGap = sync.hasGap,
            sessionVersion = sync.snapshot.sessionVersion,
            workspaceName = sync.snapshot.workspaceName,
            activeRun = active?.let {
                ActiveRunView(it.runId, it.status, it.version)
            },
            writable = writable,
            // A successful sync disproves a connectivity error, but not a
            // refusal: `startRun` sets its message and then syncs, and an
            // unconditional clear here erased "已有活动回合" before it could be
            // read.
            error = if (_state.value.error in TRANSIENT_ERRORS) null
            else _state.value.error,
        )
        val open = _state.value.approval
        if (_state.value.screen == Screen.Approval && open != null
                && !_state.value.busy && _state.value.decisionInFlight == null) {
            val item = items.filterIsInstance<TimelineItem.Approval>()
                .firstOrNull { it.approvalId == open.approvalId }
            if (item != null) {
                val newerVersion = (item.version ?: 0L) > open.version
                val changed = item.detailsStatus != open.detailsStatus ||
                    item.status != open.status
                if (newerVersion || changed) {
                    refreshOpenApproval(open.approvalId, item.version,
                        item.status, item.detailsStatus)
                }
            } else if (open.status in OPEN_APPROVAL_STATUSES) {
                // The approval is gone from the timeline. It may have expired
                // during a Host restart; re-read it instead of leaving stale
                // buttons on screen.
                refreshOpenApproval(open.approvalId, null, null, null,
                    nonce = sync.latestSeq)
            }
        }
    }

    /**
     * Re-read an approval whose review content changed while its screen is
     * open. The timeline event and the approval row commit together, so a
     * version in the timeline is a safe trigger; the request is de-duplicated
     * and never replaces a newer open view with an older response.
     */
    private fun refreshOpenApproval(approvalId: String, version: Long?,
                                    status: String?, detailsStatus: String?,
                                    nonce: Long? = null) {
        val key = listOf(approvalId, version, status, detailsStatus, nonce)
            .joinToString("|")
        if (approvalRefreshInFlight || approvalRefreshKey == key) return
        approvalRefreshKey = key
        approvalRefreshInFlight = true
        scope.launch {
            try {
                val refreshed = session.api().approval(approvalId)
                val current = _state.value.approval
                if (current != null
                        && _state.value.screen == Screen.Approval
                        && current.approvalId == approvalId
                        && refreshed.version >= current.version) {
                    _state.value = _state.value.copy(
                        approval = refreshed, approvalStale = false,
                        approvalStaleReason = null)
                    recoverApprovalDecision(refreshed)
                }
            } catch (error: ProtocolError) {
                if (error.code in STALE_APPROVAL_ERRORS) {
                    markApprovalStale(staleApprovalMessage(error.code))
                }
            } catch (error: IOException) {
                // A transient read failure is not proof the approval vanished;
                // the next sync retries.
            } finally {
                val current = _state.value.approval
                if (version != null && current != null
                        && current.approvalId == approvalId
                        && current.version < version) {
                    // The API did not catch up yet; a later sync retries.
                    approvalRefreshKey = null
                }
                approvalRefreshInFlight = false
            }
        }
    }

    /**
     * A decision may have been submitted before the screen was reopened. The
     * operation row is written before the network call, so finding one here
     * means the product must query it, not offer a second decision.
     */
    private fun recoverApprovalDecision(view: ApprovalView) {
        if (view.approvalId.isEmpty() || _state.value.approvalStale) return
        if (view.status !in OPEN_APPROVAL_STATUSES) return
        val operation = runCatching {
            session.latestOperation("approval", view.approvalId)
        }.getOrNull() ?: return
        if (operation.status == "failed" && !operation.unresolvable) return
        val unresolved = operation.unresolvable ||
            operation.status == "unknown"
        _state.value = _state.value.copy(
            decisionInFlight = operation.requestId,
            decisionNotice = if (unresolved)
                "结果待确认：已有审批决定提交，正在用原请求编号核对"
            else "已有审批决定已提交，等待 Host 确认…",
            decisionUnresolved = unresolved)
        reconcileDecision(view.approvalId, operation.requestId)
    }

    private fun reconcileDecision(approvalId: String, requestId: String) {
        scope.launch {
            var unresolved = false
            var failed = false
            var confirmed = false
            try {
                val receipt = session.api().operation(
                    OPERATION_QUERY_KIND, requestId)
                val hostId = runCatching { session.hostId() }.getOrNull()
                if (hostId != null) {
                    session.cacheOperation(hostId, receipt, OPERATION_QUERY_KIND)
                }
                when (receipt.status) {
                    "confirmed" -> confirmed = true
                    "failed" -> failed = true
                    "unknown" -> unresolved = true
                }
            } catch (error: ProtocolError) {
                // A missing operation means the Host never persisted the
                // write. It is still unresolved from the user's point of
                // view: the decision may have crossed the network before the
                // failure, so do not silently resubmit it.
                unresolved = true
            } catch (error: IOException) {
                unresolved = true
            }
            val current = _state.value.approval
            if (current == null || current.approvalId != approvalId) return@launch
            _state.value = _state.value.copy(
                decisionInFlight = if (failed) null else _state.value.decisionInFlight,
                decisionNotice = when {
                    failed -> "操作未完成，请查看原因"
                    unresolved -> "结果待确认：可稍后查询此操作"
                    confirmed -> "已确认处理"
                    else -> "等待 Codex 处理"
                },
                decisionUnresolved = unresolved)
        }
    }

    private fun markApprovalStale(message: String) {
        val current = _state.value.approval
        _state.value = _state.value.copy(
            approval = current?.copy(
                status = if (current.status in OPEN_APPROVAL_STATUSES)
                    "expired" else current.status,
                canAllow = false, canDeny = false,
                allowedDecisions = emptyList()),
            approvalStale = true,
            approvalStaleReason = message,
            busy = false,
            decisionInFlight = null,
            pendingDecision = null,
            decisionNotice = null,
            decisionUnresolved = false)
    }

    fun loadEarlier() {
        val sessionId = _state.value.selectedSessionId ?: return
        scope.launch {
            try {
                val page = session.loadHistory(sessionId)
                if (page == null) {
                    _state.value = _state.value.copy(notice = "已到历史开头")
                } else {
                    syncSelectedSession()
                }
            } catch (error: Exception) {
                _state.value = _state.value.copy(error = "历史加载失败")
            }
        }
    }

    // -- results ---------------------------------------------------------
    /**
     * Open the result of the latest run, not the earliest one still in the
     * timeline. `activeRun` wins while a run is live; otherwise the newest
     * timeline row carrying a run id identifies the completed result.
     */
    fun openResults() {
        if (!session.isConnected()) {
            _state.value = _state.value.copy(
                notice = "离线，无法读取结果；恢复连接后再试")
            return
        }
        val runId = _state.value.activeRun?.runId
            ?: _state.value.timeline.lastOrNull { it.runId != null }?.runId
        if (runId == null) {
            _state.value = _state.value.copy(notice = "还没有可查看的回合结果")
            return
        }
        openResultsFor(runId)
    }

    private fun openResultsFor(runId: String) {
        artifactGeneration++
        _state.value = _state.value.copy(
            screen = Screen.Results, resultsRunId = runId, results = null,
            resultsLoading = true, commands = emptyList(), commandsCursor = null,
            commandsHasMore = false, commandsLoading = false,
            artifacts = emptyList(), artifactsCursor = null,
            artifactsHasMore = false, artifactsLoading = false,
            artifact = null, artifactText = "", artifactNextOffset = null,
            artifactError = null, error = null, notice = null)
        persistRoute(Screen.Results, runId = runId)
        artifactBuffer.reset()
        loadResults(runId)
    }

    fun refreshResults() {
        val runId = _state.value.resultsRunId ?: return
        if (_state.value.resultsLoading || _state.value.commandsLoading) return
        _state.value = _state.value.copy(resultsLoading = true, error = null)
        loadResults(runId)
    }

    private fun loadResults(runId: String) {
        scope.launch {
            try {
                val result = session.api().runResults(runId)
                val page = session.api().runCommands(runId)
                val artifacts = session.api().runArtifacts(runId)
                _state.value = _state.value.copy(
                    results = result, resultsLoading = false,
                    commands = page.items, commandsCursor = page.nextCursor,
                    commandsHasMore = page.hasMore,
                    artifacts = artifacts.items,
                    artifactsCursor = artifacts.nextCursor,
                    artifactsHasMore = artifacts.hasMore)
            } catch (error: ProtocolError) {
                _state.value = _state.value.copy(
                    resultsLoading = false, error = resultMessage(error.code))
            } catch (error: IOException) {
                _state.value = _state.value.copy(
                    resultsLoading = false, results = null,
                    error = "结果读取失败，请检查连接")
            }
        }
    }

    fun loadMoreCommands() {
        val runId = _state.value.resultsRunId ?: return
        val cursor = _state.value.commandsCursor ?: return
        if (_state.value.commandsLoading || !_state.value.commandsHasMore) return
        _state.value = _state.value.copy(commandsLoading = true, error = null)
        scope.launch {
            try {
                val page = session.api().runCommands(runId, cursor)
                _state.value = _state.value.copy(
                    commands = _state.value.commands + page.items,
                    commandsCursor = page.nextCursor,
                    commandsHasMore = page.hasMore,
                    commandsLoading = false)
            } catch (error: ProtocolError) {
                _state.value = _state.value.copy(
                    commandsLoading = false, error = resultMessage(error.code))
            } catch (error: IOException) {
                _state.value = _state.value.copy(
                    commandsLoading = false, error = "命令读取失败，请检查连接")
            }
        }
    }

    fun loadMoreArtifacts() {
        val runId = _state.value.resultsRunId ?: return
        val cursor = _state.value.artifactsCursor ?: return
        if (_state.value.artifactsLoading || !_state.value.artifactsHasMore) return
        _state.value = _state.value.copy(artifactsLoading = true, error = null)
        scope.launch {
            try {
                val page = session.api().runArtifacts(runId, cursor)
                _state.value = _state.value.copy(
                    artifacts = _state.value.artifacts + page.items,
                    artifactsCursor = page.nextCursor,
                    artifactsHasMore = page.hasMore,
                    artifactsLoading = false)
            } catch (error: ProtocolError) {
                _state.value = _state.value.copy(
                    artifactsLoading = false, error = resultMessage(error.code))
            } catch (error: IOException) {
                _state.value = _state.value.copy(
                    artifactsLoading = false, error = "产物读取失败，请检查连接")
            }
        }
    }

    fun openArtifact(artifact: RunArtifact) {
        val generation = ++artifactGeneration
        artifactBuffer.reset()
        val available = artifact.contentAvailable
        val binary = artifact.representation == "binary"
        // A process loss returns to the results list, not to a half-loaded
        // artifact that has no durable content buffer.
        persistRoute(Screen.Results)
        _state.value = _state.value.copy(
            screen = Screen.Artifact, artifact = artifact, artifactText = "",
            artifactLoadedBytes = 0, artifactNextOffset = 0,
            artifactLoading = false, artifactRepresentation = artifact.representation,
            artifactTruncated = artifact.truncated,
            artifactDroppedBytes = artifact.droppedBytes,
            artifactError = when {
                !available -> artifactUnavailableText(artifact)
                binary -> "二进制内容仅显示元信息。"
                else -> null
            })
        if (available && !binary && artifact.sizeBytes > 0) {
            loadArtifactChunk(artifact.artifactId, 0, generation)
        }
    }

    fun loadMoreArtifact() {
        val artifact = _state.value.artifact ?: return
        val next = _state.value.artifactNextOffset ?: return
        if (_state.value.artifactLoading || next >= artifact.sizeBytes) return
        if (!artifact.contentAvailable || artifact.representation == "binary") return
        loadArtifactChunk(artifact.artifactId, next, artifactGeneration)
    }

    private fun loadArtifactChunk(artifactId: String, offset: Long,
                                  generation: Int) {
        _state.value = _state.value.copy(artifactLoading = true, artifactError = null)
        scope.launch {
            try {
                val chunk = session.api().artifactContent(artifactId, offset)
                if (!artifactRequestIsCurrent(generation)) return@launch
                if (chunk.offset != offset || chunk.nextOffset <= offset) {
                    throw ProtocolError("invalid_artifact_content", 200)
                }
                artifactBuffer.write(chunk.data)
                val text = artifactBuffer.toByteArray().toString(Charsets.UTF_8)
                _state.value = _state.value.copy(
                    artifactText = text,
                    artifactLoadedBytes = chunk.nextOffset,
                    artifactNextOffset = chunk.nextOffset,
                    artifactRepresentation = chunk.representation,
                    artifactTruncated = chunk.truncated,
                    artifactDroppedBytes = chunk.droppedBytes,
                    artifactLoading = false)
            } catch (error: ProtocolError) {
                if (!artifactRequestIsCurrent(generation)) return@launch
                _state.value = _state.value.copy(
                    artifactLoading = false, artifactError = artifactMessage(error.code))
            } catch (error: IOException) {
                if (!artifactRequestIsCurrent(generation)) return@launch
                _state.value = _state.value.copy(
                    artifactLoading = false, artifactError = "产物内容读取失败，请检查连接")
            }
        }
    }

    private fun artifactRequestIsCurrent(generation: Int): Boolean =
        generation == artifactGeneration && _state.value.screen == Screen.Artifact

    fun closeArtifact() {
        artifactGeneration++
        _state.value = _state.value.copy(
            screen = Screen.Results, artifact = null, artifactText = "",
            artifactNextOffset = null, artifactError = null,
            artifactLoading = false)
        persistRoute(Screen.Results)
        artifactBuffer.reset()
    }

    fun closeResults() {
        artifactGeneration++
        _state.value = _state.value.copy(
            screen = Screen.Timeline, results = null, resultsLoading = false,
            commands = emptyList(), commandsCursor = null,
            commandsHasMore = false, commandsLoading = false,
            artifacts = emptyList(), artifactsCursor = null,
            artifactsHasMore = false, artifactsLoading = false,
            artifact = null, artifactText = "", artifactNextOffset = null,
            artifactError = null, artifactLoading = false)
        persistRoute(Screen.Timeline, runId = null)
        artifactBuffer.reset()
        syncSelectedSession()
    }

    // -- approvals -------------------------------------------------------
    fun openApproval(approvalId: String) {
        if (!session.isConnected()) {
            _state.value = _state.value.copy(
                error = "离线，无法读取审批详情；恢复连接后再试")
            return
        }
        approvalRefreshKey = null
        _state.value = _state.value.copy(
            screen = Screen.Approval, approval = null, busy = true, error = null,
            approvalStale = false, approvalStaleReason = null,
            decisionInFlight = null, pendingDecision = null,
            decisionNotice = null, decisionUnresolved = false)
        persistRoute(Screen.Approval, approvalId = approvalId)
        scope.launch {
            try {
                val view = session.api().approval(approvalId)
                _state.value = _state.value.copy(busy = false, approval = view)
                recoverApprovalDecision(view)
            } catch (error: ProtocolError) {
                if (error.code in STALE_APPROVAL_ERRORS) {
                    _state.value = _state.value.copy(
                        busy = false, screen = Screen.Approval,
                        approval = null, approvalStale = true,
                        approvalStaleReason = staleApprovalMessage(error.code))
                } else {
                    _state.value = _state.value.copy(
                        busy = false, screen = Screen.Timeline,
                        error = approvalMessage(error))
                }
            } catch (error: IOException) {
                _state.value = _state.value.copy(
                    busy = false, screen = Screen.Timeline,
                    error = "无法读取审批详情，请检查连接")
            }
        }
    }

    fun closeApproval() {
        approvalRefreshKey = null
        _state.value = _state.value.copy(
            screen = Screen.Timeline, approvalStale = false,
            approvalStaleReason = null)
        persistRoute(Screen.Timeline)
        // Decisions often resolve while the approval screen is open; refresh
        // so the timeline shows the output without a manual pull.
        syncSelectedSession()
    }

    /**
     * Submit one decision. The button is disabled while the call is in flight,
     * and the request id is persisted before the network call so a lost
     * response can be queried instead of re-submitted.
     */
    fun decide(decision: String) {
        val approval = _state.value.approval ?: return
        if (_state.value.decisionInFlight != null) return
        val hostId = runCatching { session.hostId() }.getOrNull()
            ?: run {
                _state.value = _state.value.copy(
                    error = "未连接，不能提交审批决定")
                return
            }
        val previous = runCatching {
            session.latestOperation("approval", approval.approvalId)
        }.getOrNull()
        if (previous != null && previous.status != "failed") {
            _state.value = _state.value.copy(
                decisionInFlight = previous.requestId,
                decisionNotice = if (previous.unresolvable ||
                    previous.status == "unknown")
                    "结果待确认：已有审批决定提交，不能重复发送"
                else "已有审批决定已提交，正在核对…",
                decisionUnresolved = previous.unresolvable ||
                    previous.status == "unknown")
            return
        }
        val requestId = "dec-" + UUID.randomUUID().toString()
        // Persist the request id before the network call. If the response is
        // lost or the process dies, the same id is queried, never re-sent.
        recordOperation(hostId, requestId, "approval", approval.approvalId)
        _state.value = _state.value.copy(
            decisionInFlight = requestId,
            pendingDecision = decision,
            decisionNotice = "已提交，等待 Host 确认…")
        scope.launch {
            try {
                val receipt = session.activeApi()!!.decide(
                    approval.approvalId, requestId, approval.version, decision)
                _state.value = _state.value.copy(
                    decisionNotice = decisionNotice(receipt),
                    lastDecision = LastDecision(
                        requestId = requestId, decision = decision,
                        recordedAt = System.currentTimeMillis()))
                val hostIdNow = session.hostId()
                session.cacheOperation(hostIdNow, receipt, "approval")
                awaitApprovalOutcome(receipt.requestId)
            } catch (error: ProtocolError) {
                when (error.code) {
                    "resource_changed" -> {
                        _state.value = _state.value.copy(
                            decisionInFlight = null, decisionNotice = null,
                            error = "审批已变化，请刷新后重新判断")
                        refreshApproval(approval.approvalId)
                    }
                    "approval_expired" -> {
                        _state.value = _state.value.copy(
                            decisionInFlight = null, decisionNotice = null,
                            error = "审批已失效，不能再允许或拒绝")
                        refreshApproval(approval.approvalId)
                    }
                    "approval_details_insufficient" -> {
                        _state.value = _state.value.copy(
                            decisionInFlight = null, decisionNotice = null,
                            error = "审批详情不足，只能拒绝")
                        refreshApproval(approval.approvalId)
                    }
                    "decision_conflict" -> {
                        _state.value = _state.value.copy(
                            decisionInFlight = null, decisionNotice = null,
                            error = "已有相反的审批决定")
                        refreshApproval(approval.approvalId)
                    }
                    else -> {
                        _state.value = _state.value.copy(
                            decisionInFlight = null, decisionNotice = null,
                            error = approvalMessage(error))
                        if (error.retryable || error.status >= 500) {
                            confirmOperation(requestId, "approval")
                        }
                    }
                }
            } catch (error: IOException) {
                // The Host may have recorded the decision before the socket
                // failed. Do not re-submit: query the same request id.
                _state.value = _state.value.copy(
                    decisionInFlight = null, decisionNotice = null,
                    error = "结果待确认：决定可能已送达，正在用原请求编号查询")
                confirmOperation(requestId, "approval")
            }
        }
    }

    private suspend fun awaitApprovalOutcome(requestId: String) {
        repeat(APPROVAL_POLLS) {
            delay(APPROVAL_POLL_DELAY)
            // Query the submitted operation itself, not only the approval
            // view: the operation carries the delivery status (accepted ->
            // dispatching -> confirmed/failed) that the cache records.
            val hostId = runCatching { session.hostId() }.getOrNull()
            val receipt = runCatching {
                session.api().operation(OPERATION_QUERY_KIND, requestId)
            }.getOrNull()
            if (hostId != null && receipt != null) {
                session.cacheOperation(hostId, receipt, OPERATION_QUERY_KIND)
            }
            val current = _state.value.approval ?: return
            val refreshed = try {
                session.api().approval(current.approvalId)
            } catch (error: ProtocolError) {
                if (error.code in STALE_APPROVAL_ERRORS) {
                    markApprovalStale(staleApprovalMessage(error.code))
                }
                return
            } catch (error: IOException) {
                return
            }
            val operationFinal = receipt?.status in setOf(
                "confirmed", "failed", "unknown")
            val approvedFinal = refreshed.status in setOf(
                "resolved", "expired", "unknown")
            _state.value = _state.value.copy(
                approval = refreshed,
                // A receipt is not an outcome. Keep the buttons disabled until
                // the approval leaves pending or the operation is terminal;
                // otherwise a fast receipt re-enables them before the next
                // sync shows the recorded decision.
                decisionInFlight = if (refreshed.status != "pending")
                    null else _state.value.decisionInFlight,
                decisionNotice = when {
                    receipt?.status == "unknown" || refreshed.status == "unknown" ->
                        "结果待确认：可稍后查询此操作"
                    refreshed.status == "dispatching" ||
                        receipt?.status == "dispatching" -> "等待 Codex 处理"
                    else -> approvalStatusText(refreshed)
                },
                decisionUnresolved = receipt?.status == "unknown" ||
                    refreshed.status == "unknown")
            // Delivery is confirmed as soon as the Host says so; the approval
            // can legitimately stay `resolved` while the turn keeps running.
            if (operationFinal || approvedFinal) return
        }
        _state.value = _state.value.copy(
            decisionUnresolved = true,
            decisionNotice = "结果待确认：可稍后查询此操作")
    }

    private fun refreshApproval(approvalId: String) {
        scope.launch {
            try {
                val refreshed = session.api().approval(approvalId)
                _state.value = _state.value.copy(
                    approval = refreshed, approvalStale = false,
                    approvalStaleReason = null)
                recoverApprovalDecision(refreshed)
            } catch (error: ProtocolError) {
                if (error.code in STALE_APPROVAL_ERRORS) {
                    markApprovalStale(staleApprovalMessage(error.code))
                }
            } catch (error: IOException) {
                // Keep the current view; a later sync retries.
            }
        }
    }

    // -- operations ------------------------------------------------------
    fun refreshOperations() {
        scope.launch {
            val pending = runCatching {
                session.pendingOperations().map { operation ->
                    runCatching { session.refreshOperation(operation) }
                        .getOrDefault(operation)
                }
            }.getOrDefault(emptyList())
            _state.value = _state.value.copy(
                unresolvedInformed = pending.isNotEmpty())
        }
    }

    private fun recordOperation(hostId: String, requestId: String, kind: String,
                                resourceId: String?) {
        session.recordOperation(hostId, requestId, kind, resourceId)
    }

    private fun confirmOperation(requestId: String, kind: String) {
        scope.launch {
            val hostId = runCatching { session.hostId() }.getOrNull() ?: return@launch
            val receipt = runCatching { session.api().operation(kind, requestId) }
                .getOrNull()
            if (receipt == null) {
                _state.value = _state.value.copy(
                    decisionUnresolved = true,
                    error = "结果待确认：请稍后用原请求编号再查询")
            } else {
                session.cacheOperation(hostId, receipt, kind)
                _state.value = _state.value.copy(
                    decisionUnresolved = receipt.status == "unknown",
                    decisionNotice = decisionNotice(receipt))
            }
        }
    }

    // -- runs ------------------------------------------------------------
    fun startRun(input: String) {
        val sessionId = _state.value.selectedSessionId ?: return
        if (input.isBlank()) return
        if (_state.value.startInFlight) return
        if (!session.isConnected()) {
            _state.value = _state.value.copy(
                error = "离线，不能提交新回合", notice = null)
            return
        }
        val requestId = "run-" + UUID.randomUUID().toString()
        val expected = _state.value.sessionVersion
        _state.value = _state.value.copy(
            startInFlight = true, composerText = "", error = null,
            notice = "正在提交新回合…")
        scope.launch {
            try {
                val receipt = session.api().startRun(
                    sessionId, requestId, expected, input)
                session.cacheOperation(session.hostId(), receipt, "run")
                _state.value = _state.value.copy(
                    startInFlight = false, notice = "新回合已受理")
                syncSelectedSession()
            } catch (error: ProtocolError) {
                val message = when (error.code) {
                    "active_run_exists" -> "已有活动回合，请等待或中断"
                    "resource_changed" -> "会话已变化，请刷新后重试"
                    "thread_unavailable" -> "会话线程不可用，请在电脑处理"
                    "execution_unavailable" -> "执行环境不可用"
                    else -> runMessage(error.code)
                }
                _state.value = _state.value.copy(
                    startInFlight = false, notice = null, error = message)
                syncSelectedSession()
            } catch (error: IOException) {
                _state.value = _state.value.copy(
                    startInFlight = false,
                    error = "结果待确认：提交可能已送达，正在核对")
                syncSelectedSession()
            }
        }
    }

    fun updateComposer(text: String) {
        _state.value = _state.value.copy(composerText = text.take(MAX_INPUT))
    }

    fun interrupt() {
        val run = _state.value.activeRun ?: return
        _state.value = _state.value.copy(
            interruptConfirm = true)
    }

    fun cancelInterrupt() {
        _state.value = _state.value.copy(interruptConfirm = false)
    }

    fun confirmInterrupt() {
        val run = _state.value.activeRun ?: return
        val requestId = "int-" + UUID.randomUUID().toString()
        _state.value = _state.value.copy(
            interruptConfirm = false, interruptInFlight = true,
            error = null)
        scope.launch {
            try {
                val receipt = session.api().interrupt(
                    run.runId, requestId, run.version)
                session.cacheOperation(session.hostId(), receipt, "interrupt")
                _state.value = _state.value.copy(
                    interruptInFlight = false, notice = "已请求中断，等待终态")
                syncSelectedSession()
            } catch (error: ProtocolError) {
                _state.value = _state.value.copy(
                    interruptInFlight = false,
                    error = "中断请求未受理：" + interruptMessage(error.code))
                syncSelectedSession()
            } catch (error: IOException) {
                _state.value = _state.value.copy(
                    interruptInFlight = false,
                    error = "结果待确认：中断可能已送达，正在核对")
                syncSelectedSession()
            }
        }
    }

    fun dismissError() {
        // The one banner button dismisses whichever banner is showing; a
        // notice left behind would keep claiming something that is no longer
        // on screen.
        _state.value = _state.value.copy(error = null, notice = null)
    }

    private fun handle(error: ProtocolError) {
        if (error.code == "unauthorized") {
            _state.value = _state.value.copy(
                loading = false,
                connection = "配对凭据已失效，请在电脑重新配对",
                writable = false, screen = Screen.Pairing,
                error = "配对凭据已失效")
            return
        }
        _state.value = _state.value.copy(
            loading = false, error = approvalMessage(error))
    }

    private fun row(session: CachedSession): SessionRow = SessionRow(
        sessionId = session.sessionId,
        title = session.title ?: session.workspaceName ?: "会话",
        workspaceName = session.workspaceName,
        status = session.activeRunStatus ?: session.latestRunStatus,
        pendingApprovals = session.pendingApprovals,
        updatedAt = session.updatedAt,
        syncedAt = session.syncedAt,
        active = session.activeRunId != null,
    )

    companion object {
        const val MAX_INPUT = 16 * 1024
        const val APPROVAL_POLLS = 8
        const val APPROVAL_POLL_DELAY = 1_000L
        const val OPERATION_QUERY_KIND = "approval"
        val OPEN_APPROVAL_STATUSES = setOf(
            "pending", "decision_recorded", "dispatching")
        val STALE_APPROVAL_ERRORS = setOf(
            "approval_not_found", "approval_expired", "resource_changed")
        /** Messages a completed sync makes untrue; every other error stands. */
        val TRANSIENT_ERRORS = setOf(
            "网络不可达，保留上次同步内容",
            "网络不可达，显示上次同步内容")
        const val OFFLINE_MESSAGE = "离线，显示上次同步内容"
        const val OFFLINE_HINT = "网络不可达，显示上次同步内容；当前为只读"
        val OFFLINE_MESSAGES = setOf(OFFLINE_MESSAGE, "正在重新连接")
    }
}
