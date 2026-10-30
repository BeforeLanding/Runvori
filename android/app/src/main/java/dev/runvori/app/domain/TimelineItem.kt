package dev.runvori.app.domain

/**
 * Render-ready timeline item, projected from normalized events.
 *
 * Items are keyed by the upstream item id so streaming chunks merge into one
 * entry instead of creating a new row per token. The event cursor is tracked
 * separately: it decides replay, never display order.
 */
sealed interface TimelineItem {
    val key: String
    val firstSeq: Long
    val lastSeq: Long
    val runId: String?

    data class UserMessage(
        override val key: String,
        override val firstSeq: Long,
        override val lastSeq: Long,
        override val runId: String?,
        val text: String,
    ) : TimelineItem

    data class AgentMessage(
        override val key: String,
        override val firstSeq: Long,
        override val lastSeq: Long,
        override val runId: String?,
        val text: String,
        val complete: Boolean,
        val textOmitted: Boolean,
    ) : TimelineItem

    data class Command(
        override val key: String,
        override val firstSeq: Long,
        override val lastSeq: Long,
        override val runId: String?,
        val command: String?,
        val commandTruncated: Boolean,
        val cwd: String?,
        val status: String?,
        val exitCode: Int?,
        val durationMs: Int?,
        val output: String,
        val outputOmitted: Boolean,
        val complete: Boolean,
    ) : TimelineItem

    data class Approval(
        override val key: String,
        override val firstSeq: Long,
        override val lastSeq: Long,
        override val runId: String?,
        val approvalId: String,
        val kind: String,
        val status: String,
        val detailsStatus: String?,
        val version: Long? = null,
    ) : TimelineItem

    data class RunEvent(
        override val key: String,
        override val firstSeq: Long,
        override val lastSeq: Long,
        override val runId: String?,
        val type: String,
        val reason: String?,
    ) : TimelineItem

    data class Notice(
        override val key: String,
        override val firstSeq: Long,
        override val lastSeq: Long,
        override val runId: String?,
        val type: String,
        val text: String,
    ) : TimelineItem
}

/** One persisted, ordered event as read from the Host. */
data class SessionEvent(
    val seq: Long,
    val type: String,
    val runId: String?,
    val payload: Map<String, Any?>,
    val recordedAt: String?,
)