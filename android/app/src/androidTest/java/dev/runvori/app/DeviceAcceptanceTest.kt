package dev.runvori.app

import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.runvori.app.data.HostSession
import dev.runvori.app.data.KeystoreCredentialStore
import dev.runvori.app.data.NavigationRouteStore
import dev.runvori.app.data.RunArtifact
import dev.runvori.app.data.RunvoriCache
import dev.runvori.app.domain.PairingInfo
import dev.runvori.app.domain.ProtocolError
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
import java.security.MessageDigest

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

    private fun arg(name: String): String =
        InstrumentationRegistry.getArguments().getString(name).orEmpty()

    private fun requiredArg(name: String): String {
        val value = arg(name)
        check(value.isNotEmpty()) { "$name is required" }
        return value
    }

    private fun runPrompt(synthetic: String): String {
        if (!realMode) return synthetic
        val encoded = requiredArg("run_prompt_b64")
        return String(android.util.Base64.decode(
            encoded, android.util.Base64.NO_WRAP), Charsets.UTF_8)
    }

    /** Each real scenario carries its own prompt; none shares a marker set. */
    private fun realPrompt(name: String): String {
        check(realMode) { "$name is a real-mode scenario" }
        val encoded = requiredArg("run_prompt_${name}_b64")
        return String(android.util.Base64.decode(
            encoded, android.util.Base64.NO_WRAP), Charsets.UTF_8)
    }

    private fun sha256Hex(value: String): String =
        MessageDigest.getInstance("SHA-256")
            .digest(value.toByteArray(Charsets.UTF_8))
            .joinToString("") {
                (it.toInt() and 0xff).toString(16).padStart(2, '0')
            }

    private val acceptTimeoutMs: Long
        get() = if (realMode) 60_000 else 30_000

    private val approvalTimeoutMs: Long
        get() = if (realMode) 240_000 else 30_000

    private val terminalTimeoutMs: Long
        get() = if (realMode) 240_000 else 45_000

    /** The real command prints this before sleeping; it proves execution
     *  started, not merely that the approval was answered. */
    private val executionStartTimeoutMs: Long
        get() = if (realMode) 180_000 else 30_000

    @Before
    fun setUp() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        cache = RunvoriCache(context)
        store = KeystoreCredentialStore(context)
    }

    @After
    fun tearDown() {
        if (this::viewModel.isInitialized) {
            viewModel.pause()
            viewModel.close()
            // Let any cache read that was already running finish before the
            // SQLite connection pool is closed. A pending approval keeps a
            // sync in flight longer than the ordinary scenarios.
            Thread.sleep(500)
        }
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
        loadMoreArtifacts = vm::loadMoreArtifacts,
        openArtifact = vm::openArtifact,
        closeArtifact = vm::closeArtifact,
        loadMoreArtifact = vm::loadMoreArtifact,
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

    private fun openFirstArtifact(source: String): RunArtifact {
        waitFor("artifact list", timeoutMs = 30_000) {
            viewModel.state.value.screen.name == "Results" &&
                viewModel.state.value.artifacts.any {
                    it.source == source && it.contentAvailable
                }
        }
        val artifact = viewModel.state.value.artifacts.first {
            it.source == source && it.contentAvailable
        }
        viewModel.openArtifact(artifact)
        waitFor("artifact content", timeoutMs = 30_000) {
            viewModel.state.value.screen.name == "Artifact" &&
                !viewModel.state.value.artifactLoading &&
                (viewModel.state.value.artifactText.isNotEmpty() ||
                    artifact.sizeBytes == 0L ||
                    viewModel.state.value.artifactError != null)
        }
        return artifact
    }

    /**
     * Scenarios that run after the pairing bootstrap (or after `b`) reuse the
     * stored credential instead of consuming the one-shot pairing payload.
     */
    private fun startWithStoredCredential() {
        assertNotNull("this scenario needs a prior pairing", store.load())
        startViewModel(resetCredential = false)
        waitFor("connected", timeoutMs = 30_000) {
            viewModel.state.value.connection == "已连接"
        }
    }

    private lateinit var routes: NavigationRouteStore

    /** Start with a durable route store, as the production Activity does. */
    private fun startWithRoutes() {
        assertNotNull("this scenario needs a prior pairing", store.load())
        routes = NavigationRouteStore(
            InstrumentationRegistry.getInstrumentation().targetContext)
        routes.clear()
        session = freshSession(resetCredential = false)
        viewModel = RunvoriViewModel(session, routes)
        renderOnce()
        viewModel.start()
        waitFor("connected with routes", timeoutMs = 30_000) {
            viewModel.state.value.connection == "已连接"
        }
    }

    /** Replace the in-memory view model but keep the durable route pointer. */
    private fun restoreAfterProcessLoss() {
        viewModel.close()
        session = freshSession(resetCredential = false)
        attach(RunvoriViewModel(session, routes))
        viewModel.start()
    }

    private fun openArtifact(source: String, path: String? = null): RunArtifact {
        waitFor("artifact $source", timeoutMs = 30_000) {
            viewModel.state.value.screen.name == "Results" &&
                viewModel.state.value.artifacts.any {
                    it.source == source &&
                        (path == null || it.path == path) && it.contentAvailable
                }
        }
        val artifact = viewModel.state.value.artifacts.first {
            it.source == source && (path == null || it.path == path) &&
                it.contentAvailable
        }
        viewModel.openArtifact(artifact)
        waitFor("artifact content", timeoutMs = 30_000) {
            viewModel.state.value.screen.name == "Artifact" &&
                !viewModel.state.value.artifactLoading &&
                (viewModel.state.value.artifactText.isNotEmpty() ||
                    artifact.sizeBytes == 0L ||
                    viewModel.state.value.artifactError != null)
        }
        return artifact
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

    /** The terminal event for exactly the run this scenario interrupted. */
    private fun terminalFor(runId: String): TimelineItem.RunEvent? =
        viewModel.state.value.timeline
            .filterIsInstance<TimelineItem.RunEvent>()
            .lastOrNull {
                it.runId == runId && it.type in setOf(
                    "run.completed", "run.failed", "run.interrupted")
            }

    private fun terminalNameFor(runId: String): String =
        terminalFor(runId)?.type ?: "none"

    /** Wait for the real command's own output marker, or fail with the
     *  terminal that arrived instead. A fixed sleep never satisfies this. */
    private fun awaitExecutionStart(runId: String) {
        val deadline = System.currentTimeMillis() + executionStartTimeoutMs
        while (System.currentTimeMillis() < deadline) {
            val started = viewModel.state.value.timeline
                .filterIsInstance<TimelineItem.Command>()
                .any { it.output.contains("RUNVORI_SLEEP_STARTED") }
            if (started) return
            viewModel.state.value.error?.let {
                throw AssertionError("execution_start_refused: " + it)
            }
            val terminal = terminalFor(runId)
            if (terminal != null) {
                throw AssertionError(
                    "execution_start_not_observed; terminal=" + terminal.type)
            }
            Thread.sleep(100)
        }
        throw AssertionError("execution_start_timeout")
    }

    private fun interruptOperation(runId: String, timeoutMs: Long) {
        val hostId = viewModel.state.value.hostId
        waitFor("interrupt operation recorded", timeoutMs = timeoutMs) {
            cache.operations(hostId).any {
                it.kind == "interrupt" && it.resourceId == runId
            }
        }
        val cached = cache.operations(hostId).first {
            it.kind == "interrupt" && it.resourceId == runId
        }
        val refreshed = session.refreshOperation(cached)
        assertEquals("the interrupt operation must be confirmed",
            "confirmed", refreshed.status)
        assertEquals("the interrupt must have ended the run",
            "run_interrupted", refreshed.outcome)
    }

    /**
     * Open a real (or synthetic) approval, end the turn through the product
     * interrupt entry while the page stays open, and prove the old request is
     * invalid and cannot be decided behind the disabled UI.
     */
    private fun approvalPageInterruptExpiresApproval(
        sessionId: String, input: String,
    ) {
        startWithStoredCredential()
        openSession(sessionId)
        startRun(input)
        waitFor("active run identity", timeoutMs = acceptTimeoutMs) {
            viewModel.state.value.activeRun != null
        }
        val runId = viewModel.state.value.activeRun!!.runId
        val hostId = viewModel.state.value.hostId
        val approval = openFirstApproval()
        val approvalId = approval.approvalId
        val initialVersion = viewModel.state.value.approval!!.version
        assertEquals(0, cache.operations(hostId).count {
            it.kind == "approval" && it.resourceId == approvalId
        })
        assertEquals("the approval page must stay open",
            "Approval", viewModel.state.value.screen.name)

        // End the turn through the product interrupt entry while the approval
        // is still pending. The confirmation dialog is the same one the
        // timeline uses.
        compose.onNodeWithTag(Tag.INTERRUPT).performClick()
        assertTrue("interrupt must ask before acting",
            viewModel.state.value.interruptConfirm)
        compose.onNodeWithTag(Tag.CONFIRM_INTERRUPT).performClick()
        waitFor("run terminal after interrupt", timeoutMs = terminalTimeoutMs) {
            viewModel.state.value.activeRun == null
        }
        val terminal = terminalFor(runId)
        assertNotNull("the interrupted run must reach a terminal event",
            terminal)
        assertEquals(
            "a natural completion before the interrupt is a race, not success",
            "run.interrupted", terminal!!.type)

        waitFor("approval expired after turn termination",
            timeoutMs = terminalTimeoutMs) {
            val current = viewModel.state.value.approval
            current != null && current.approvalId == approvalId &&
                current.status == "expired"
        }
        val expired = viewModel.state.value.approval!!
        assertFalse("an expired approval must not offer allow", expired.canAllow)
        assertFalse("an expired approval must not offer deny", expired.canDeny)
        compose.onNodeWithTag(Tag.ALLOW).assertIsNotEnabled()
        compose.onNodeWithTag(Tag.DENY).assertIsNotEnabled()

        // Bypassing the disabled UI must still be refused by the Host, and the
        // refusal must not create or dispatch an approval operation.
        val refused = runCatching {
            session.api().decide(approvalId,
                "ra-bypass-" + System.currentTimeMillis(),
                if (expired.version > 0) expired.version else initialVersion,
                "allow_once")
        }
        assertTrue("the Host must refuse a decision on the old approval",
            refused.isFailure)
        val error = refused.exceptionOrNull()
        assertTrue("expected a protocol refusal, got: " + error,
            error is ProtocolError)
        assertEquals("approval_expired", (error as ProtocolError).code)
        assertEquals(
            "no approval decision may be recorded or dispatched",
            0, cache.operations(hostId).count {
                it.kind == "approval" && it.resourceId == approvalId
            })
        assertEquals("the approval page must remain visible",
            "Approval", viewModel.state.value.screen.name)
        assertTrue("the terminal must be for the same run",
            viewModel.state.value.timeline
                .filterIsInstance<TimelineItem.RunEvent>()
                .any { it.runId == runId && it.type == "run.interrupted" })
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
            val output = openFirstArtifact("command_execution")
            assertEquals("command_output", output.kind)
            compose.onNodeWithTag(Tag.ARTIFACT_CONTENT).assertIsDisplayed()
            assertTrue("the command output must come from the persisted artifact",
                viewModel.state.value.artifactText.contains("3.12"))
            viewModel.closeArtifact()
            waitFor("results after artifact") {
                viewModel.state.value.screen.name == "Results"
            }
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
    fun j_file_approval_complete_allows_and_shows_artifacts() {
        startViewModel(resetCredential = false)
        waitFor("connected") {
            viewModel.state.value.screen.name == "Sessions" &&
                viewModel.state.value.connection == "已连接"
        }
        openSession("devsess0")
        startRun("please change a file [file-change-complete]")
        openFirstApproval()
        val approval = viewModel.state.value.approval!!
        assertEquals("complete", approval.detailsStatus)
        assertTrue(approval.canAllow)
        assertTrue(approval.canDeny)
        assertTrue(approval.allowedDecisions.contains("allow_once"))
        assertTrue(approval.allowedDecisions.contains("deny"))
        val change = approval.changes.first()
        assertEquals("probe-artifact.txt", change.path)
        assertEquals("add", change.changeType)
        assertTrue("the diff must be visible before allowing",
            change.diff.orEmpty().contains("RUNVORI_ARTIFACT_OK"))
        compose.onNodeWithTag(Tag.ALLOW).performClick()
        waitFor("run terminal", timeoutMs = terminalTimeoutMs) {
            viewModel.state.value.activeRun == null
        }
        openResults()
        val proposal = openFirstArtifact("approval_proposal")
        assertEquals("file_diff", proposal.kind)
        assertEquals("add", proposal.operation)
        compose.onNodeWithTag(Tag.ARTIFACT_CONTENT).assertIsDisplayed()
        compose.onNodeWithTag(Tag.ARTIFACT_METADATA).assertIsDisplayed()
        assertTrue("the artifact must expose the persisted diff",
            viewModel.state.value.artifactText.contains("RUNVORI_ARTIFACT_OK"))
        assertTrue("the upstream report must also be listed",
            viewModel.state.value.artifacts.any {
                it.source == "upstream_report" && it.path == "probe-artifact.txt"
            })
    }

    @Test
    fun k_file_approval_denied_keeps_proposal_visible() {
        startViewModel(resetCredential = false)
        waitFor("connected") {
            viewModel.state.value.screen.name == "Sessions" &&
                viewModel.state.value.connection == "已连接"
        }
        openSession("devsess0")
        startRun("please change a file [file-change-deny]")
        openFirstApproval()
        val approval = viewModel.state.value.approval!!
        assertEquals("complete", approval.detailsStatus)
        assertTrue(approval.canDeny)
        compose.onNodeWithTag(Tag.DENY).performClick()
        waitFor("run terminal", timeoutMs = terminalTimeoutMs) {
            viewModel.state.value.activeRun == null
        }
        openResults()
        val proposal = openFirstArtifact("approval_proposal")
        assertEquals("probe-artifact.txt", proposal.path)
        compose.onNodeWithTag(Tag.ARTIFACT_CONTENT).assertIsDisplayed()
        assertTrue(viewModel.state.value.artifactText.contains("RUNVORI_ARTIFACT_OK"))
        assertTrue("a denied file change must remain inspectable",
            viewModel.state.value.artifacts.any {
                it.source == "approval_proposal" && it.path == "probe-artifact.txt"
            })
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
    fun p_pairing_bootstrap() {
        // Consumes the one-shot pairing payload so a standalone real/synthetic
        // scenario set can reuse the stored credential. No model turn.
        startViewModel(resetCredential = true)
        pair()
    }

    @Test
    fun n_file_approval_absolute_updated_after_open() {
        check(!realMode) { "n is a synthetic-only ordering scenario" }
        startWithStoredCredential()
        openSession("devsess0")
        startRun("please change a file [file-change-absolute-first]")
        openFirstApproval()
        val initial = viewModel.state.value.approval!!
        assertEquals("file_change", initial.kind)
        // The request arrives first; the review must be unusable until the
        // item update lands, even while this screen stays open.
        assertEquals("insufficient", initial.detailsStatus)
        assertFalse(initial.canAllow)
        compose.onNodeWithTag(Tag.INSUFFICIENT).assertIsDisplayed()
        compose.onNodeWithTag(Tag.ALLOW).assertIsNotEnabled()
        waitFor("review completes while open", timeoutMs = 60_000) {
            viewModel.state.value.screen.name == "Approval" &&
                viewModel.state.value.approval?.detailsStatus == "complete" &&
                viewModel.state.value.approval?.canAllow == true
        }
        val review = viewModel.state.value.approval!!
        assertEquals("probe-artifact.txt", review.changes.first().path)
        assertTrue("the updated diff must be visible",
            review.changes.first().diff.orEmpty().contains("RUNVORI_ARTIFACT_OK"))
        viewModel.decide("allow_once")
        waitFor("run terminal", timeoutMs = terminalTimeoutMs) {
            viewModel.state.value.activeRun == null
        }
        openResults()
        assertEquals("completed", viewModel.state.value.results!!.status)
        val proposal = openArtifact("approval_proposal", "probe-artifact.txt")
        assertEquals("proposed", proposal.attribution)
        assertTrue(viewModel.state.value.artifactText
            .contains("RUNVORI_ARTIFACT_OK"))
        viewModel.closeArtifact()
        waitFor("results after proposal") {
            viewModel.state.value.screen.name == "Results"
        }
        val report = openArtifact("upstream_report", "probe-artifact.txt")
        assertEquals("reported", report.attribution)
        assertTrue("the upstream report must be readable",
            viewModel.state.value.artifactText.contains("RUNVORI_ARTIFACT_OK"))
    }

    @Test
    fun l_real_file_approval_allow_shows_diff_and_report() {
        check(realMode) { "l is a real-mode scenario" }
        startWithStoredCredential()
        openSession("devsessallow")
        startRun(realPrompt("l"))
        openFirstApproval()
        waitFor("complete file review", timeoutMs = approvalTimeoutMs) {
            viewModel.state.value.approval?.detailsStatus == "complete" &&
                viewModel.state.value.approval?.canAllow == true &&
                viewModel.state.value.approval?.changes?.isNotEmpty() == true
        }
        val approval = viewModel.state.value.approval!!
        assertEquals("file_change", approval.kind)
        assertEquals("approval-target.txt", approval.changes.first().path)
        val diff = approval.changes.first().diff.orEmpty()
        assertTrue("the proposed old line must be visible",
            diff.contains("RUNVORI_APPROVAL_BEFORE"))
        assertTrue("the proposed new line must be visible",
            diff.contains("RUNVORI_APPROVAL_AFTER"))
        compose.onNodeWithTag(Tag.ALLOW).performClick()
        waitFor("run terminal", timeoutMs = terminalTimeoutMs) {
            viewModel.state.value.activeRun == null
        }
        openResults()
        assertEquals("completed", viewModel.state.value.results!!.status)
        val proposal = openArtifact("approval_proposal", "approval-target.txt")
        assertEquals("proposed", proposal.attribution)
        assertEquals("file_diff", proposal.kind)
        assertTrue(viewModel.state.value.artifactText
            .contains("RUNVORI_APPROVAL_AFTER"))
        viewModel.closeArtifact()
        waitFor("results after proposal") {
            viewModel.state.value.screen.name == "Results"
        }
        val report = openArtifact("upstream_report", "approval-target.txt")
        assertEquals("reported", report.attribution)
        assertTrue("the upstream completion report must be readable",
            viewModel.state.value.artifactText.contains("RUNVORI_APPROVAL_AFTER"))
        assertTrue("the two sources must stay distinct",
            proposal.artifactId != report.artifactId)
    }

    @Test
    fun m_real_file_approval_denied_keeps_proposal_visible() {
        check(realMode) { "m is a real-mode scenario" }
        startWithStoredCredential()
        openSession("devsessdeny")
        startRun(realPrompt("m"))
        openFirstApproval()
        waitFor("complete file review", timeoutMs = approvalTimeoutMs) {
            viewModel.state.value.approval?.detailsStatus == "complete" &&
                viewModel.state.value.approval?.canDeny == true &&
                viewModel.state.value.approval?.changes?.isNotEmpty() == true
        }
        val approval = viewModel.state.value.approval!!
        assertEquals("approval-target.txt", approval.changes.first().path)
        assertTrue("the proposed denied content must be visible",
            approval.changes.first().diff.orEmpty()
                .contains("RUNVORI_APPROVAL_DENIED"))
        compose.onNodeWithTag(Tag.DENY).performClick()
        waitFor("run terminal", timeoutMs = terminalTimeoutMs) {
            viewModel.state.value.activeRun == null
        }
        openResults()
        assertEquals("completed", viewModel.state.value.results!!.status)
        val proposal = openArtifact("approval_proposal", "approval-target.txt")
        assertEquals("proposed", proposal.attribution)
        assertEquals("a denied proposal must stay inspectable",
            "approval-target.txt", proposal.path)
        assertTrue(viewModel.state.value.artifactText
            .contains("RUNVORI_APPROVAL_DENIED"))
    }

    @Test
    fun v_real_verification_result_is_passed() {
        check(realMode) { "v is a real-mode scenario" }
        startWithStoredCredential()
        openSession("devsess0")
        startRun(realPrompt("v"))
        openFirstApproval()
        val approval = viewModel.state.value.approval!!
        assertEquals("command", approval.kind)
        assertTrue("the review must carry a command",
            !approval.command.isNullOrBlank())
        assertTrue("the command cwd must be inside the workspace",
            approval.cwdInsideWorkspace)
        assertTrue(approval.allowedDecisions.contains("allow_once"))
        compose.onNodeWithTag(Tag.ALLOW).performClick()
        waitFor("run terminal", timeoutMs = terminalTimeoutMs) {
            viewModel.state.value.activeRun == null
        }
        openResults()
        val results = viewModel.state.value.results!!
        assertEquals("completed", results.status)
        assertTrue(results.verification.configured)
        assertEquals("passed", results.verification.summary)
        val item = results.verification.items.first { it.key == "python" }
        assertEquals("passed", item.status)
        assertTrue(item.exitCodePresent)
        assertEquals(0, item.exitCode)
        assertTrue("the executed command must carry exit 0",
            viewModel.state.value.commands.any {
                it.exitCodePresent && it.exitCode == 0
            })
        compose.onNodeWithTag(Tag.RESULT_VERIFICATION_SUMMARY)
            .assertIsDisplayed()
        compose.onNodeWithTag(Tag.verificationItem("python"))
            .assertIsDisplayed()
    }

    @Test
    fun ri_execution_interrupt_has_confirmed_terminal_and_released_slot() {
        check(realMode) { "ri is a real-mode scenario" }
        startWithStoredCredential()
        openSession("devsess0")
        startRun(realPrompt("ri"))
        waitFor("active run identity", timeoutMs = acceptTimeoutMs) {
            viewModel.state.value.activeRun != null
        }
        val runId = viewModel.state.value.activeRun!!.runId
        val hostId = viewModel.state.value.hostId

        openFirstApproval()
        val approval = viewModel.state.value.approval!!
        assertEquals("complete", approval.detailsStatus)
        assertTrue(approval.cwdInsideWorkspace)
        compose.onNodeWithTag(Tag.ALLOW).performClick()
        // The receipt proves only that the decision was submitted. Execution
        // starting is proven by the command's own output below.
        waitFor("approval decision submitted", timeoutMs = acceptTimeoutMs) {
            cache.operations(hostId).any {
                it.kind == "approval" && it.resourceId == approval.approvalId
            }
        }
        viewModel.closeApproval()
        waitFor("timeline after approval", timeoutMs = 30_000) {
            viewModel.state.value.screen.name == "Timeline"
        }

        // The interrupt is requested only after the real command printed its
        // start marker; a missing marker or an earlier terminal is a failure,
        // never a reason to wait a fixed number of seconds and try anyway.
        awaitExecutionStart(runId)
        if (viewModel.state.value.activeRun == null) {
            throw AssertionError(
                "terminal_race_natural_completion; terminal=" +
                    terminalNameFor(runId))
        }
        compose.onNodeWithTag(Tag.INTERRUPT).performClick()
        assertTrue("interrupt must ask before acting",
            viewModel.state.value.interruptConfirm)
        compose.onNodeWithTag(Tag.CONFIRM_INTERRUPT).performClick()
        waitFor("run terminal after interrupt", timeoutMs = terminalTimeoutMs) {
            viewModel.state.value.activeRun == null
        }

        val terminal = terminalFor(runId)
        assertNotNull("the interrupted run must reach a terminal event",
            terminal)
        assertEquals(
            "a natural completion before the interrupt is a race, not success",
            "run.interrupted", terminal!!.type)
        interruptOperation(runId, terminalTimeoutMs)
        waitFor("composer after interrupt", timeoutMs = 30_000) {
            viewModel.state.value.writable
        }
        compose.onNodeWithTag(Tag.SEND_RUN).assertIsDisplayed()
        assertTrue("the command's real start output must stay on the timeline",
            viewModel.state.value.timeline
                .filterIsInstance<TimelineItem.Command>()
                .any { it.output.contains("RUNVORI_SLEEP_STARTED") })
    }

    @Test
    fun ra_turn_termination_expires_waiting_approval() {
        check(realMode) { "ra is a real-mode scenario" }
        approvalPageInterruptExpiresApproval("devsess1", realPrompt("ra"))
    }

    @Test
    fun s3_approval_page_interrupt_expires_request() {
        check(!realMode) { "s3 is the synthetic companion of ra" }
        approvalPageInterruptExpiresApproval(
            "devsess1", "wait for the approval [interrupt]")
    }

    @Test
    fun r_restart_readback_keeps_command_and_artifact_hashes() {
        val sessionId = requiredArg("restart_session")
        val runId = requiredArg("restart_run_id")
        startWithStoredCredential()
        openSession(sessionId)
        openResults()
        val results = viewModel.state.value.results!!
        assertEquals("the captured run must still be the latest result",
            runId, results.runId)

        val commandItemId = arg("restart_command_item_id")
        val commandSha = arg("restart_command_sha256")
        if (commandItemId.isNotEmpty() && commandSha.isNotEmpty()) {
            val command = viewModel.state.value.commands
                .firstOrNull { it.itemId == commandItemId }
            assertNotNull("the captured command must still be listed", command)
            assertFalse("the captured command must not be truncated",
                command!!.commandTruncated)
            assertEquals("the command text must survive the restart",
                commandSha, sha256Hex(command.command.orEmpty()))
        }

        val artifactId = arg("restart_artifact_id")
        val artifactSha = arg("restart_artifact_sha256")
        val artifactSize = arg("restart_artifact_size")
        if (artifactId.isNotEmpty() && artifactSha.isNotEmpty() &&
            artifactSize.isNotEmpty()) {
            waitFor("captured artifact in results", timeoutMs = 30_000) {
                viewModel.state.value.artifacts
                    .any { it.artifactId == artifactId }
            }
            val artifact = viewModel.state.value.artifacts
                .first { it.artifactId == artifactId }
            assertEquals("the persisted artifact digest must not change",
                artifactSha, artifact.sha256)
            assertEquals(artifactSize.toLong(), artifact.sizeBytes)
            assertTrue(artifact.contentAvailable)
            viewModel.openArtifact(artifact)
            waitFor("full artifact content", timeoutMs = 30_000) {
                val current = viewModel.state.value
                current.screen.name == "Artifact" && !current.artifactLoading &&
                    current.artifactError == null &&
                    (artifact.sizeBytes == 0L ||
                        (current.artifactNextOffset ?: 0L) >= artifact.sizeBytes)
            }
            val current = viewModel.state.value
            assertEquals("text", current.artifactRepresentation)
            assertFalse(current.artifactTruncated)
            assertEquals(artifact.sizeBytes, current.artifactLoadedBytes)
            assertEquals("the artifact bytes must survive the restart",
                artifactSha, sha256Hex(current.artifactText))
        }
    }

    @Test
    fun o_approval_survives_pause_and_process_restore() {
        startWithRoutes()
        openSession("devsess1")
        startRun("please change a file [file-change]")
        val approval = openFirstApproval()
        assertEquals("Approval", viewModel.state.value.screen.name)

        // Backgrounding must not drop the screen or submit anything.
        viewModel.pause()
        Thread.sleep(500)
        assertEquals("Approval", viewModel.state.value.screen.name)
        assertNotNull(viewModel.state.value.approval)
        viewModel.resume()
        waitFor("approval after foreground", timeoutMs = 30_000) {
            viewModel.state.value.screen.name == "Approval" &&
                viewModel.state.value.approval != null
        }
        assertTrue(viewModel.state.value.approval?.canDeny == true)

        // Process loss: a new view model starts from the durable route and
        // the cache, and re-reads the approval from the Host.
        restoreAfterProcessLoss()
        waitFor("restored approval", timeoutMs = 30_000) {
            viewModel.state.value.screen.name == "Approval" &&
                viewModel.state.value.approval?.approvalId == approval.approvalId
        }
        assertEquals("Approval", viewModel.state.value.screen.name)
        assertTrue(viewModel.state.value.approval?.canDeny == true)
        assertNull(viewModel.state.value.decisionInFlight)
        // The approval is re-read from the Host; wait until that sync closes
        // the write gate. A click while the restored screen is still read-only
        // would be ignored and would leave the single active slot occupied.
        waitFor("restored approval actionable", timeoutMs = 30_000) {
            viewModel.state.value.screen.name == "Approval" &&
                viewModel.state.value.writable &&
                viewModel.state.value.approval?.canDeny == true
        }
        compose.onNodeWithTag(Tag.DENY).performClick()
        waitFor("run terminal after deny", timeoutMs = 45_000) {
            viewModel.state.value.activeRun == null &&
                approval.runId?.let { terminalFor(it) } != null
        }
    }

    @Test
    fun q_decision_receipt_loss_is_recovered_without_resubmit() {
        startWithRoutes()
        openSession("devsess1")
        startRun("please change a file [file-change]")
        val approval = openFirstApproval()
        val hostId = viewModel.state.value.hostId
        val lostRequest = "dec-lost-" + System.currentTimeMillis()
        session.recordOperation(
            hostId, lostRequest, "approval", approval.approvalId)
        val before = cache.operations(hostId).count {
            it.kind == "approval" && it.resourceId == approval.approvalId
        }

        restoreAfterProcessLoss()
        waitFor("receipt-loss recovery", timeoutMs = 30_000) {
            viewModel.state.value.screen.name == "Approval" &&
                viewModel.state.value.decisionNotice?.contains("待确认") == true
        }
        assertEquals(
            "the submitted decision must not be sent again",
            before,
            cache.operations(hostId).count {
                it.kind == "approval" && it.resourceId == approval.approvalId
            })
        assertTrue(viewModel.state.value.decisionUnresolved)
        assertNotNull(viewModel.state.value.decisionInFlight)
        compose.onNodeWithTag(Tag.ALLOW).assertIsNotEnabled()
        compose.onNodeWithTag(Tag.DENY).assertIsNotEnabled()
        viewModel.decide("deny")
        Thread.sleep(500)
        assertEquals(
            "a repeated tap must not create a second operation",
            before,
            cache.operations(hostId).count {
                it.kind == "approval" && it.resourceId == approval.approvalId
            })

        // Cleanup only: submit a separate, real decision so the Host's single
        // active-run slot is free for the next scenario. This is not product
        // behaviour and is not retried.
        val current = viewModel.state.value.approval!!
        session.api().decide(
            current.approvalId, "dec-cleanup-" + System.currentTimeMillis(),
            current.version, "deny")
        waitFor("cleanup deny terminal", timeoutMs = 45_000) {
            viewModel.state.value.activeRun == null
        }
    }

    @Test
    fun w_missing_approval_is_stale_and_not_actionable() {
        startWithRoutes()
        openSession("devsess1")
        viewModel.openApproval("approval_missing_acceptance")
        waitFor("stale approval", timeoutMs = 30_000) {
            viewModel.state.value.screen.name == "Approval" &&
                viewModel.state.value.approvalStale
        }
        compose.onNodeWithTag(Tag.APPROVAL_STALE).assertIsDisplayed()
        compose.onNodeWithTag(Tag.ALLOW).assertDoesNotExist()
        compose.onNodeWithTag(Tag.DENY).assertDoesNotExist()
        assertNull(viewModel.state.value.approval)
        viewModel.closeApproval()
        waitFor("timeline after stale approval") {
            viewModel.state.value.screen.name == "Timeline"
        }
    }

    @Test
    fun x_artifact_switch_ignores_stale_chunk() {
        startWithStoredCredential()
        openSession("devsess1")
        startRun("please change a file [file-change-complete]")
        openFirstApproval()
        compose.onNodeWithTag(Tag.ALLOW).performClick()
        waitFor("run terminal", timeoutMs = terminalTimeoutMs) {
            viewModel.state.value.activeRun == null
        }
        openResults()
        waitFor("two readable artifacts", timeoutMs = 30_000) {
            viewModel.state.value.artifacts.count {
                it.contentAvailable && it.sizeBytes > 0
            } >= 2
        }
        val readable = viewModel.state.value.artifacts.filter {
            it.contentAvailable && it.sizeBytes > 0
        }
        val first = readable[0]
        val second = readable[1]
        val expectedSecond = session.api()
            .artifactContent(second.artifactId, 0).data.toString(Charsets.UTF_8)

        viewModel.openArtifact(first)
        viewModel.openArtifact(second)
        waitFor("second artifact", timeoutMs = 30_000) {
            viewModel.state.value.screen.name == "Artifact" &&
                viewModel.state.value.artifact?.artifactId == second.artifactId &&
                !viewModel.state.value.artifactLoading
        }
        assertEquals("the old request must not overwrite the new artifact",
            expectedSecond, viewModel.state.value.artifactText)

        viewModel.openArtifact(first)
        viewModel.closeArtifact()
        viewModel.openArtifact(second)
        waitFor("second artifact after reopen", timeoutMs = 30_000) {
            viewModel.state.value.screen.name == "Artifact" &&
                viewModel.state.value.artifact?.artifactId == second.artifactId &&
                !viewModel.state.value.artifactLoading
        }
        assertEquals("a closed request must not append to the reopened one",
            expectedSecond, viewModel.state.value.artifactText)
    }

    @Test
    fun u_process_loss_setup_leaves_approval_and_lost_receipt() {
        check(!realMode) { "u is an internal synthetic setup" }
        startWithRoutes()
        openSession("devsess1")
        startRun("please change a file [file-change]")
        val approval = openFirstApproval()
        // Record a submitted decision whose receipt was never received. The
        // Host has no such operation; the app must query, not resend.
        session.recordOperation(viewModel.state.value.hostId,
            "dec-lost-process-" + System.currentTimeMillis(),
            "approval", approval.approvalId)
        assertEquals("Approval", viewModel.state.value.screen.name)
        assertNotNull(viewModel.state.value.approval)
        viewModel.pause()
        Thread.sleep(500)
    }

    @Test
    fun s2_stale_approval_after_host_restart() {
        check(!realMode) { "s2 is a synthetic restart scenario" }
        val approvalId = requiredArg("stale_approval_id")
        startWithStoredCredential()
        openSession("devsess0")
        viewModel.openApproval(approvalId)
        waitFor("stale approval after restart", timeoutMs = 30_000) {
            viewModel.state.value.screen.name == "Approval" &&
                (viewModel.state.value.approvalStale ||
                    viewModel.state.value.approval != null)
        }
        val view = viewModel.state.value.approval
        if (view == null) {
            assertTrue(viewModel.state.value.approvalStale)
            compose.onNodeWithTag(Tag.APPROVAL_STALE).assertIsDisplayed()
            compose.onNodeWithTag(Tag.ALLOW).assertDoesNotExist()
            compose.onNodeWithTag(Tag.DENY).assertDoesNotExist()
        } else {
            assertTrue("an approval from the old epoch must not stay actionable",
                view.status in setOf("expired", "unknown", "resolved"))
            assertFalse(view.canAllow)
            assertFalse(view.canDeny)
            compose.onNodeWithTag(Tag.ALLOW).assertIsNotEnabled()
            compose.onNodeWithTag(Tag.DENY).assertIsNotEnabled()
        }
        val refused = runCatching {
            session.api().decide(approvalId,
                "stale-attempt-" + System.currentTimeMillis(),
                view?.version ?: 1, "allow_once")
        }
        assertTrue("the Host must refuse a decision from the old epoch",
            refused.isFailure)
    }

    @Test
    fun off_offline_shows_cached_sessions_and_timeline_read_only() {
        check(!realMode) { "off is a synthetic offline scenario" }
        assertNotNull("this scenario needs a prior pairing", store.load())
        // The orchestrator stopped the Host before starting this scenario.
        startViewModel(resetCredential = false)
        waitFor("offline connection", timeoutMs = 30_000) {
            viewModel.state.value.connection.contains("离线")
        }
        assertTrue("cached sessions must survive without the Host",
            viewModel.state.value.sessions.any { it.sessionId == "devsess0" })
        compose.onNodeWithTag(Tag.sessionRow("devsess0")).assertIsDisplayed()

        val hostId = viewModel.state.value.hostId
        val operationsBefore = cache.operations(hostId).size
        viewModel.openSession("devsess0")
        waitFor("cached timeline", timeoutMs = 30_000) {
            viewModel.state.value.screen.name == "Timeline" &&
                viewModel.state.value.timeline.isNotEmpty()
        }
        assertFalse("an offline timeline is read-only",
            viewModel.state.value.writable)
        compose.onNodeWithTag(Tag.TIMELINE).assertIsDisplayed()
        compose.onNodeWithTag(Tag.SEND_RUN).assertDoesNotExist()

        // A write entry that bypasses the hidden composer still refuses to
        // leave the cache and never creates a run operation.
        viewModel.updateComposer("offline attempt")
        viewModel.startRun("offline attempt")
        Thread.sleep(500)
        assertFalse(viewModel.state.value.startInFlight)
        assertTrue(viewModel.state.value.error.orEmpty().contains("离线"))
        assertEquals(
            "offline must not record a new operation",
            operationsBefore, cache.operations(hostId).size)
    }

    @Test
    fun rec_recovery_resyncs_from_cursor_without_duplicates() {
        check(!realMode) { "rec is a synthetic recovery scenario" }
        startWithStoredCredential()
        waitFor("sessions after recovery", timeoutMs = 30_000) {
            viewModel.state.value.sessions.any { it.sessionId == "devsess0" }
        }
        openSession("devsess0")
        val hostId = viewModel.state.value.hostId
        val before = cache.events(hostId, "devsess0", limit = 100_000)
        assertTrue("expected cached history before recovery", before.isNotEmpty())

        // Re-read twice through the product sync path: the cursor must not
        // duplicate events and the terminal history must remain.
        val first = session.syncSession("devsess0")
        val second = session.syncSession("devsess0")
        for (sync in listOf(first, second)) {
            val seqs = sync.events.map { it.seq }
            assertEquals("no duplicate seq after recovery",
                seqs.size, seqs.toSet().size)
            assertEquals("the recovered sequence is contiguous",
                (1..seqs.size).map { it.toLong() }, seqs)
            assertFalse("recovery must not leave a gap", sync.hasGap)
            assertTrue("the terminal run must survive recovery",
                sync.events.any {
                    it.type == "run.completed" || it.type == "run.interrupted"
                })
        }
        assertEquals("recovery must not duplicate stored operations",
            cache.operations(hostId).size,
            cache.operations(hostId).distinctBy { it.requestId }.size)
    }

    @Test
    fun fs_font_scale_200_key_pages_remain_operable() {
        check(!realMode) { "fs is a synthetic font-scale scenario" }
        val fontScale = InstrumentationRegistry.getInstrumentation()
            .targetContext.resources.configuration.fontScale
        assertTrue("orchestrator must set 200% font scale, got $fontScale",
            fontScale >= 1.9f)
        startWithStoredCredential()
        waitFor("sessions at 200% font", timeoutMs = 30_000) {
            viewModel.state.value.sessions.any { it.sessionId == "devsess1" }
        }
        compose.onNodeWithTag(Tag.sessionRow("devsess1")).assertIsDisplayed()

        openSession("devsess1")
        // `openSession` starts an asynchronous sync. Wait for the projected
        // history and its session version before writing; otherwise a write
        // can carry the previous screen's version and be refused.
        waitFor("devsess1 synced at 200% font", timeoutMs = 30_000) {
            viewModel.state.value.screen.name == "Timeline" &&
                !viewModel.state.value.loading &&
                viewModel.state.value.timeline.isNotEmpty() &&
                viewModel.state.value.sessionVersion > 0
        }
        compose.onNodeWithTag(Tag.TIMELINE).assertIsDisplayed()
        startRun("please change a file [file-change]")
        openFirstApproval()
        // This synthetic request is a file-change review, so there is no
        // command tag; the status and the actionable button are the key
        // controls that must remain reachable at 200%.
        compose.onNodeWithTag(Tag.APPROVAL_STATUS).assertExists()
        compose.onNodeWithTag(Tag.INSUFFICIENT).assertExists()
        compose.onNodeWithTag(Tag.DENY)
            .performScrollTo().assertIsDisplayed().performClick()
        waitFor("terminal at 200% font", timeoutMs = 45_000) {
            viewModel.state.value.activeRun == null
        }
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