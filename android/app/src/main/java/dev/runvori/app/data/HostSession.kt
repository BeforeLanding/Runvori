package dev.runvori.app.data

import dev.runvori.app.domain.PairedDevice
import dev.runvori.app.domain.PairingEndpoint
import dev.runvori.app.domain.PairingInfo
import dev.runvori.app.domain.ProtocolError
import dev.runvori.app.domain.SessionEvent
import java.io.IOException

/**
 * Owns the pairing round trip, credential persistence and the sync sequence.
 *
 * The reconnect order is fixed by the protocol: authenticate, check identity,
 * take a snapshot, fill events, then refresh approvals and unresolved
 * operations. Writes stay closed until that finishes, so the UI cannot offer a
 * decision against stale state.
 */
class HostSession(
    private val credentialStore: CredentialStore,
    private val cache: RunvoriCache,
    private val deviceName: String,
) {
    @Volatile
    private var active: ActiveConnection? = null

    fun credential(): StoredCredential? = credentialStore.load()

    fun isConnected(): Boolean = active != null

    /** The identity a cached projection belongs to, online or not. */
    fun credentialHostId(): String? = credentialStore.load()?.hostId

    /**
     * Read the last committed session list without a live connection.
     *
     * The offline screen is a projection of the cache, never a fresh claim:
     * no HTTP request is attempted here and writes remain closed.
     */
    fun cachedSessions(): List<CachedSession> {
        val hostId = credentialHostId() ?: return emptyList()
        return cache.sessions(hostId)
    }

    /** Read one cached session and its committed event stream. */
    fun cachedSession(sessionId: String): CachedSessionView? {
        val hostId = credentialHostId() ?: return null
        val session = cache.session(hostId, sessionId) ?: return null
        val cursor = cache.cursor(hostId, sessionId)
        return CachedSessionView(
            session = session,
            events = cache.events(hostId, sessionId),
            continuousSeq = cursor.continuousSeq,
            hasGap = cursor.continuousSeq < session.snapshotSeq,
        )
    }

    /** Forgets the local pairing; the Host side must be revoked separately. */
    fun forget() {
        active = null
        credentialStore.clear()
    }

    fun api(): RunvoriApi {
        val connection = active ?: throw ConnectionNotReady()
        return connection.api
    }

    fun hostId(): String = active?.credential?.hostId
        ?: throw ConnectionNotReady()

    /**
     * Exchange a pairing secret over the pinned endpoint and store the result.
     *
     * The pin comes from the pairing payload the user is looking at; the
     * exchange never runs against an unpinned endpoint.
     */
    fun pair(info: PairingInfo, endpoint: PairingEndpoint): PairedDevice {
        val api = RunvoriApi(
            HostEndpoint(endpoint.host, endpoint.port, info.spkiSha256), null)
        val device = api.pair(info.pairingSecret, deviceName)
        if (device.hostId != info.hostId) {
            // A Host that answers with another identity is not the Host the
            // user reviewed; refuse instead of storing the credential.
            throw ProtocolError("pairing_identity_mismatch", 200)
        }
        if (device.protocol != "1") {
            throw ProtocolError("protocol_incompatible", 200)
        }
        credentialStore.save(StoredCredential(
            hostId = device.hostId,
            hostName = info.hostName,
            httpsUrl = endpoint.url,
            spkiSha256 = info.spkiSha256,
            deviceId = device.deviceId,
            token = device.token,
        ))
        // A new pairing is a new identity epoch: nothing cached from a former
        // device may be merged into it.
        cache.clearAll()
        return device
    }

    /**
     * Open a connection from stored credentials.
     *
     * @throws ProtocolError with `unauthorized` when the Host revoked this
     *   device; the caller must then ask for a new pairing while keeping the
     *   cached history readable.
     */
    fun connect(): ConnectionState {
        val credential = credentialStore.load()
            ?: return ConnectionState.NotPaired
        val endpoint = HostEndpoint.fromUrl(credential.httpsUrl, credential.spkiSha256)
            ?: return ConnectionState.NotPaired
        val api = RunvoriApi(endpoint, credential.token)
        val host = try {
            api.host()
        } catch (error: ProtocolError) {
            if (error.code == "unauthorized") {
                return ConnectionState.Unauthorized
            }
            return ConnectionState.Offline(error.code)
        } catch (error: IOException) {
            return ConnectionState.Offline(error.javaClass.simpleName)
        }
        val hostId = host.text("host_id")
        if (hostId != credential.hostId) {
            // Same endpoint, different installation: the cached history must
            // not be continued under the new identity.
            cache.clearAll()
            credentialStore.clear()
            return ConnectionState.IdentityChanged
        }
        if (host.text("protocol") != "1") {
            return ConnectionState.Incompatible
        }
        active = ActiveConnection(credential, api)
        return ConnectionState.Online(credential)
    }

    fun syncSessions(): List<CachedSession> {
        val connection = active ?: throw ConnectionNotReady()
        val hostId = connection.credential.hostId
        val collected = ArrayList<SessionSummary>()
        var cursor: String? = null
        repeat(MAX_PAGES) {
            val page = connection.api.sessions(cursor, 100)
            collected.addAll(page.items)
            cursor = page.nextCursor
            if (cursor == null) return@repeat
        }
        cache.replaceSessions(hostId, collected)
        return cache.sessions(hostId)
    }

    /**
     * Bring one session up to date.
     *
     * Returns the events the UI should now display and the continuous cursor.
     * A cursor ahead of the Host (a rebuilt Host under the same identity) or a
     * cursor the Host no longer retains forces a fresh snapshot.
     */
    fun syncSession(sessionId: String): SessionSync {
        val connection = active ?: throw ConnectionNotReady()
        val hostId = connection.credential.hostId
        val cached = cache.session(hostId, sessionId)
        val snapshot = try {
            connection.api.snapshot(sessionId)
        } catch (error: ProtocolError) {
            throw error
        }
        val snapshotIdentity = snapshot.hostId
        if (snapshotIdentity != null && snapshotIdentity != hostId) {
            cache.clearAll()
            credentialStore.clear()
            throw ProtocolError("host_identity_changed", 200)
        }
        if (cached == null || snapshot.snapshotSeq < cached.snapshotSeq) {
            // Nothing cached, or the Host's sequence went backwards: rebuild
            // from the snapshot instead of merging two numbering epochs.
            cache.applySnapshot(hostId, snapshot, clearExisting = cached != null)
        } else {
            cache.applySnapshot(hostId, snapshot, clearExisting = false)
        }
        val cursor = cache.cursor(hostId, sessionId)
        var after = cursor.continuousSeq
        var lastSeq = snapshot.snapshotSeq
        var gap = false
        var pages = 0
        while (pages < MAX_PAGES) {
            val page = try {
                connection.api.events(sessionId, afterSeq = after, limit = 100)
            } catch (error: ProtocolError) {
                if (error.code == "cursor_ahead") {
                    // The cached cursor cannot exist on this Host; a fresh
                    // snapshot is the only honest recovery.
                    cache.applySnapshot(hostId, snapshot, clearExisting = true)
                    after = 0
                    gap = true
                    pages++
                    continue
                }
                throw error
            }
            cache.applyIncrement(hostId, sessionId, page.events, page.latestSeq)
            lastSeq = page.latestSeq
            val next = cache.cursor(hostId, sessionId).continuousSeq
            if (page.events.isNotEmpty() && next <= after) {
                // The page arrived but the continuous cursor did not move:
                // a hole sits inside the received range. Stop and resync.
                gap = true
                break
            }
            if (!page.hasMore) break
            after = next
            pages++
        }
        val finalCursor = cache.cursor(hostId, sessionId)
        return SessionSync(
            hostId = hostId,
            sessionId = sessionId,
            snapshot = snapshot,
            events = cache.events(hostId, sessionId),
            continuousSeq = finalCursor.continuousSeq,
            hasGap = gap || finalCursor.continuousSeq < lastSeq,
            latestSeq = lastSeq,
        )
    }

    /** Read one older page; returns null when the history start is reached. */
    fun loadHistory(sessionId: String): List<SessionEvent>? {
        val connection = active ?: throw ConnectionNotReady()
        val hostId = connection.credential.hostId
        val cursor = cache.cursor(hostId, sessionId)
        val before = cursor.historyBeforeSeq ?: return null
        if (before <= 1) return null
        val page = connection.api.events(sessionId, beforeSeq = before, limit = 100)
        cache.applyHistory(hostId, sessionId, page.events)
        return page.events
    }

    fun pendingOperations(): List<CachedOperation> {
        val hostId = hostId()
        return cache.operations(hostId).filter {
            !it.unresolvable && it.status in setOf("accepted", "dispatching")
        }
    }

    fun latestOperation(kind: String, resourceId: String): CachedOperation? =
        cache.latestOperation(hostId(), kind, resourceId)

    /**
     * Query a submitted operation again.
     *
     * A receipt that is still `accepted`/`dispatching` after the query timeout
     * is reported as unconfirmed rather than retried; a `404` means the Host
     * never persisted it, which the UI must show as "result unknown".
     */
    fun refreshOperation(operation: CachedOperation): CachedOperation {
        val connection = active ?: throw ConnectionNotReady()
        val hostId = connection.credential.hostId
        val receipt = try {
            connection.api.operation(operation.kind, operation.requestId)
        } catch (error: ProtocolError) {
            if (error.code == "operation_not_found") {
                cache.markUnresolvable(hostId, operation.requestId)
                return operation.copy(unresolvable = true)
            }
            throw error
        }
        cache.storeOperation(hostId, receipt, operation.kind)
        return CachedOperation(
            requestId = receipt.requestId,
            kind = operation.kind,
            resourceId = receipt.resourceId,
            status = receipt.status,
            outcome = receipt.outcome,
            unresolvable = false,
        )
    }

    fun activeApi(): RunvoriApi? = active?.api

    /** Record a submitted write before its response arrives. */
    fun recordOperation(hostId: String, requestId: String, kind: String,
                        resourceId: String?) {
        cache.storeOperation(hostId, OperationReceipt(
            requestId = requestId, status = "accepted",
            resourceId = resourceId, outcome = null), kind)
    }

    fun cacheOperation(hostId: String, receipt: OperationReceipt, kind: String) {
        cache.storeOperation(hostId, receipt, kind)
    }

    private data class ActiveConnection(
        val credential: StoredCredential,
        val api: RunvoriApi,
    )

    private companion object {
        const val MAX_PAGES = 20
    }
}

data class SessionSync(
    val hostId: String,
    val sessionId: String,
    val snapshot: SessionSnapshot,
    val events: List<SessionEvent>,
    val continuousSeq: Long,
    val hasGap: Boolean,
    val latestSeq: Long,
)

/** A read-only projection assembled only from the on-disk cache. */
data class CachedSessionView(
    val session: CachedSession,
    val events: List<SessionEvent>,
    val continuousSeq: Long,
    val hasGap: Boolean,
)

sealed interface ConnectionState {
    data class Online(val credential: StoredCredential) : ConnectionState
    data object NotPaired : ConnectionState
    data object Unauthorized : ConnectionState
    data object Incompatible : ConnectionState
    data object IdentityChanged : ConnectionState
    data class Offline(val reason: String) : ConnectionState
}

class ConnectionNotReady : Exception("host_not_connected")
