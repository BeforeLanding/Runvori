package dev.runvori.app

import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import dev.runvori.app.ui.Tag
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * A real Activity rotation around a pending approval.
 *
 * `DeviceAcceptanceTest` simulates process loss with a new view model. This
 * class instead uses the production Activity, its ViewModelProvider and its
 * lifecycle observer, and asks Android to recreate the Activity. The approval
 * must stay on screen and remain actionable, with no second decision.
 */
@RunWith(AndroidJUnit4::class)
class LifecycleRotationAcceptanceTest {

    @get:Rule
    val compose = createAndroidComposeRule<MainActivity>()

    private fun exists(tag: String): Boolean =
        compose.onAllNodesWithTag(tag).fetchSemanticsNodes().isNotEmpty()

    private fun waitTag(tag: String, timeoutMs: Long = 30_000) {
        compose.waitUntil(timeoutMs) { exists(tag) }
    }

    private fun waitAnyTag(vararg tags: String, timeoutMs: Long = 30_000) {
        compose.waitUntil(timeoutMs) { tags.any(::exists) }
    }

    private fun textOf(tag: String): String? =
        compose.onAllNodesWithTag(tag).fetchSemanticsNodes()
            .firstOrNull()?.config?.getOrNull(SemanticsProperties.Text)
            ?.joinToString("") { it.text }

    private fun pendingTags(): List<String> =
        compose.onAllNodes(SemanticsMatcher("pending approval card") {
            it.config.getOrNull(SemanticsProperties.TestTag)
                ?.startsWith(Tag.PENDING_APPROVAL) == true
        }).fetchSemanticsNodes().mapNotNull {
            it.config.getOrNull(SemanticsProperties.TestTag)
        }

    @Test
    fun t_approval_survives_activity_rotation() {
        // The previous scenario may have left a durable route on a deeper
        // screen. Return to the session list first; the rotation below is the
        // state transition under test.
        waitAnyTag(Tag.sessionRow("devsess0"), Tag.BACK,
            Tag.APPROVAL_BACK, Tag.RESULTS_BACK, Tag.PAIRING_PARSE)
        if (exists(Tag.PAIRING_PARSE)) {
            throw AssertionError("this scenario needs a prior pairing")
        }
        if (exists(Tag.APPROVAL_BACK)) {
            compose.onNodeWithTag(Tag.APPROVAL_BACK).performClick()
            waitAnyTag(Tag.sessionRow("devsess0"), Tag.BACK)
        }
        if (exists(Tag.RESULTS_BACK)) {
            compose.onNodeWithTag(Tag.RESULTS_BACK).performClick()
            waitAnyTag(Tag.sessionRow("devsess0"), Tag.BACK)
        }
        if (exists(Tag.BACK)) {
            compose.onNodeWithTag(Tag.BACK).performClick()
        }
        waitTag(Tag.sessionRow("devsess0"))
        compose.onNodeWithTag(Tag.sessionRow("devsess0")).performClick()
        waitTag(Tag.COMPOSER)
        val pendingBefore = pendingTags().toSet()
        compose.onNodeWithTag(Tag.COMPOSER)
            .performTextInput("please change a file [file-change]")
        compose.onNodeWithTag(Tag.SEND_RUN).performClick()
        // Wait for an approval id that was not on screen before the run. A
        // stale pending card from an earlier scenario may still be projected
        // until the next sync; a count-only check would be ambiguous.
        var refusal: String? = null
        var newPending: String? = null
        try {
            compose.waitUntil(60_000) {
                val error = textOf(Tag.ERROR)
                if (error != null) {
                    refusal = error
                    return@waitUntil true
                }
                newPending = pendingTags().firstOrNull {
                    it !in pendingBefore
                }
                newPending != null
            }
        } catch (error: androidx.compose.ui.test.ComposeTimeoutException) {
            throw AssertionError(
                "no new pending approval; before=$pendingBefore " +
                    "now=" + pendingTags() +
                    " error=" + textOf(Tag.ERROR) +
                    " notice=" + textOf(Tag.NOTICE) +
                    " status=" + textOf(Tag.RUN_STATUS))
        }
        if (refusal != null) {
            throw AssertionError("the run was refused: $refusal")
        }
        compose.onNodeWithTag(newPending!!).performClick()
        waitTag(Tag.APPROVAL_STATUS)
        compose.onNodeWithTag(Tag.DENY).assertIsEnabled()
        compose.onNodeWithTag(Tag.ALLOW).assertIsNotEnabled()

        // Configuration change: Android destroys and recreates the Activity.
        compose.activityRule.scenario.recreate()

        waitTag(Tag.APPROVAL_STATUS)
        compose.onNodeWithTag(Tag.APPROVAL_STATUS).assertIsDisplayed()
        compose.onNodeWithTag(Tag.DENY).assertIsEnabled()
        compose.onNodeWithTag(Tag.ALLOW).assertIsNotEnabled()
        compose.onNodeWithTag(Tag.DENY).performClick()
        waitTag(Tag.DECISION_NOTICE)
        compose.onNodeWithTag(Tag.APPROVAL_BACK).performClick()
        waitTag(Tag.SEND_RUN, timeoutMs = 45_000)
    }
}
