package dev.runvori.app

import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.runvori.app.data.HostSession
import dev.runvori.app.data.KeystoreCredentialStore
import dev.runvori.app.data.RunvoriCache
import dev.runvori.app.domain.PairingInfo
import dev.runvori.app.domain.TimelineItem
import dev.runvori.app.ui.Actions
import dev.runvori.app.ui.RunvoriApp
import dev.runvori.app.ui.RunvoriViewModel
import dev.runvori.app.ui.Tag
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Real APK → pinned HTTPS → real HostRuntime → synthetic (or real) upstream.
 *
 * `scripts/accept_device.py` provisions the Host, workspace, sessions and a
 * pairing payload, and the emulator dials the Host through its host alias.
 * Each scenario below drives the product's own session/view-model and renders
 * the product's own composables; no test shortcut bypasses TLS, the API or the
 * cache.
 *
 * The orchestrator runs scenarios as separate instrumentation invocations so
 * it can act on the Host between them (revoke a device; stop the Host).
 */
@RunWith(AndroidJUnit4::class)
class DeviceAcceptanceTest {

    @get:Rule
    val compose = createComposeRule()

    private lateinit var cache: RunvoriCache
    private lateinit var store: KeystoreCredentialStore
    private lateinit var session: HostSession
    private lateinit var viewModel: RunvoriViewModel

    /** `am instrument -e` mangles quotes, so the payload arrives base64. */
    private val port: String
        get() = InstrumentationRegistry.getArguments().getString("host_port")
            .orEmpty()

    private val pairingJson: String
        get() {
            val encoded = InstrumentationRegistry.getArguments()
                .getString("pairing_info_b64").orEmpty()
            if (encoded.isEmpty()) return ""
            return String(android.util.Base64.decode(encoded, android.util.Base64.NO_WRAP),
                Charsets.UTF_8)
        }

    /** Real Codex mode uses its own prompt and longer model latencies. */
    private val realMode: Boolean
        get() = InstrumentationRegistry.getArguments()
            .getString("real_mode") == "1"

    private fun runPrompt(synthetic: String): String {
        if (!realMode) return synthetic
        val encoded = InstrumentationRegistry.getArguments()
            .getString("run_prompt_b64").orEmpty()
        check(encoded.isNotEmpty()) { "run_prompt_b64 is required in real mode" }
        return String(android.util.Base64.decode(
            encoded, android.util.Base64.NO_WRAP), Charsets.UTF_8)
    }

    private val acceptTimeoutMs: Long
        get() = if (realMode) 60_000 else 30_000

    private val approvalTimeoutMs: Long
        get() = if (realMode) 240_000 else 30_000

    private val terminalTimeoutMs: Long
        get() = if (realMode) 240_000 else 45_000

