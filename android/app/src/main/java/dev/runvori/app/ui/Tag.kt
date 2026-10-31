package dev.runvori.app.ui

/** Stable test tags shared with the instrumented acceptance suite. */
object Tag {
    const val CONNECTION = "connection"
    const val ERROR = "error"
    const val NOTICE = "notice"
    const val BUSY = "busy"
    const val LOADING = "loading"
    const val PAIRING_INPUT = "pairing_input"
    const val PAIRING_PARSE = "pairing_parse"
    const val PAIRING_ERROR = "pairing_error"
    const val PAIRING_REVIEW = "pairing_review"
    const val PAIRING_PIN = "pairing_pin"
    const val PAIRING_CONFIRM = "pairing_confirm"
    const val REFRESH_SESSIONS = "refresh_sessions"
    const val EMPTY_SESSIONS = "empty_sessions"
    const val BACK = "back"
    const val RUN_STATUS = "run_status"
    const val TIMELINE = "timeline"
    const val LOAD_EARLIER = "load_earlier"
    const val INTERRUPT = "interrupt"
    const val CONFIRM_INTERRUPT = "confirm_interrupt"
    const val COMPOSER = "composer"
    const val SEND_RUN = "send_run"
    const val ACTIVE_NOTICE = "active_notice"
    const val APPROVAL_BACK = "approval_back"
    const val PENDING_APPROVAL = "pending_approval_"
    const val APPROVAL_STATUS = "approval_status"
    const val APPROVAL_STALE = "approval_stale"
    const val APPROVAL_COMMAND = "approval_command"
    const val APPROVAL_CWD = "approval_cwd"
    const val DECISION_NOTICE = "decision_notice"
    const val LAST_DECISION = "last_decision"
    const val INSUFFICIENT = "insufficient"
    const val ALLOW = "allow"
    const val DENY = "deny"
    const val OPEN_RESULTS = "open_results"
    const val RESULTS_BACK = "results_back"
    const val REFRESH_RESULTS = "refresh_results"
    const val RESULT_UNAVAILABLE = "result_unavailable"
    const val RESULT_RUN_STATUS = "result_run_status"
    const val RESULT_RUN_TERMINAL = "result_run_terminal"
    const val RESULT_VERIFICATION_SUMMARY = "result_verification_summary"
    const val RESULT_COMMANDS_EMPTY = "result_commands_empty"
    const val LOAD_MORE_COMMANDS = "load_more_commands"
    const val RESULT_ARTIFACTS_EMPTY = "result_artifacts_empty"
    const val LOAD_MORE_ARTIFACTS = "load_more_artifacts"
    const val ARTIFACT_BACK = "artifact_back"
    const val ARTIFACT_METADATA = "artifact_metadata"
    const val ARTIFACT_CONTENT = "artifact_content"
    const val ARTIFACT_EMPTY = "artifact_empty"
    const val ARTIFACT_BINARY = "artifact_binary"
    const val ARTIFACT_UNAVAILABLE = "artifact_unavailable"
    const val ARTIFACT_TRUNCATED = "artifact_truncated"
    const val LOAD_MORE_ARTIFACT_CONTENT = "load_more_artifact_content"

    fun approvalItem(approvalId: String) = "approval_item_$approvalId"

    fun pendingApproval(approvalId: String) = "$PENDING_APPROVAL$approvalId"

    fun sessionRow(sessionId: String) = "session_row_$sessionId"

    fun sessionStatus(sessionId: String) = "session_status_$sessionId"

    fun commandStatus(key: String) = "command_status_$key"

    fun verificationItem(key: String) = "verification_item_$key"

    fun verificationStatus(key: String) = "verification_status_$key"

    fun verificationExit(key: String) = "verification_exit_$key"

    fun verificationOutput(key: String) = "verification_output_$key"

    fun runCommand(itemId: String) = "run_command_$itemId"

    fun commandOutput(itemId: String) = "command_output_$itemId"

    fun artifactItem(artifactId: String) = "artifact_item_$artifactId"

    fun artifactMetadata(artifactId: String) = "artifact_metadata_$artifactId"

    fun artifactOpen(artifactId: String) = "artifact_open_$artifactId"

    fun artifactUnavailable(artifactId: String) = "artifact_unavailable_$artifactId"
}
