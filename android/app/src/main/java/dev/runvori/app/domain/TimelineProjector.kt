package dev.runvori.app.domain

/**
 * Folds normalized events into stable timeline items.
 *
 * Rules that matter for correctness:
 * - order comes from `seq`, never from timestamps;
 * - an item is keyed by its upstream item id, so a streamed message grows in
 *   place and keeps the reader's position;
 * - an unknown event type is shown as a notice with the raw type name instead
 *   of being dropped, so a pending approval can never disappear silently.
 */
object TimelineProjector {
    private val knownTypes = setOf(
        "run.created", "run.started", "run.completed", "run.failed",
        "run.interrupted", "message.started", "message.delta",
        "message.completed", "command.started", "command.output",
        "command.completed", "approval.requested", "approval.updated",
        "operation.updated", "timeline.truncated", "verification.updated",
        "diagnostic.recorded", "artifact.created", "artifact.unavailable",
        "file_change.updated",
    )

    fun project(events: List<SessionEvent>): List<TimelineItem> {
        val ordered = events.sortedBy { it.seq }
        val items = LinkedHashMap<String, TimelineItem>()
        for (event in ordered) {
            val payload = event.payload
            when (event.type) {
                "message.started", "message.delta", "message.completed" ->
                    foldMessage(items, event)
                "command.started", "command.output", "command.completed" ->
                    foldCommand(items, event)
                "approval.requested", "approval.updated" ->
                    foldApproval(items, event)
                "run.created", "run.started", "run.completed", "run.failed",
                "run.interrupted" -> {
                    val key = "run:${event.runId ?: event.seq}"
                    items[key] = TimelineItem.RunEvent(
                        key = key,
                        firstSeq = items[key]?.firstSeq ?: event.seq,
                        lastSeq = event.seq,
                        runId = event.runId,
                        type = event.type,
                        reason = payload.text("reason"),
                    )
                }
                "timeline.truncated" -> {
                    val key = "notice:${event.seq}"
                    items[key] = TimelineItem.Notice(
                        key = key, firstSeq = event.seq, lastSeq = event.seq,
                        runId = event.runId, type = event.type,
                        text = "超出记录的完整上限，部分内容未保存。",
                    )
                }
                else -> {
                    if (event.type.startsWith("message.")
                        || event.type.startsWith("command.")) {
                        val key = "notice:${event.seq}"
                        items[key] = TimelineItem.Notice(
                            key = key, firstSeq = event.seq, lastSeq = event.seq,
                            runId = event.runId, type = event.type,
                            text = "此内容需升级客户端（${event.type}）。",
                        )
                    }
                    // Other control events (operation.updated,
                    // verification.updated, …) are not timeline rows.
                }
            }
        }
        return items.values.sortedBy { it.firstSeq }
    }

    private fun foldMessage(items: MutableMap<String, TimelineItem>,
                            event: SessionEvent) {
        val itemId = event.payload.text("item_id") ?: return
        val key = "message:$itemId"
        val prior = items[key] as? TimelineItem.AgentMessage
        val text = when (event.type) {
            "message.delta" -> event.payload.text("text").orEmpty()
            else -> ""
        }
        val merged = (prior?.text ?: "") + text
        val complete = event.type == "message.completed"
        val omitted = prior?.textOmitted == true ||
            (complete && event.payload.flag("text_omitted") && text.isEmpty())
        items[key] = TimelineItem.AgentMessage(
            key = key,
            firstSeq = prior?.firstSeq ?: event.seq,
            lastSeq = event.seq,
            runId = event.runId ?: prior?.runId,
            text = merged,
            complete = complete || prior?.complete == true,
            textOmitted = omitted,
        )
    }

    private fun foldCommand(items: MutableMap<String, TimelineItem>,
                            event: SessionEvent) {
        val payload = event.payload
        val itemId = payload.text("item_id") ?: return
        val key = "command:$itemId"
        val prior = items[key] as? TimelineItem.Command
        val outputChunk = if (event.type == "command.output")
            payload.text("text").orEmpty() else ""
        val merged = (prior?.output ?: "") + outputChunk
        items[key] = TimelineItem.Command(
            key = key,
            firstSeq = prior?.firstSeq ?: event.seq,
            lastSeq = event.seq,
            runId = event.runId ?: prior?.runId,
            command = if (event.type == "command.started")
                payload.text("command") else prior?.command,
            commandTruncated = if (event.type == "command.started")
                payload.flag("command_truncated")
            else prior?.commandTruncated ?: false,
            cwd = if (event.type == "command.started")
                payload.text("cwd") else prior?.cwd,
            status = if (event.type == "command.completed")
                payload.text("status") else prior?.status,
            exitCode = if (event.type == "command.completed")
                payload.integer("exit_code") else prior?.exitCode,
            durationMs = if (event.type == "command.completed")
                payload.integer("duration_ms") else prior?.durationMs,
            output = merged,
            outputOmitted = prior?.outputOmitted == true ||
                (event.type == "command.completed"
                    && payload.flag("output_omitted")
                    && outputChunk.isEmpty()),
            complete = event.type == "command.completed" ||
                prior?.complete == true,
        )
    }

    private fun foldApproval(items: MutableMap<String, TimelineItem>,
                             event: SessionEvent) {
        val payload = event.payload
        val approvalId = payload.text("approval_id") ?: return
        val key = "approval:$approvalId"
        val prior = items[key] as? TimelineItem.Approval
        items[key] = TimelineItem.Approval(
            key = key,
            firstSeq = prior?.firstSeq ?: event.seq,
            lastSeq = event.seq,
            runId = event.runId ?: prior?.runId,
            approvalId = approvalId,
            kind = payload.text("kind") ?: prior?.kind ?: "unknown",
            status = payload.text("status")
                ?: if (event.type == "approval.requested") "pending"
                else prior?.status ?: "unknown",
            detailsStatus = payload.text("details_status") ?: prior?.detailsStatus,
            version = payload.long("approval_version") ?: prior?.version,
        )
    }

    private fun Map<String, Any?>.text(name: String): String? =
        this[name] as? String

    private fun Map<String, Any?>.flag(name: String): Boolean =
        this[name] == true

    private fun Map<String, Any?>.integer(name: String): Int? =
        (this[name] as? Number)?.toInt()

    private fun Map<String, Any?>.long(name: String): Long? =
        (this[name] as? Number)?.toLong()
}