    @Before
    fun setUp() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        cache = RunvoriCache(context)
        store = KeystoreCredentialStore(context)
    }

    @After
    fun tearDown() {
        if (this::viewModel.isInitialized) viewModel.close()
        cache.close()
    }

    // -- harness ---------------------------------------------------------
    private fun freshSession(resetCredential: Boolean = false): HostSession {
        if (resetCredential) {
            store.clear()
            cache.clearAll()
        }
        return HostSession(store, cache, "acceptance-device")
    }

    /** The view model the rendered UI follows; swapped to simulate process loss. */
    private val rendered = androidx.compose.runtime.mutableStateOf<RunvoriViewModel?>(null)

    private fun startViewModel(resetCredential: Boolean = false) {
        session = freshSession(resetCredential)
        viewModel = RunvoriViewModel(session)
        renderOnce()
        viewModel.start()
        waitFor("initial connection") {
            viewModel.state.value.connection != "正在连接"
        }
    }

    /**
     * Attach a newly constructed view model to the already-rendered UI.
     *
     * A process loss is "the old object is gone and a fresh one starts from
     * the on-disk cache". Compose may only be given content once per test, so
     * the composable reads whichever view model is current instead of being
     * installed twice.
     */
    private fun attach(vm: RunvoriViewModel) {
        viewModel = vm
        rendered.value = vm
        compose.waitForIdle()
    }

    /** Render the product UI against the product view model. */
    private fun renderOnce() {
        rendered.value = viewModel
        compose.setContent {
            val current = rendered.value ?: return@setContent
            val state by current.state.collectAsState()
            RunvoriApp(state, actionsFor(current))
        }
    }

    private fun actionsFor(vm: RunvoriViewModel) = Actions(
        beginPairing = vm::beginPairing,
        confirmPairing = vm::confirmPairing,
        cancelPairing = vm::cancelPairing,
        refreshSessions = vm::refreshSessions,
        openSession = vm::openSession,
        backToSessions = vm::backToSessions,
        loadEarlier = vm::loadEarlier,
        openApproval = vm::openApproval,
        closeApproval = vm::closeApproval,
        decide = vm::decide,
        startRun = vm::startRun,
        updateComposer = vm::updateComposer,
        interrupt = vm::interrupt,
        confirmInterrupt = vm::confirmInterrupt,
        cancelInterrupt = vm::cancelInterrupt,
        openResults = vm::openResults,
        closeResults = vm::closeResults,
        refreshResults = vm::refreshResults,
        loadMoreCommands = vm::loadMoreCommands,
        dismissError = vm::dismissError,
    )

    private fun waitFor(label: String, timeoutMs: Long = 25_000,
                        condition: () -> Boolean) {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            if (condition()) return
            Thread.sleep(100)
        }
        throw AssertionError("timed out waiting for $label; state=" +
            viewModel.state.value.let {
                "screen=${it.screen} connection=${it.connection} " +
                    "error=${it.error} notice=${it.notice} gap=${it.syncGap}"
            })
    }

    private fun pair() {
        assertTrue("pairing_info_b64 argument is required", pairingJson.isNotEmpty())
        viewModel.beginPairing(pairingJson)
        waitFor("pairing review") {
            viewModel.state.value.pairingReview != null ||
                viewModel.state.value.pairingError != null
        }
        assertNull(viewModel.state.value.pairingError)
        compose.onNodeWithTag(Tag.PAIRING_REVIEW).assertIsDisplayed()
        compose.onNodeWithTag(Tag.PAIRING_PIN).assertIsDisplayed()
        compose.onNodeWithTag(Tag.PAIRING_CONFIRM).performClick()
        waitFor("pairing call to finish", timeoutMs = 20_000) {
            store.load() != null || viewModel.state.value.pairingError != null
        }
        val error = viewModel.state.value.pairingError
        assertNull("pairing failed: " + error, error)
        val stored = store.load()
        assertNotNull("pairing must store a credential", stored)
        assertEquals("the stored URL must keep the explicit port",
            port, stored!!.httpsUrl.substringAfterLast(':'))
        waitFor("paired sessions", timeoutMs = 30_000) {
            viewModel.state.value.screen.name == "Sessions" &&
                viewModel.state.value.connection == "已连接" &&
                viewModel.state.value.sessions.isNotEmpty()
        }
    }

    private fun openSession(sessionId: String) {
        viewModel.openSession(sessionId)
        waitFor("timeline of $sessionId", timeoutMs = 30_000) {
            viewModel.state.value.screen.name == "Timeline" &&
                viewModel.state.value.connection == "已连接" &&
                !viewModel.state.value.loading
        }
    }

    private fun startRun(input: String) {
        viewModel.updateComposer(input)
        viewModel.startRun(input)
        // A run may legitimately be accepted and finish before the next sync
        // (a turn that closes its own request does), so acceptance is proven
        // by the product's own receipt notice, not only by `activeRun`.
        waitFor("accepted run", timeoutMs = acceptTimeoutMs) {
            viewModel.state.value.activeRun != null ||
                viewModel.state.value.error != null ||
                viewModel.state.value.notice == "新回合已受理"
        }
        val failure = viewModel.state.value.error
        assertNull("the run was refused: " + failure, failure)
    }

    private fun openResults() {
        viewModel.openResults()
        waitFor("results", timeoutMs = 30_000) {
            viewModel.state.value.screen.name == "Results" &&
                !viewModel.state.value.resultsLoading &&
                viewModel.state.value.results != null
        }
        compose.onNodeWithTag(Tag.RESULT_RUN_STATUS).assertIsDisplayed()
        compose.onNodeWithTag(Tag.RESULT_VERIFICATION_SUMMARY).assertIsDisplayed()
    }

    private fun openFirstApproval(): TimelineItem.Approval {
        // The newest pending request, not the oldest entry: a session keeps
        // approvals from earlier scenarios, and those are already resolved.
        waitFor("approval in timeline", timeoutMs = approvalTimeoutMs) {
            viewModel.state.value.timeline.any {
                it is TimelineItem.Approval && it.status == "pending"
            }
        }
        val approval = viewModel.state.value.timeline
            .filterIsInstance<TimelineItem.Approval>()
            .last { it.status == "pending" }
        viewModel.openApproval(approval.approvalId)
        waitFor("approval detail", timeoutMs = 20_000) {
            viewModel.state.value.screen.name == "Approval" &&
                viewModel.state.value.approval != null
        }
        return approval
    }

    // -- scenarios -------------------------------------------------------

    @Test
    fun s_credential_store_round_trips_and_needs_the_keystore() {
        store.clear()
        assertNull(store.load())
        val credential = dev.runvori.app.data.StoredCredential(
            hostId = "host_" + "a".repeat(32),
            hostName = "Runvori Host 测试",
            httpsUrl = "https://localhost:8443",
            spkiSha256 = "f".repeat(64),
            deviceId = "device_1",
            token = ByteArray(32) { (it + 1).toByte() },
        )
        store.save(credential)
        val restored = store.load()
        assertNotNull("a saved credential must load again", restored)
        assertEquals(credential.hostId, restored!!.hostId)
        assertEquals(credential.hostName, restored.hostName)
        assertEquals(credential.httpsUrl, restored.httpsUrl)
        assertEquals(credential.spkiSha256, restored.spkiSha256)
        assertEquals(credential.deviceId, restored.deviceId)
        assertTrue(credential.token.contentEquals(restored.token))
        // The stored bytes on disk must be ciphertext, not the token.
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val raw = context.getSharedPreferences("runvori.credentials", 0)
            .getString("credential", null)
        assertNotNull(raw)
        assertFalse("the token must not be stored in clear text",
            raw!!.contains(android.util.Base64.encodeToString(
                credential.token, android.util.Base64.NO_WRAP)))
        store.clear()
        assertNull(store.load())
    }

    @Test
    fun a_wrong_fingerprint_is_refused_before_any_request() {
        startViewModel(resetCredential = true)
        val info = PairingInfo.parse(pairingJson)
        val forged = pairingJson.replace(info.spkiSha256, "0".repeat(64))
        viewModel.beginPairing(forged)
        waitFor("forged review") { viewModel.state.value.pairingReview != null }
        compose.onNodeWithTag(Tag.PAIRING_CONFIRM).performClick()
        waitFor("pin refusal", timeoutMs = 30_000) {
            viewModel.state.value.pairingError != null
        }
        compose.onNodeWithTag(Tag.PAIRING_ERROR).assertIsDisplayed()
        val message = viewModel.state.value.pairingError.orEmpty()
        assertTrue("expected a TLS identity refusal, got: $message",
            message.contains("spki_pin_mismatch", ignoreCase = true) ||
                message.contains("certificate", ignoreCase = true) ||
                message.contains("配对失败", ignoreCase = true))
        assertNull("a refused pairing must not store credentials", store.load())
        // The reviewed payload stays on screen so the user can re-read the pin
        // and compare again; only the credential is refused.
        assertNotNull(viewModel.state.value.pairingReview)
    }

    @Test
    fun b_full_loop_lists_reads_approves_and_reports() {
        startViewModel(resetCredential = true)
        pair()
        val hostId = viewModel.state.value.hostId
        assertTrue(viewModel.state.value.sessions.any { it.sessionId == "devsess0" })
        assertTrue(viewModel.state.value.sessions.any { it.sessionId == "devsess1" })
        compose.onNodeWithTag(Tag.sessionRow("devsess0")).assertIsDisplayed()

        viewModel.openSession("devsess0")
        waitFor("timeline") {
            viewModel.state.value.screen.name == "Timeline" &&
                !viewModel.state.value.loading
        }
        // Composer is available because no run is active yet. `writable` is
        // false while a gap or a failed sync is outstanding, so waiting on it
        // reports the real reason instead of a bare "not displayed".
        waitFor("writable timeline") { viewModel.state.value.writable }
        compose.onNodeWithTag(Tag.SEND_RUN).assertIsDisplayed()
        startRun(runPrompt("please run the probe command"))
        // The notice is only present while the turn is still active; the run
        // being accepted is already asserted by `startRun`.
        if (viewModel.state.value.activeRun != null) {
            compose.onNodeWithTag(Tag.ACTIVE_NOTICE).assertIsDisplayed()
        }

        val approval = openFirstApproval()
        compose.onNodeWithTag(Tag.APPROVAL_COMMAND).assertIsDisplayed()
        compose.onNodeWithTag(Tag.APPROVAL_CWD).assertIsDisplayed()
        val detail = viewModel.state.value.approval!!
        assertEquals("complete", detail.detailsStatus)
        assertTrue("the approval must carry a command",
            !detail.command.isNullOrBlank())
        assertTrue(detail.cwdInsideWorkspace)
        assertTrue(detail.allowedDecisions.contains("allow_once"))
        assertTrue(detail.allowedDecisions.contains("deny"))
        if (!realMode) {
            assertTrue("the displayed command must be the exact upstream string",
                detail.command!!.contains("python --version"))
        }

        // Double click: the first submission must disable both buttons, and a
        // repeated call must not create a second operation.
        compose.onNodeWithTag(Tag.ALLOW).performClick()
        compose.onNodeWithTag(Tag.ALLOW).assertIsNotEnabled()
        compose.onNodeWithTag(Tag.DENY).assertIsNotEnabled()
        viewModel.decide("allow_once")
        Thread.sleep(500)
        val decisions = cache.operations(hostId).filter { it.kind == "approval" }
        assertEquals("exactly one decision operation", 1, decisions.size)

        waitFor("approval resolved", timeoutMs = terminalTimeoutMs) {
            val current = viewModel.state.value.approval
            current != null && (
                if (realMode) current.status == "resolved"
                else current.status in setOf("resolved", "expired"))
        }
        waitFor("run terminal", timeoutMs = terminalTimeoutMs) {
            viewModel.state.value.activeRun == null
        }
        val timeline = viewModel.state.value.timeline
        if (realMode) {
            val terminal = timeline.filterIsInstance<TimelineItem.RunEvent>()
                .lastOrNull {
                    it.type in setOf("run.completed", "run.failed", "run.interrupted")
                }
            assertNotNull("the real run must have a terminal event", terminal)
            assertEquals("the real run must complete", "run.completed", terminal!!.type)
        }
        val command = timeline.filterIsInstance<TimelineItem.Command>().firstOrNull()
        assertNotNull("the executed command must be on the timeline", command)
        if (realMode) {
            assertEquals("the real command must exit successfully",
                0, command!!.exitCode)
            assertTrue("the real command must produce output",
                command.output.isNotBlank())
        } else {
            assertTrue("command output must be visible",
                command!!.output.contains("3.12"))
            assertEquals(0, command.exitCode)
        }
        val message = timeline.filterIsInstance<TimelineItem.AgentMessage>()
            .firstOrNull()
        assertNotNull("streamed agent text must be visible", message)
        if (realMode) {
            assertTrue("the real model must produce an agent message",
                message!!.text.isNotBlank())
        } else {
            assertTrue(message!!.text.contains("合成完成"))
        }

        // A decision was recorded once, and the operation reached a final state.
        waitFor("decision operation finished", timeoutMs = terminalTimeoutMs) {
            cache.operations(hostId).any {
                it.kind == "approval" && it.status in setOf(
                    "confirmed", "failed", "unknown")
            }
        }
        val operation = cache.operations(hostId)
            .first { it.kind == "approval" }
        if (realMode) {
            assertEquals("the real decision operation must be confirmed",
                "confirmed", operation.status)
        } else {
            assertTrue(operation.status in setOf("confirmed", "failed", "unknown"))
        }

        // The session can take a new run afterwards; the old result stays.
        viewModel.closeApproval()
        waitFor("composer re-opened") {
            viewModel.state.value.activeRun == null && viewModel.state.value.writable
        }
        compose.onNodeWithTag(Tag.SEND_RUN).assertIsDisplayed()
        if (!realMode) {
            openResults()
            val results = viewModel.state.value.results!!
            assertEquals("completed", results.status)
            assertTrue(results.terminal)
            assertTrue("the workspace verification is configured",
                results.verification.configured)
            assertEquals("passed", results.verification.summary)
            viewModel.closeResults()
            waitFor("timeline after results") {
                viewModel.state.value.screen.name == "Timeline"
            }
        }
        viewModel.backToSessions()
        waitFor("sessions screen") {
            viewModel.state.value.screen.name == "Sessions" &&
                !viewModel.state.value.loading
        }
        assertTrue(approval.approvalId.isNotEmpty())
    }

    @Test
    fun c_reconnect_after_process_loss_has_no_duplicates_or_gaps() {
        startViewModel(resetCredential = false)
        waitFor("stored credential is usable") {
            viewModel.state.value.connection != "正在连接"
        }
        assertTrue("this scenario needs a prior pairing",
            store.load() != null)
        waitFor("sessions from cache/host", timeoutMs = 30_000) {
            viewModel.state.value.screen.name == "Sessions" &&
                viewModel.state.value.sessions.isNotEmpty()
        }
        // Real mode reuses the history the core loop already wrote to
        // devsess0; it must not spend a second model turn just to make cache
        // entries. Synthetic mode keeps the deny turn on devsess1, which also
        // frees the Host's single active-run slot for the next scenario.
        val sessionId = if (realMode) "devsess0" else "devsess1"
        openSession(sessionId)
        val hostId = viewModel.state.value.hostId
        if (!realMode) {
            startRun("please run the probe command")
            val pending = openFirstApproval()
            viewModel.decide("deny")
            waitFor("run terminal", timeoutMs = terminalTimeoutMs) {
                viewModel.state.value.activeRun == null
            }
            viewModel.closeApproval()
        }
        val countBefore = cache.events(hostId, sessionId, limit = 100_000).size
        assertTrue("expected cached events", countBefore > 0)

        // Simulate process loss: drop the in-memory connection but keep the
        // on-disk cache; a fresh view model must resync from its cursor.
        viewModel.close()
        attach(RunvoriViewModel(HostSession(store, cache, "acceptance-device")))
        viewModel.start()
        waitFor("reconnect", timeoutMs = 30_000) {
            viewModel.state.value.screen.name == "Sessions" &&
                viewModel.state.value.connection == "已连接"
        }
        viewModel.openSession(sessionId)
        waitFor("resync", timeoutMs = 30_000) {
            viewModel.state.value.screen.name == "Timeline" &&
                !viewModel.state.value.loading &&
                viewModel.state.value.connection == "已连接"
        }
        val events = cache.events(hostId, sessionId, limit = 100_000)
        val seqs = events.map { it.seq }
        assertEquals("no duplicate seq after resync", seqs.size, seqs.toSet().size)
        assertEquals("the stored sequence is contiguous",
            (1..seqs.size).map { it.toLong() }, seqs)
        assertFalse(viewModel.state.value.syncGap)
        assertTrue("the terminal run survived the resync",
            events.any { it.type == "run.completed" || it.type == "run.interrupted" })
    }

    @Test
    fun d_expired_approval_cannot_be_allowed() {
        startViewModel(resetCredential = false)
        waitFor("connected") {
            viewModel.state.value.screen.name == "Sessions" &&
                viewModel.state.value.connection == "已连接"
        }
        openSession("devsess0")
        startRun("run then close the request [expire-request]")
        waitFor("expired approval on timeline", timeoutMs = 30_000) {
            viewModel.state.value.timeline.any {
                it is TimelineItem.Approval && it.status == "expired"
            }
        }
        val item = viewModel.state.value.timeline
            .filterIsInstance<TimelineItem.Approval>()
            .first { it.status == "expired" }
        viewModel.openApproval(item.approvalId)
        waitFor("approval detail") { viewModel.state.value.approval != null }
        val approval = viewModel.state.value.approval!!
        assertEquals("expired", approval.status)
        assertFalse("an expired approval must not offer allow", approval.canAllow)
        assertFalse("an expired approval must not offer deny", approval.canDeny)
        compose.onNodeWithTag(Tag.ALLOW).assertIsNotEnabled()
        compose.onNodeWithTag(Tag.DENY).assertIsNotEnabled()
        val refused = runCatching {
            session.api().decide(approval.approvalId, "expired-attempt",
                approval.version, "allow_once")
        }
        assertTrue("the Host must refuse a decision on an expired approval",
            refused.isFailure)
    }

    @Test
    fun i_results_show_completed_run_with_failed_verification() {
        startViewModel(resetCredential = false)
        waitFor("connected") {
            viewModel.state.value.screen.name == "Sessions" &&
                viewModel.state.value.connection == "已连接"
        }
        openSession("devsess0")
        startRun("please run the probe command [verify-fail]")
        val approval = openFirstApproval()
        assertTrue("the failing command must be the configured one",
            approval.approvalId.isNotEmpty())
        assertTrue("the review must name the failing command",
            viewModel.state.value.approval!!.command!!.contains("exit 7"))
        viewModel.decide("allow_once")
        waitFor("run terminal", timeoutMs = terminalTimeoutMs) {
            viewModel.state.value.activeRun == null
        }
        openResults()
        val results = viewModel.state.value.results!!
        assertEquals("the turn itself completed", "completed", results.status)
        assertTrue(results.terminal)
        assertTrue(results.verification.configured)
        assertEquals("the required command failed", "failed",
            results.verification.summary)
        val item = results.verification.items.first { it.key == "failure" }
        assertEquals("failed", item.status)
        assertTrue(item.exitCodePresent)
        assertEquals(7, item.exitCode)
        assertEquals("complete", item.outputState)
        assertTrue("the failing command must be listed",
            viewModel.state.value.commands.any {
                it.command?.contains("exit 7") == true &&
                    it.exitCodePresent && it.exitCode == 7
            })
        compose.onNodeWithTag(Tag.RESULT_RUN_STATUS).assertIsDisplayed()
        compose.onNodeWithTag(Tag.RESULT_VERIFICATION_SUMMARY).assertIsDisplayed()
        compose.onNodeWithTag(Tag.verificationItem("failure")).assertIsDisplayed()
        compose.onNodeWithTag(Tag.verificationExit("failure")).assertIsDisplayed()
        compose.onNodeWithTag(Tag.verificationOutput("failure"))
            .assertIsDisplayed()
    }

    @Test
    fun e_revoked_device_cannot_read() {
        startViewModel(resetCredential = false)
        waitFor("connect attempt finished") {
            viewModel.state.value.connection != "正在连接"
        }
        // The orchestrator revoked this device between the previous scenario
        // and this one; the stored token must now be refused.
        assertEquals("配对凭据已失效，请在电脑重新配对",
            viewModel.state.value.connection)
        assertTrue(store.load() != null)
        val failure = runCatching { session.api().host() }
        assertTrue("a revoked token must not authenticate", failure.isFailure)
        compose.onNodeWithTag(Tag.PAIRING_PARSE).assertIsDisplayed()
    }

    @Test
    fun f_insufficient_review_offers_only_deny() {
        startViewModel(resetCredential = false)
        waitFor("connected") {
            viewModel.state.value.screen.name == "Sessions" &&
                viewModel.state.value.connection == "已连接"
        }
        openSession("devsess1")
        startRun("please change a file [file-change]")
        val item = openFirstApproval()
        val approval = viewModel.state.value.approval!!
        assertEquals("insufficient", approval.detailsStatus)
        assertFalse(approval.canAllow)
        assertTrue(approval.canDeny)
        compose.onNodeWithTag(Tag.INSUFFICIENT).assertIsDisplayed()
        compose.onNodeWithTag(Tag.ALLOW).assertIsNotEnabled()
        compose.onNodeWithTag(Tag.DENY).assertIsDisplayed()
        assertEquals("deny", approval.allowedDecisions.single())
        assertTrue(item.approvalId.isNotEmpty())
        // Deny is the only offered decision; submitting it must close the turn
        // so the Host's single active-run slot is free again.
        compose.onNodeWithTag(Tag.DENY).performClick()
        waitFor("run terminal after deny", timeoutMs = 45_000) {
            viewModel.state.value.activeRun == null
        }
    }

    @Test
    fun h_interrupt_ends_the_active_run() {
        startViewModel(resetCredential = false)
        waitFor("connected") {
            viewModel.state.value.screen.name == "Sessions" &&
                viewModel.state.value.connection == "已连接"
        }
        openSession("devsess0")
        startRun("please run the probe command [interrupt]")
        waitFor("active run", timeoutMs = 45_000) {
            viewModel.state.value.activeRun != null
        }
        // Interrupt is a two-step product action: the first tap only opens a
        // confirmation, so an accidental tap cannot stop a healthy turn.
        compose.onNodeWithTag(Tag.INTERRUPT).performClick()
        assertTrue("interrupt must ask before acting",
            viewModel.state.value.interruptConfirm)
        viewModel.cancelInterrupt()
        assertFalse(viewModel.state.value.interruptConfirm)
        assertNotNull("cancelling must not stop the run",
            viewModel.state.value.activeRun)

        viewModel.interrupt()
        viewModel.confirmInterrupt()
        waitFor("run interrupted", timeoutMs = 45_000) {
            viewModel.state.value.activeRun == null
        }
        compose.onNodeWithTag(Tag.SEND_RUN).assertIsDisplayed()
    }

    @Test
    fun g_duplicate_operation_query_is_stable() {
        startViewModel(resetCredential = false)
        waitFor("connected") {
            viewModel.state.value.screen.name == "Sessions" &&
                viewModel.state.value.connection == "已连接"
        }
        val hostId = viewModel.state.value.hostId
        val operations = cache.operations(hostId)
        // Real mode must not pass vacuously: the core loop leaves a confirmed
        // operation, which is still a valid subject for a duplicate query.
        val selected = if (realMode) operations
            else operations.filter { it.status in setOf("accepted", "dispatching") }
        if (realMode) {
            assertTrue("expected at least one approval operation",
                selected.any { it.kind == "approval" })
        }
        for (operation in selected) {
            val first = session.refreshOperation(operation)
            val second = session.refreshOperation(first)
            assertEquals("querying the same request id is stable",
                first.status, second.status)
        }
    }
}