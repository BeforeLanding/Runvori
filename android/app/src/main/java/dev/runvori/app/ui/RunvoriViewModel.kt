package dev.runvori.app.ui

import dev.runvori.app.data.ApprovalView
import dev.runvori.app.data.CachedOperation
import dev.runvori.app.data.CachedSession
import dev.runvori.app.data.ConnectionState
import dev.runvori.app.data.HostSession
import dev.runvori.app.data.OperationReceipt
import dev.runvori.app.data.RunCommand
import dev.runvori.app.data.RunResults
import dev.runvori.app.data.SessionSync
import dev.runvori.app.domain.PairingError
import dev.runvori.app.domain.PairingEndpoint
import dev.runvori.app.domain.PairingInfo
import dev.runvori.app.domain.ProtocolError
import dev.runvori.app.domain.TimelineItem
import dev.runvori.app.domain.TimelineProjector
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
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
class RunvoriViewModel(private val session: HostSession) {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var pollJob: Job? = null

    private val _state = MutableStateFlow(AppState())
    val state: StateFlow<AppState> = _state.asStateFlow()

    fun start() {
        scope.launch { reconnect() }
    }

    fun close() {
        pollJob?.cancel()
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
                    hostId = result.credential.hostId,
                    screen = Screen.Sessions)
                refreshSessions()
                refreshOperations()
            }
            ConnectionState.NotPaired -> _state.value = _state.value.copy(
                connection = "未配对", busy = false, screen = Screen.Pairing)
            ConnectionState.Unauthorized -> _state.value = _state.value.copy(
                connection = "配对凭据已失效，请在电脑重新配对", busy = false,
                writable = false, screen = Screen.Pairing)
            ConnectionState.Incompatible -> _state.value = _state.value.copy(
                connection = "版本不兼容，请更新后重连", busy = false,
                writable = false)
            ConnectionState.IdentityChanged -> _state.value = _state.value.copy(
                connection = "Host 身份已改变，请重新配对", busy = false,
                writable = false, screen = Screen.Pairing)
            is ConnectionState.Offline -> _state.value = _state.value.copy(
                connection = "离线，显示上次同步内容", busy = false,
                writable = false)
        }
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
        pollJob?.cancel()
        session.forget()
        _state.value = AppState(
            connection = "未配对", screen = Screen.Pairing,
            pairingError = "已在手机解除配对；电脑上的设备仍在，需要时请在电脑撤销")
    }

    // -- sessions --------------------------------------------------------
    fun refreshSessions() {
        scope.launch {
            _state.value = _state.value.copy(loading = true, error = null)
            try {
                val sessions = session.syncSessions()
                _state.value = _state.value.copy(
                    loading = false, sessions = sessions.map(::row),
                    lastSync = "刚刚")
            } catch (error: ProtocolError) {
                handle(error)
            } catch (error: IOException) {
                _state.value = _state.value.copy(
                    loading = false, connection = "离线，显示上次同步内容",
                    writable = false, error = "网络不可达，保留上次同步内容")
            }
        }
    }

    fun openSession(sessionId: String) {
        pollJob?.cancel()
        _state.value = _state.value.copy(
            screen = Screen.Timeline, selectedSessionId = sessionId,
            timeline = emptyList(), syncGap = false, writable = false,
            approval = null, error = null, notice = null)
        syncSelectedSession()
        startPolling(sessionId)
    }

    fun backToSessions() {
        pollJob?.cancel()
        _state.value = _state.value.copy(
            screen = Screen.Sessions, selectedSessionId = null,
            approval = null, timeline = emptyList())
        refreshSessions()
    }

    private fun syncSelectedSession() {
        val sessionId = _state.value.selectedSessionId ?: return
        scope.launch {
            _state.value = _state.value.copy(loading = true)
            try {
                applySync(session.syncSession(sessionId))
                refreshOperations()
            } catch (error: ProtocolError) {
                handle(error)
            } catch (error: IOException) {
                _state.value = _state.value.copy(
                    loading = false, connection = "离线，显示上次同步内容",
                    writable = false, error = "网络不可达，显示上次同步内容")
            }
        }
    }

    private fun startPolling(sessionId: String) {
        pollJob = scope.launch {
            var backoff = 1_000L
            while (true) {
                delay(backoff)
                // The approval screen sits on top of the timeline; polling
                // must keep running there, otherwise the run's output and
                // terminal state would be stale the moment the user returns.
                if (_state.value.screen == Screen.Sessions) return@launch
                if (_state.value.selectedSessionId != sessionId) return@launch
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
        val runId = _state.value.activeRun?.runId
            ?: _state.value.timeline.lastOrNull { it.runId != null }?.runId
        if (runId == null) {
            _state.value = _state.value.copy(notice = "还没有可查看的回合结果")
            return
        }
        _state.value = _state.value.copy(
            screen = Screen.Results, resultsRunId = runId, results = null,
            resultsLoading = true, commands = emptyList(), commandsCursor = null,
            commandsHasMore = false, commandsLoading = false,
            error = null, notice = null)
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
                _state.value = _state.value.copy(
                    results = result, resultsLoading = false,
                    commands = page.items, commandsCursor = page.nextCursor,
                    commandsHasMore = page.hasMore)
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

    fun closeResults() {
        _state.value = _state.value.copy(
            screen = Screen.Timeline, results = null, resultsLoading = false,
            commands = emptyList(), commandsCursor = null,
            commandsHasMore = false, commandsLoading = false)
        syncSelectedSession()
    }

    private fun resultMessage(code: String): String = when (code) {
        "run_not_found" -> "回合不存在或已被清理"
        "invalid_cursor" -> "命令游标已失效，请刷新"
        "session_not_found" -> "会话不存在"
        else -> "结果读取失败：" + code
    }

    // -- approvals -------------------------------------------------------
    fun openApproval(approvalId: String) {
        _state.value = _state.value.copy(
            screen = Screen.Approval, approval = null, busy = true, error = null)
        scope.launch {
            try {
                val view = session.api().approval(approvalId)
                _state.value = _state.value.copy(busy = false, approval = view)
            } catch (error: ProtocolError) {
                _state.value = _state.value.copy(
                    busy = false, screen = Screen.Timeline,
                    error = approvalMessage(error))
            } catch (error: IOException) {
                _state.value = _state.value.copy(
                    busy = false, screen = Screen.Timeline,
                    error = "无法读取审批详情，请检查连接")
            }
        }
    }

    fun closeApproval() {
        _state.value = _state.value.copy(screen = Screen.Timeline)
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
        val requestId = "dec-" + UUID.randomUUID().toString()
        _state.value = _state.value.copy(
            decisionInFlight = requestId,
            pendingDecision = decision,
            decisionNotice = "已提交，等待 Host 确认…")
        val hostId = runCatching { session.hostId() }.getOrNull()
        if (hostId != null) {
            session.api()
            recordOperation(hostId, requestId, "approval", approval.runId)
        }
        scope.launch {
            try {
                val receipt = session.activeApi()!!.decide(
                    approval.approvalId, requestId, approval.version, decision)
                _state.value = _state.value.copy(
                    decisionNotice = decisionNotice(receipt),
                    decisionInFlight = null,
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
            val refreshed = runCatching {
                session.api().approval(current.approvalId)
            }.getOrNull() ?: return
            val operationFinal = receipt?.status in setOf(
                "confirmed", "failed", "unknown")
            val approvedFinal = refreshed.status in setOf(
                "resolved", "expired", "unknown")
            _state.value = _state.value.copy(
                approval = refreshed,
                decisionInFlight = null,
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
            val refreshed = runCatching { session.api().approval(approvalId) }
                .getOrNull() ?: return@launch
            _state.value = _state.value.copy(approval = refreshed)
        }
    }

    private fun decisionNotice(receipt: OperationReceipt): String =
        when (receipt.status) {
            "accepted" -> "Host 已受理，等待发送与处理"
            "dispatching" -> "正在发送，等待 Codex 处理"
            "confirmed" -> "已确认处理"
            "failed" -> "操作未完成，请查看原因"
            "unknown" -> "结果不确定，正在核对"
            else -> "状态：" + receipt.status
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

    private fun pairingMessage(code: String): String = when (code) {
        "pairing_url_not_https" -> "配对地址必须是 HTTPS"
        "pairing_url_invalid" -> "配对地址格式无效"
        "invalid_pairing_fingerprint" -> "配对信息里的身份指纹无效"
        "invalid_pairing_text" -> "无法解析配对信息，请粘贴电脑输出的完整内容"
        "invalid_pairing_secret" -> "配对秘密无效"
        "pairing_identity_mismatch" -> "Host 返回的身份与配对信息不一致，已拒绝"
        "pairing_unavailable" -> "配对秘密已过期、已使用或已有其他设备"
        "rate_limited" -> "尝试过于频繁，请稍后再试"
        "unauthorized" -> "配对凭据被拒绝"
        "protocol_incompatible" -> "Host 协议版本不兼容"
        else -> "配对失败：" + code
    }

    private fun approvalMessage(error: ProtocolError): String = when (error.code) {
        "resource_changed" -> "状态已变化，请刷新"
        "approval_expired" -> "审批已失效"
        "approval_not_found" -> "审批不存在"
        "operation_not_found" -> "操作不存在，可能未被执行"
        "host_stopped" -> "Host 正在停止"
        else -> "操作失败：" + error.code
    }

    private fun runMessage(code: String): String = when (code) {
        "active_run_exists" -> "已有活动回合"
        "thread_unavailable" -> "会话线程不可用"
        "execution_unavailable" -> "执行环境不可用"
        "idempotency_conflict" -> "请求标识冲突"
        "payload_too_large" -> "输入过长"
        else -> "提交失败：" + code
    }

    private fun interruptMessage(code: String): String = when (code) {
        "resource_changed" -> "回合状态已变化，请刷新"
        "active_run_exists" -> "已有其他活动回合"
        else -> "中断失败：" + code
    }

    private fun transportCode(error: Exception, info: PairingInfo): String =
        when (error) {
            is dev.runvori.app.data.PinMismatch -> error.code
            is javax.net.ssl.SSLException -> "tls_handshake_failed"
            is java.net.SocketTimeoutException -> "connection_timeout"
            is java.net.UnknownHostException -> "host_unreachable"
            is java.net.ConnectException -> "host_unreachable"
            else -> error.javaClass.simpleName
        }

    private fun approvalStatusText(view: ApprovalView): String =
        when (view.status) {
            "pending" -> "等待决定"
            "decision_recorded" -> "决定已收到，等待发送"
            "dispatching" -> "等待 Codex 处理"
            "resolved" -> "请求已处理，请查看执行结果"
            "expired" -> "请求已失效"
            "unknown" -> "决定可能已送达，正在核对"
            else -> "状态：" + view.status
        }

    companion object {
        const val MAX_INPUT = 16 * 1024
        const val APPROVAL_POLLS = 8
        const val APPROVAL_POLL_DELAY = 1_000L
        const val OPERATION_QUERY_KIND = "approval"
        /** Messages a completed sync makes untrue; every other error stands. */
        val TRANSIENT_ERRORS = setOf(
            "网络不可达，保留上次同步内容",
            "网络不可达，显示上次同步内容")
    }
}

enum class Screen { Pairing, Sessions, Timeline, Approval, Results }

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
    val continuousSeq: Long = 0,
    val latestSeq: Long = 0,
    val syncGap: Boolean = false,
    val sessionVersion: Long = 0,
    val activeRun: ActiveRunView? = null,
    val approval: ApprovalView? = null,
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