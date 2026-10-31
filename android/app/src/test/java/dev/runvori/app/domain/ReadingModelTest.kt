package dev.runvori.app.domain

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ReadingModelTest {

    private fun keys(vararg k: String) = k.toList()

    @Test
    fun `following tracks the newest item and never counts unseen`() {
        var state = ReadingState()
        state = ReadingModel.onItems(state, keys("a", "b"))
        state = ReadingModel.onItems(state, keys("a", "b", "c", "d"))
        assertTrue(state.following)
        assertEquals("d", state.lastSeenKey)
        assertEquals(0, state.unseen)
    }

    @Test
    fun `scrolling away from the end switches to browsing`() {
        val list = keys("a", "b", "c", "d")
        val state = ReadingModel.onUserScrolled(
            ReadingState(lastSeenKey = "d"), list, lastVisibleKey = "b", atEnd = false)
        assertFalse(state.following)
        // The user already had "d" on screen before scrolling up.
        assertEquals("d", state.lastSeenKey)
        assertEquals(0, state.unseen)
    }

    @Test
    fun `items arriving while browsing are counted below the last seen item`() {
        var state = ReadingState(following = false, lastSeenKey = "b")
        state = ReadingModel.onItems(state, keys("a", "b", "c", "d", "e"))
        assertFalse(state.following)
        assertEquals("b", state.lastSeenKey)
        assertEquals(3, state.unseen)
    }

    @Test
    fun `an older page loaded above the view is not new`() {
        var state = ReadingState(following = false, lastSeenKey = "c")
        state = ReadingModel.onItems(state, keys("a", "b", "c"))
        state = ReadingModel.onItems(state, keys("x", "y", "a", "b", "c"))
        assertFalse(state.following)
        assertEquals(0, state.unseen)
    }

    @Test
    fun `an item growing in place is not new`() {
        // A command keeps its key while its output grows.
        var state = ReadingState(following = false, lastSeenKey = "cmd")
        state = ReadingModel.onItems(state, keys("run", "cmd"))
        state = ReadingModel.onItems(state, keys("run", "cmd"))
        assertEquals(0, state.unseen)
    }

    @Test
    fun `scrolling down part of the way reduces unseen`() {
        val list = keys("a", "b", "c", "d", "e")
        val state = ReadingModel.onUserScrolled(
            ReadingState(following = false, lastSeenKey = "b", unseen = 3),
            list, lastVisibleKey = "d", atEnd = false)
        assertFalse(state.following)
        assertEquals("d", state.lastSeenKey)
        assertEquals(1, state.unseen)
    }

    @Test
    fun `reaching the end resumes following`() {
        val list = keys("a", "b", "c")
        val state = ReadingModel.onUserScrolled(
            ReadingState(following = false, lastSeenKey = "a", unseen = 2),
            list, lastVisibleKey = "c", atEnd = true)
        assertTrue(state.following)
        assertEquals("c", state.lastSeenKey)
        assertEquals(0, state.unseen)
    }

    @Test
    fun `jump to latest resumes following`() {
        val state = ReadingModel.jumpToLatest(keys("a", "b", "c"))
        assertTrue(state.following)
        assertEquals("c", state.lastSeenKey)
        assertEquals(0, state.unseen)
    }

    @Test
    fun `a vanished anchor returns to following instead of guessing`() {
        val state = ReadingModel.onItems(
            ReadingState(following = false, lastSeenKey = "gone", unseen = 4),
            keys("p", "q"))
        assertTrue(state.following)
        assertEquals("q", state.lastSeenKey)
        assertEquals(0, state.unseen)
    }

    @Test
    fun `an empty timeline has nothing unseen`() {
        val state = ReadingModel.onItems(
            ReadingState(following = false, lastSeenKey = "a", unseen = 2), emptyList())
        assertEquals(0, state.unseen)
    }

    @Test
    fun `end detection tolerates spacing rounding but not a hidden last item`() {
        assertTrue(ReadingModel.isAtEnd(9, 10, lastVisibleEndPx = 1004, viewportEndPx = 1000))
        assertFalse(ReadingModel.isAtEnd(9, 10, lastVisibleEndPx = 1200, viewportEndPx = 1000))
        assertFalse(ReadingModel.isAtEnd(8, 10, lastVisibleEndPx = 1000, viewportEndPx = 1000))
        assertTrue(ReadingModel.isAtEnd(-1, 0, lastVisibleEndPx = 0, viewportEndPx = 1000))
    }
}
