package dev.runvori.app.data

import dev.runvori.app.domain.PairedDevice
import dev.runvori.app.domain.ProtocolError
import dev.runvori.app.domain.SessionEvent

/**
 * The v1 operations the app uses, mapped onto pinned HTTPS exchanges.
 *
 * Each call performs exactly one HTTP exchange and surfaces the Host's error
 * code unchanged. Nothing here retries a write: a duplicate decision or a
 * replayed turn would be a product defect, not a resilience win.
 */
class RunvoriApi(
    private val endpoint: HostEndpoint,
    private val token: ByteArray?,
) {
    fun withToken(token: ByteArray?) = RunvoriApi(endpoint, token)

    fun pair(pairingSecret: String, deviceName: String): PairedDevice {
        val response = endpoint.exchange(
            "POST", "/v1/pairings/exchange",
            body = linkedMapOf(
                "pairing_secret" to pairingSecret,
                "device_name" to deviceName,
            ),
            token = null,
            timeoutMs = 20_000,
        )
        response.error()?.let { throw it }
        return ProtocolView.paired(response)
    }

    fun host(): HttpResult = get("/v1/host")

    fun sessions(cursor: String? = null, limit: Int = 50): SessionPage {
        val query = buildString {
            append("?limit=").append(limit)
            if (cursor != null) append("&cursor=").append(encode(cursor))
        }
        val body = get("/v1/sessions$query")
        return SessionPage(
            items = body.list("items").mapNotNull { summary(it) },
            nextCursor = body.text("next_cursor"),
        )
    }

    fun snapshot(sessionId: String): SessionSnapshot {
        val body = get("/v1/sessions/${encode(sessionId)}/snapshot")
        return SessionSnapshot(
            sessionId = body.text("session_id") ?: sessionId,
            hostId = body.text("host_id"),
            snapshotSeq = body.long("snapshot_seq")
                ?: throw ProtocolError("invalid_snapshot", 200),
            sessionVersion = body.long("session_version")
                ?: throw ProtocolError("invalid_snapshot", 200),
            workspaceName = body.text("workspace_name"),
            activeRun = activeRun(body.map("active_run")),
            openApprovalIds = body.list("open_approval_ids")
                .filterIsInstance<String>(),
            recentEvents = body.list("recent_events").map(ProtocolView::sessionEvent),
            historyBeforeSeq = body.long("history_before_seq"),
            serverTime = body.text("server_time"),
        )
    }

    fun events(
        sessionId: String,
        afterSeq: Long? = null,
        beforeSeq: Long? = null,
        limit: Int = 100,
        waitMs: Int = 0,
        timeoutMs: Int = 35_000,
    ): EventPage {
        val query = buildString {
            append("?limit=").append(limit)
            if (afterSeq != null) append("&after_seq=").append(afterSeq)
            if (beforeSeq != null) append("&before_seq=").append(beforeSeq)
            if (waitMs > 0) append("&wait_ms=").append(waitMs)
        }
        val response = endpoint.exchange(
            "GET", "/v1/sessions/${encode(sessionId)}/events$query",
            token = token, timeoutMs = timeoutMs)
        response.error()?.let { throw it }
        return EventPage(
            events = response.list("events").map(ProtocolView::sessionEvent),
            nextAfterSeq = response.long("next_after_seq"),
            nextBeforeSeq = response.long("next_before_seq"),
            latestSeq = response.long("latest_seq")
                ?: throw ProtocolError("invalid_event_page", 200),
            hasMore = response.body["has_more"] == true,
        )
    }

    fun startRun(
        sessionId: String,
        requestId: String,
        expectedSessionVersion: Long,
        input: String,
    ): OperationReceipt {
        val response = endpoint.exchange(
            "POST", "/v1/sessions/${encode(sessionId)}/runs",
            body = linkedMapOf(
                "request_id" to requestId,
                "expected_session_version" to expectedSessionVersion,
                "input" to input,
            ),
            token = token, timeoutMs = 20_000)
        response.error()?.let { throw it }
        return receipt(response)
    }

    fun interrupt(
        runId: String,
        requestId: String,
        expectedVersion: Long,
    ): OperationReceipt {
        val response = endpoint.exchange(
            "POST", "/v1/runs/${encode(runId)}/interrupt",
            body = linkedMapOf(
                "request_id" to requestId,
                "expected_version" to expectedVersion,
            ),
            token = token, timeoutMs = 20_000)
        response.error()?.let { throw it }
        return receipt(response)
    }

    fun approval(approvalId: String): ApprovalView {
        val body = get("/v1/approvals/${encode(approvalId)}")
        return ApprovalView(
            approvalId = body.text("approval_id") ?: approvalId,
            runId = body.text("run_id"),
            kind = body.text("kind") ?: "unknown",
            version = body.long("version") ?: 1,
            status = body.text("status") ?: "unknown",
            workspaceName = body.text("workspace_name"),
            command = body.text("command"),
            commandTruncated = body.body["command_truncated"] == true,
            commandKind = body.text("command_kind"),
            cwd = body.text("cwd"),
            cwdInsideWorkspace = body.body["cwd_inside_workspace"] == true,
            reason = body.text("reason"),
            changes = changesOf(body.body["changes"]),
            reviewTruncated = body.body["review_truncated"] == true,
            detailsStatus = body.text("details_status"),
            missingDetails = body.list("missing_details").filterIsInstance<String>(),
            allowedDecisions = body.list("allowed_decisions")
                .filterIsInstance<String>(),
            effectiveDecision = body.text("effective_decision"),
            canAllow = body.body["can_allow"] == true,
            canDeny = body.body["can_deny"] == true,
            operationId = body.text("operation_id"),
            operationStatus = body.text("operation_status"),
            operationOutcome = body.text("operation_outcome"),
            requestedAt = body.text("requested_at"),
            expiresAt = body.text("expires_at"),
        )
    }

    fun decide(
        approvalId: String,
        requestId: String,
        expectedVersion: Long,
        decision: String,
    ): OperationReceipt {
        val response = endpoint.exchange(
            "POST", "/v1/approvals/${encode(approvalId)}/decision",
            body = linkedMapOf(
                "request_id" to requestId,
                "expected_version" to expectedVersion,
                "decision" to decision,
            ),
            token = token, timeoutMs = 20_000)
        response.error()?.let { throw it }
        return receipt(response)
    }

    fun operation(kind: String, requestId: String): OperationReceipt {
        val body = get("/v1/operations/${encode(requestId)}?kind=${encode(kind)}")
        return OperationReceipt(
            requestId = body.text("request_id") ?: requestId,
            status = body.text("status") ?: "unknown",
            resourceId = body.text("resource_id"),
            outcome = body.text("outcome"),
        )
    }

    private fun get(path: String): HttpResult {
        val response = endpoint.exchange("GET", path, token = token)
        response.error()?.let { throw it }
        return response
    }

    private fun receipt(response: HttpResult) = OperationReceipt(
        requestId = response.text("request_id") ?: "",
        status = response.text("status") ?: "unknown",
        resourceId = response.text("resource_id"),
        outcome = response.text("outcome"),
    )

    private fun summary(value: Any?): SessionSummary? {
        val map = value as? Map<*, *> ?: return null
        val sessionId = map["session_id"] as? String ?: return null
        val active = activeRun(map["active_run"] as? Map<*, *>)
        val latest = activeRun(map["latest_run"] as? Map<*, *>)
        return SessionSummary(
            sessionId = sessionId,
            title = map["title"] as? String,
            workspaceName = map["workspace_name"] as? String,
            activeRun = active,
            latestRun = latest,
            pendingApprovalCount = (map["pending_approval_count"] as? Number)
                ?.toInt() ?: 0,
            updatedAt = map["updated_at"] as? String,
            sessionVersion = (map["session_version"] as? Number)?.toLong() ?: 0,
            snapshotSeq = (map["snapshot_seq"] as? Number)?.toLong() ?: 0,
        )
    }

    private fun activeRun(value: Map<*, *>?): RunRef? {
        if (value == null) return null
        val runId = value["run_id"] as? String ?: return null
        return RunRef(
            runId = runId,
            status = value["status"] as? String ?: "unknown",
            version = (value["version"] as? Number)?.toLong() ?: 0,
        )
    }

    private fun changesOf(value: Any?): List<FileChangeView> {
        val list = value as? List<*> ?: return emptyList()
        return list.mapNotNull { entry ->
            val map = entry as? Map<*, *> ?: return@mapNotNull null
            FileChangeView(
                path = map["path"] as? String,
                changeType = map["change_type"] as? String,
                movePath = map["move_path"] as? String,
                diff = map["diff"] as? String,
                diffTruncated = map["diff_truncated"] == true,
            )
        }
    }

    private fun encode(value: String): String =
        java.net.URLEncoder.encode(value, "UTF-8")
}

