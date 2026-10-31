package dev.runvori.app.domain

/**
 * Where the reader is in a timeline that keeps growing.
 *
 * Two modes, and only the user moves between them. While [following], every
 * change keeps the newest item in view, including an item that grows in place
 * (streaming text, command output). Once the user scrolls away from the end
 * the reader is browsing: new items must not move what is on screen, and
 * [unseen] counts the items that arrived below the last one the user saw.
 * Scrolling back to the end, or "jump to latest", resumes following.
 *
 * Positions are identified by stable item keys, never by index or by token
 * count, so a page of older items loaded above the view does not count as new
 * and does not shift the anchor. Nothing here depends on the UI toolkit; the
 * timeline screen binds it to the list state.
 */
data class ReadingState(
    val following: Boolean = true,
    /** Newest item key the user has had on screen. */
    val lastSeenKey: String? = null,
    val unseen: Int = 0,
)

object ReadingModel {

    /** The projected items changed (new page, new item, item grew). */
    fun onItems(state: ReadingState, keys: List<String>): ReadingState {
        if (keys.isEmpty()) return state.copy(unseen = 0)
        if (state.following) {
            return state.copy(lastSeenKey = keys.last(), unseen = 0)
        }
        val seen = state.lastSeenKey?.let(keys::indexOf) ?: -1
        // The anchor vanished (another session, a rebuilt projection): there
        // is nothing left to stay put for, so return to the newest item.
        if (seen < 0) return ReadingState(following = true, lastSeenKey = keys.last())
        return state.copy(unseen = keys.lastIndex - seen)
    }

    /**
     * The user finished a scroll gesture. [lastVisibleKey] is the lowest item
     * on screen; [atEnd] says whether the end of the list is visible.
     */
    fun onUserScrolled(
        state: ReadingState,
        keys: List<String>,
        lastVisibleKey: String?,
        atEnd: Boolean,
    ): ReadingState {
        if (keys.isEmpty() || atEnd) return jumpToLatest(keys)
        val previous = state.lastSeenKey?.let(keys::indexOf) ?: -1
        val visible = lastVisibleKey?.let(keys::indexOf) ?: -1
        val seen = maxOf(previous, visible)
        return ReadingState(
            following = false,
            lastSeenKey = if (seen >= 0) keys[seen] else state.lastSeenKey,
            unseen = if (seen >= 0) keys.lastIndex - seen else 0,
        )
    }

    fun jumpToLatest(keys: List<String>): ReadingState =
        ReadingState(following = true, lastSeenKey = keys.lastOrNull())

    /**
     * Whether the end of the list is on screen. [tolerancePx] absorbs rounding
     * from item spacing so a list resting at the bottom counts as the end.
     */
    fun isAtEnd(
        lastVisibleIndex: Int,
        totalItems: Int,
        lastVisibleEndPx: Int,
        viewportEndPx: Int,
        tolerancePx: Int = 8,
    ): Boolean =
        totalItems == 0 ||
            (lastVisibleIndex == totalItems - 1 &&
                lastVisibleEndPx <= viewportEndPx + tolerancePx)
}
