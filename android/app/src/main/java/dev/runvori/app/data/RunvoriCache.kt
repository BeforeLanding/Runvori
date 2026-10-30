package dev.runvori.app.data

import android.content.ContentValues
import android.content.Context
import android.database.Cursor
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import dev.runvori.app.domain.SessionEvent

/**
 * Local projection of Host state.
 *
 * Two invariants drive the schema:
 * - events are unique per `(host_id, session_id, seq)`; a repeated page or a
 *   second Host identity can never be merged into the first one's history;
 * - the continuous cursor only advances inside the same transaction that
 *   stored the events, so a crash cannot claim progress it did not persist.
 *   A gap in the incoming page stops the cursor; it never skips ahead.
 */
class RunvoriCache(context: Context) :
    SQLiteOpenHelper(context, DATABASE_NAME, null, DATABASE_VERSION) {

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL("""
            CREATE TABLE sessions (
                host_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                title TEXT,
                workspace_name TEXT,
                latest_run_id TEXT,
                latest_run_status TEXT,
                active_run_id TEXT,
                active_run_status TEXT,
                active_run_version INTEGER NOT NULL DEFAULT 0,
                pending_approvals INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT,
                session_version INTEGER NOT NULL DEFAULT 0,
                snapshot_seq INTEGER NOT NULL DEFAULT 0,
                synced_at INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (host_id, session_id)
            )
        """)
        db.execSQL("""
            CREATE TABLE events (
                host_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                type TEXT NOT NULL,
                run_id TEXT,
                payload TEXT NOT NULL,
                recorded_at TEXT,
                PRIMARY KEY (host_id, session_id, seq)
            )
        """)
        db.execSQL("""
            CREATE TABLE cursors (
                host_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                continuous_seq INTEGER NOT NULL DEFAULT 0,
                history_before_seq INTEGER,
                PRIMARY KEY (host_id, session_id)
            )
        """)
        db.execSQL("""
            CREATE TABLE operations (
                host_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                resource_id TEXT,
                status TEXT NOT NULL,
                outcome TEXT,
                unresolvable INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (host_id, request_id)
            )
        """)
        db.execSQL("""
            CREATE TABLE approvals (
                host_id TEXT NOT NULL,
                approval_id TEXT NOT NULL,
                run_id TEXT,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 0,
                details_status TEXT,
                checked_at INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (host_id, approval_id)
            )
        """)
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        // Cache content is a projection; the Host is authoritative. A schema
        // change discards it and the next sync rebuilds from a snapshot
        // instead of migrating an unknown local shape.
        db.execSQL("DROP TABLE IF EXISTS approvals")
        db.execSQL("DROP TABLE IF EXISTS operations")
        db.execSQL("DROP TABLE IF EXISTS cursors")
        db.execSQL("DROP TABLE IF EXISTS events")
        db.execSQL("DROP TABLE IF EXISTS sessions")
        onCreate(db)
    }

    fun clearAll(db: SQLiteDatabase = writableDatabase) {
        db.beginTransaction()
        try {
            for (table in listOf("approvals", "operations", "cursors",
                "events", "sessions")) {
                db.delete(table, null, null)
            }
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    fun replaceSessions(hostId: String, sessions: List<SessionSummary>) {
        val db = writableDatabase
        db.beginTransaction()
        try {
            for (session in sessions) {
                val values = ContentValues().apply {
                    put("host_id", hostId)
                    put("session_id", session.sessionId)
                    put("title", session.title)
                    put("workspace_name", session.workspaceName)
                    put("latest_run_id", session.latestRun?.runId)
                    put("latest_run_status", session.latestRun?.status)
                    put("active_run_id", session.activeRun?.runId)
                    put("active_run_status", session.activeRun?.status)
                    put("active_run_version", session.activeRun?.version ?: 0)
                    put("pending_approvals", session.pendingApprovalCount)
                    put("updated_at", session.updatedAt)
                    put("session_version", session.sessionVersion)
                    put("snapshot_seq", session.snapshotSeq)
                    put("synced_at", System.currentTimeMillis())
                }
                db.insertWithOnConflict("sessions", null, values,
                    SQLiteDatabase.CONFLICT_REPLACE)
            }
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    fun sessions(hostId: String): List<CachedSession> {
        val result = ArrayList<CachedSession>()
        readableDatabase.rawQuery(
            "SELECT * FROM sessions WHERE host_id=? ORDER BY session_id",
            arrayOf(hostId)).use { cursor ->
            while (cursor.moveToNext()) result.add(cursor.toSession())
        }
        return result
    }

    fun session(hostId: String, sessionId: String): CachedSession? =
        readableDatabase.rawQuery(
            "SELECT * FROM sessions WHERE host_id=? AND session_id=?",
            arrayOf(hostId, sessionId)).use { cursor ->
            if (cursor.moveToFirst()) cursor.toSession() else null
        }

    /**
     * Store a snapshot's identity and reset the cursor to its boundary.
     *
     * A snapshot may legitimately arrive with a *lower* seq after the Host was
     * rebuilt; the caller detects an identity change before calling this, and
     * this method clears the previous history for the same session so the two
     * identity epochs are never stitched together.
     */
    fun applySnapshot(
        hostId: String,
        snapshot: SessionSnapshot,
        clearExisting: Boolean,
    ) {
        val db = writableDatabase
        db.beginTransaction()
        try {
            if (clearExisting) {
                db.delete("events", "host_id=? AND session_id=?",
                    arrayOf(hostId, snapshot.sessionId))
                db.delete("cursors", "host_id=? AND session_id=?",
                    arrayOf(hostId, snapshot.sessionId))
            }
            val values = ContentValues().apply {
                put("workspace_name", snapshot.workspaceName)
                put("active_run_id", snapshot.activeRun?.runId)
                put("active_run_status", snapshot.activeRun?.status)
                put("active_run_version", snapshot.activeRun?.version ?: 0)
                put("session_version", snapshot.sessionVersion)
                put("snapshot_seq", snapshot.snapshotSeq)
                put("synced_at", System.currentTimeMillis())
            }
            val updated = db.update("sessions", values,
                "host_id=? AND session_id=?",
                arrayOf(hostId, snapshot.sessionId))
            if (updated == 0) {
                // First snapshot for this session: the list row does not exist
                // yet, so create one and let the next list refresh fill the
                // title and latest-run summary.
                val insert = ContentValues(values).apply {
                    put("host_id", hostId)
                    put("session_id", snapshot.sessionId)
                }
                db.insertWithOnConflict("sessions", null, insert,
                    SQLiteDatabase.CONFLICT_IGNORE)
            }
            storeEvents(db, hostId, snapshot.sessionId, snapshot.recentEvents)
            val continuous = continuousFrom(db, hostId, snapshot.sessionId)
            val cursor = ContentValues().apply {
                put("host_id", hostId)
                put("session_id", snapshot.sessionId)
                put("continuous_seq", continuous)
                put("history_before_seq", snapshot.historyBeforeSeq)
            }
            db.insertWithOnConflict("cursors", null, cursor,
                SQLiteDatabase.CONFLICT_REPLACE)
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    /**
     * Store an incremental page and advance the cursor only across a
     * contiguous run. Events beyond a gap are still stored (they are already
     * committed Host facts) but the cursor stops, so the UI asks for the
     * missing range instead of skipping it.
     */
    fun applyIncrement(hostId: String, sessionId: String,
                       events: List<SessionEvent>, latestSeq: Long) {
        val db = writableDatabase
        db.beginTransaction()
        try {
            storeEvents(db, hostId, sessionId, events)
            val continuous = continuousFrom(db, hostId, sessionId)
            val existing = cursorRow(db, hostId, sessionId)
            val previous = existing?.continuousSeq ?: 0L
            val next = maxOf(previous, continuous)
            val values = ContentValues().apply {
                put("host_id", hostId)
                put("session_id", sessionId)
                put("continuous_seq", next)
                put("history_before_seq", existing?.historyBeforeSeq)
            }
            db.insertWithOnConflict("cursors", null, values,
                SQLiteDatabase.CONFLICT_REPLACE)
            val refresh = ContentValues().apply {
                put("snapshot_seq", latestSeq)
                put("synced_at", System.currentTimeMillis())
            }
            db.update("sessions", refresh, "host_id=? AND session_id=?",
                arrayOf(hostId, sessionId))
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    /** Store an older history page without moving the continuous cursor. */
    fun applyHistory(hostId: String, sessionId: String,
                     events: List<SessionEvent>) {
        if (events.isEmpty()) return
        val db = writableDatabase
        db.beginTransaction()
        try {
            storeEvents(db, hostId, sessionId, events)
            val before = events.minOf { it.seq }
            val existing = cursorRow(db, hostId, sessionId)
            val values = ContentValues().apply {
                put("host_id", hostId)
                put("session_id", sessionId)
                put("continuous_seq", existing?.continuousSeq ?: 0L)
                put("history_before_seq", before)
            }
            db.insertWithOnConflict("cursors", null, values,
                SQLiteDatabase.CONFLICT_REPLACE)
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    fun cursor(hostId: String, sessionId: String): CacheCursor =
        cursorRow(readableDatabase, hostId, sessionId)

    fun events(hostId: String, sessionId: String,
               afterSeq: Long = 0L, limit: Int = 500): List<SessionEvent> {
        val result = ArrayList<SessionEvent>()
        readableDatabase.rawQuery(
            "SELECT seq, type, run_id, payload, recorded_at FROM events " +
                "WHERE host_id=? AND session_id=? AND seq>? " +
                "ORDER BY seq ASC LIMIT ?",
            arrayOf(hostId, sessionId, afterSeq.toString(),
                limit.toString())).use { cursor ->
            while (cursor.moveToNext()) result.add(cursor.toEvent())
        }
        return result
    }

    fun eventRange(hostId: String, sessionId: String,
                   fromSeq: Long, toSeq: Long): List<SessionEvent> {
        val result = ArrayList<SessionEvent>()
        readableDatabase.rawQuery(
            "SELECT seq, type, run_id, payload, recorded_at FROM events " +
                "WHERE host_id=? AND session_id=? AND seq>=? AND seq<=? " +
                "ORDER BY seq ASC",
            arrayOf(hostId, sessionId, fromSeq.toString(),
                toSeq.toString())).use { cursor ->
            while (cursor.moveToNext()) result.add(cursor.toEvent())
        }
        return result
    }

    fun storeOperation(hostId: String, receipt: OperationReceipt, kind: String) {
        val now = System.currentTimeMillis()
        val values = ContentValues().apply {
            put("host_id", hostId)
            put("request_id", receipt.requestId)
            put("kind", kind)
            put("resource_id", receipt.resourceId)
            put("status", receipt.status)
            put("outcome", receipt.outcome)
            put("unresolvable", 0)
            put("created_at", now)
            put("updated_at", now)
        }
        writableDatabase.insertWithOnConflict("operations", null, values,
            SQLiteDatabase.CONFLICT_REPLACE)
    }

    fun operations(hostId: String): List<CachedOperation> {
        val result = ArrayList<CachedOperation>()
        readableDatabase.rawQuery(
            "SELECT * FROM operations WHERE host_id=? ORDER BY updated_at DESC",
            arrayOf(hostId)).use { cursor ->
            while (cursor.moveToNext()) result.add(cursor.toOperation())
        }
        return result
    }

    private fun Cursor.toOperation() = CachedOperation(
        requestId = getString(1),
        kind = getString(2),
        resourceId = getString(3),
        status = getString(4),
        outcome = getString(5),
        unresolvable = getInt(6) == 1,
    )

    fun markUnresolvable(hostId: String, requestId: String) {
        val values = ContentValues().apply {
            put("unresolvable", 1)
            put("updated_at", System.currentTimeMillis())
        }
        writableDatabase.update("operations", values,
            "host_id=? AND request_id=?", arrayOf(hostId, requestId))
    }

    /**
     * The latest submitted operation for one resource. A decision is recorded
     * before its network call, so this survives a lost response or a process
     * kill and is what stops the UI from offering a second decision.
     */
    fun latestOperation(hostId: String, kind: String,
                        resourceId: String): CachedOperation? =
        readableDatabase.rawQuery(
            "SELECT * FROM operations WHERE host_id=? AND kind=? " +
                "AND resource_id=? ORDER BY updated_at DESC, rowid DESC LIMIT 1",
            arrayOf(hostId, kind, resourceId)).use { cursor ->
            if (cursor.moveToFirst()) cursor.toOperation() else null
        }

    fun putApproval(hostId: String, view: ApprovalView) {
        val values = ContentValues().apply {
            put("host_id", hostId)
            put("approval_id", view.approvalId)
            put("run_id", view.runId)
            put("kind", view.kind)
            put("status", view.status)
            put("version", view.version)
            put("details_status", view.detailsStatus)
            put("checked_at", System.currentTimeMillis())
        }
        writableDatabase.insertWithOnConflict("approvals", null, values,
            SQLiteDatabase.CONFLICT_REPLACE)
    }

    fun approvalStatus(hostId: String, approvalId: String): String? =
        readableDatabase.rawQuery(
            "SELECT status FROM approvals WHERE host_id=? AND approval_id=?",
            arrayOf(hostId, approvalId)).use { cursor ->
            if (cursor.moveToFirst()) cursor.getString(0) else null
        }

    private fun storeEvents(db: SQLiteDatabase, hostId: String,
                            sessionId: String, events: List<SessionEvent>) {
        for (event in events) {
            val values = ContentValues().apply {
                put("host_id", hostId)
                put("session_id", sessionId)
                put("seq", event.seq)
                put("type", event.type)
                put("run_id", event.runId)
                put("payload", dev.runvori.app.domain.JsonValue.stringify(event.payload))
                put("recorded_at", event.recordedAt)
            }
            db.insertWithOnConflict("events", null, values,
                SQLiteDatabase.CONFLICT_IGNORE)
        }
    }

    /**
     * The largest seq reachable from stored events without a hole, starting at
     * 1. A duplicate page therefore cannot move it backwards or forwards.
     */
    private fun continuousFrom(db: SQLiteDatabase, hostId: String,
                               sessionId: String): Long {
        var result = 0L
        // Events are dense per session; asking the DB for gaps keeps this a
        // single query instead of a row-by-row walk.
        db.rawQuery(
            "SELECT seq FROM events WHERE host_id=? AND session_id=? " +
                "ORDER BY seq ASC", arrayOf(hostId, sessionId)).use { cursor ->
            while (cursor.moveToNext()) {
                val seq = cursor.getLong(0)
                if (seq != result + 1) break
                result = seq
            }
        }
        return result
    }

    private fun cursorRow(db: SQLiteDatabase, hostId: String,
                          sessionId: String): CacheCursor =
        db.rawQuery(
            "SELECT continuous_seq, history_before_seq FROM cursors " +
                "WHERE host_id=? AND session_id=?",
            arrayOf(hostId, sessionId)).use { cursor ->
            if (cursor.moveToFirst()) {
                CacheCursor(
                    continuousSeq = cursor.getLong(0),
                    historyBeforeSeq = if (cursor.isNull(1)) null else cursor.getLong(1),
                )
            } else {
                CacheCursor(0L, null)
            }
        }

    private fun Cursor.toSession() = CachedSession(
        hostId = getString(0),
        sessionId = getString(1),
        title = getString(2),
        workspaceName = getString(3),
        latestRunId = getString(4),
        latestRunStatus = getString(5),
        activeRunId = getString(6),
        activeRunStatus = getString(7),
        activeRunVersion = getLong(8),
        pendingApprovals = getInt(9),
        updatedAt = getString(10),
        sessionVersion = getLong(11),
        snapshotSeq = getLong(12),
        syncedAt = getLong(13),
    )

    private fun Cursor.toEvent(): SessionEvent {
        val raw = getString(3)
        @Suppress("UNCHECKED_CAST")
        val payload = (dev.runvori.app.domain.JsonValue.parse(raw)
            as? Map<String, Any?>) ?: emptyMap()
        return SessionEvent(
            seq = getLong(0),
            type = getString(1),
            runId = getString(2),
            payload = payload,
            recordedAt = getString(4),
        )
    }

    companion object {
        const val DATABASE_NAME = "runvori-cache.db"
        const val DATABASE_VERSION = 1
    }
}

data class CachedSession(
    val hostId: String,
    val sessionId: String,
    val title: String?,
    val workspaceName: String?,
    val latestRunId: String?,
    val latestRunStatus: String?,
    val activeRunId: String?,
    val activeRunStatus: String?,
    val activeRunVersion: Long,
    val pendingApprovals: Int,
    val updatedAt: String?,
    val sessionVersion: Long,
    val snapshotSeq: Long,
    val syncedAt: Long,
)

data class CacheCursor(val continuousSeq: Long, val historyBeforeSeq: Long?)

data class CachedOperation(
    val requestId: String,
    val kind: String,
    val resourceId: String?,
    val status: String,
    val outcome: String?,
    val unresolvable: Boolean,
)