data class SessionPage(val items: List<SessionSummary>, val nextCursor: String?)

data class SessionSummary(
    val sessionId: String,
    val title: String?,
    val workspaceName: String?,
    val activeRun: RunRef?,
    val latestRun: RunRef?,
    val pendingApprovalCount: Int,
    val updatedAt: String?,
    val sessionVersion: Long,
    val snapshotSeq: Long,
)

data class RunRef(val runId: String, val status: String, val version: Long)

data class SessionSnapshot(
    val sessionId: String,
    val hostId: String?,
    val snapshotSeq: Long,
    val sessionVersion: Long,
    val workspaceName: String?,
    val activeRun: RunRef?,
    val openApprovalIds: List<String>,
    val recentEvents: List<SessionEvent>,
    val historyBeforeSeq: Long?,
    val serverTime: String?,
)

data class EventPage(
    val events: List<SessionEvent>,
    val nextAfterSeq: Long?,
    val nextBeforeSeq: Long?,
    val latestSeq: Long,
    val hasMore: Boolean,
)

data class OperationReceipt(
    val requestId: String,
    val status: String,
    val resourceId: String?,
    val outcome: String?,
)

data class FileChangeView(
    val path: String?,
    val changeType: String?,
    val movePath: String?,
    val diff: String?,
    val diffTruncated: Boolean,
)

data class ApprovalView(
    val approvalId: String,
    val runId: String?,
    val kind: String,
    val version: Long,
    val status: String,
    val workspaceName: String?,
    val command: String?,
    val commandTruncated: Boolean,
    val commandKind: String?,
    val cwd: String?,
    val cwdInsideWorkspace: Boolean,
    val reason: String?,
    val changes: List<FileChangeView>,
    val reviewTruncated: Boolean,
    val detailsStatus: String?,
    val missingDetails: List<String>,
    val allowedDecisions: List<String>,
    val effectiveDecision: String?,
    val canAllow: Boolean,
    val canDeny: Boolean,
    val operationId: String?,
    val operationStatus: String?,
    val operationOutcome: String?,
    val requestedAt: String?,
    val expiresAt: String?,
)