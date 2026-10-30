package dev.runvori.app.data

import android.content.Context

/**
 * A small durable pointer to the screen the user was reading.
 *
 * Only opaque ids and the screen name are stored; content stays in the cache
 * and is re-read from the Host. The host id prevents a route from a former
 * pairing from being restored into a new Host identity.
 */
data class NavigationRoute(
    val hostId: String,
    val screen: String,
    val sessionId: String?,
    val approvalId: String?,
    val runId: String?,
)

class NavigationRouteStore(context: Context) {
    private val preferences = context.getSharedPreferences(
        PREFERENCES, Context.MODE_PRIVATE)

    fun save(route: NavigationRoute) {
        // This is written on navigation, not per poll. commit() makes the
        // pointer durable before the process can be killed.
        preferences.edit()
            .putString(KEY_HOST, route.hostId)
            .putString(KEY_SCREEN, route.screen)
            .putString(KEY_SESSION, route.sessionId)
            .putString(KEY_APPROVAL, route.approvalId)
            .putString(KEY_RUN, route.runId)
            .commit()
    }

    fun load(): NavigationRoute? {
        val hostId = preferences.getString(KEY_HOST, null) ?: return null
        val screen = preferences.getString(KEY_SCREEN, null) ?: return null
        return NavigationRoute(
            hostId = hostId,
            screen = screen,
            sessionId = preferences.getString(KEY_SESSION, null),
            approvalId = preferences.getString(KEY_APPROVAL, null),
            runId = preferences.getString(KEY_RUN, null),
        )
    }

    fun clear() {
        preferences.edit().clear().commit()
    }

    private companion object {
        const val PREFERENCES = "runvori.navigation"
        const val KEY_HOST = "host_id"
        const val KEY_SCREEN = "screen"
        const val KEY_SESSION = "session_id"
        const val KEY_APPROVAL = "approval_id"
        const val KEY_RUN = "run_id"
    }
}
