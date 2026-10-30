package dev.runvori.app.domain

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Pure JVM checks for the parts of the domain that must not regress. */
class TimelineProjectorTest {

    private fun event(seq: Long, type: String, payload: Map<String, Any?>,
                      runId: String? = "run1") = SessionEvent(
        seq = seq, type = type, runId = runId, payload = payload,
        recordedAt = "2026-10-27T00:00:0${seq}Z")

    @Test
    fun `streaming deltas merge into one item in seq order`() {
        val items = TimelineProjector.project(listOf(
            event(4, "message.delta", mapOf("item_id" to "m1", "text" to "成")),
            event(1, "run.created", mapOf("run_id" to "run1")),
            event(2, "message.started", mapOf("item_id" to "m1")),
            event(3, "message.delta", mapOf("item_id" to "m1", "text" to "完")),
            event(5, "message.completed", mapOf("item_id" to "m1")),
        ))
        assertEquals(listOf("run:run1", "message:m1"), items.map { it.key })
        val message = items[1] as TimelineItem.AgentMessage
        assertEquals("完成", message.text)
        assertTrue(message.complete)
        assertEquals(2L, message.firstSeq)
        assertEquals(5L, message.lastSeq)
    }

    @Test
    fun `duplicate seq is projected once and keeps the first text`() {
        val items = TimelineProjector.project(listOf(
            event(1, "message.started", mapOf("item_id" to "m1")),
            event(1, "message.started", mapOf("item_id" to "m1")),
            event(2, "message.delta", mapOf("item_id" to "m1", "text" to "a")),
        ))
        assertEquals(1, items.size)
        assertEquals("a", (items[0] as TimelineItem.AgentMessage).text)
    }

    @Test
    fun `commands keep the exact command and report the exit code`() {
        val items = TimelineProjector.project(listOf(
            event(1, "command.started", mapOf(
                "item_id" to "c1", "command" to "python --version",
                "cwd" to "C:\\ws", "command_truncated" to false)),
            event(2, "command.output", mapOf("item_id" to "c1", "text" to "3.12\n")),
            event(3, "command.completed", mapOf(
                "item_id" to "c1", "status" to "completed",
                "exit_code" to 0, "duration_ms" to 12,
                "output_omitted" to false)),
        ))
        val command = items.single() as TimelineItem.Command
        assertEquals("python --version", command.command)
        assertEquals("C:\\ws", command.cwd)
        assertEquals(0, command.exitCode)
        assertEquals("3.12\n", command.output)
        assertTrue(command.complete)
    }

    @Test
    fun `a non-zero exit code is preserved and not coerced`() {
        val items = TimelineProjector.project(listOf(
            event(1, "command.started", mapOf("item_id" to "c1", "command" to "exit 7")),
            event(2, "command.completed", mapOf(
                "item_id" to "c1", "status" to "failed", "exit_code" to 7)),
        ))
        assertEquals(7, (items.single() as TimelineItem.Command).exitCode)
    }

    @Test
    fun `omitted text is surfaced rather than rendered as empty success`() {
        val items = TimelineProjector.project(listOf(
            event(1, "message.started", mapOf("item_id" to "m1")),
            event(2, "message.completed", mapOf(
                "item_id" to "m1", "text_omitted" to true)),
        ))
        val message = items.single() as TimelineItem.AgentMessage
        assertTrue(message.textOmitted)
        assertEquals("", message.text)
    }

    @Test
    fun `unknown message kinds become a notice instead of disappearing`() {
        val items = TimelineProjector.project(listOf(
            event(1, "message.reasoning", mapOf("text" to "x")),
        ))
        val notice = items.single() as TimelineItem.Notice
        assertTrue(notice.text.contains("升级客户端"))
    }

    @Test
    fun `the truncation marker is its own row, not folded into the message`() {
        val items = TimelineProjector.project(listOf(
            event(1, "message.started", mapOf("item_id" to "m1")),
            event(2, "message.delta", mapOf(
                "item_id" to "m1", "text_omitted" to true, "dropped_bytes" to 4096)),
            event(3, "timeline.truncated", mapOf(
                "reason" to "run_timeline_budget", "dropped_bytes" to 4096)),
        ))
        assertEquals(2, items.size)
        assertTrue(items[1] is TimelineItem.Notice)
        assertTrue((items[1] as TimelineItem.Notice).text.contains("上限"))
    }

    @Test
    fun `approval events keep identity and status`() {
        val items = TimelineProjector.project(listOf(
            event(1, "approval.requested", mapOf(
                "approval_id" to "apr1", "kind" to "file_change",
                "approval_version" to 1, "details_status" to "insufficient")),
            event(2, "approval.updated", mapOf(
                "approval_id" to "apr1", "status" to "pending",
                "approval_version" to 2, "details_status" to "complete")),
        ))
        val approval = items.single() as TimelineItem.Approval
        assertEquals("apr1", approval.approvalId)
        assertEquals("file_change", approval.kind)
        assertEquals("pending", approval.status)
        assertEquals("complete", approval.detailsStatus)
        assertEquals(2L, approval.version)
        assertEquals(1L, approval.firstSeq)
    }
}

class JsonValueTest {
    @Test
    fun `round trips nested values with unicode`() {
        @Suppress("UNCHECKED_CAST")
        val value = JsonValue.parse(
            """{"a": [1, -2, 3.5, true, null], "b": {"c": "汉\n\"x\""}}"""
        ) as Map<String, Any?>
        assertEquals(-2L, (value["a"] as List<*>)[1])
        assertEquals(3.5, (value["a"] as List<*>)[2])
        assertEquals("汉\n\"x\"", (value["b"] as Map<*, *>)["c"])
        val again = JsonValue.parse(JsonValue.stringify(value))
        assertEquals(value, again)
    }

    @Test
    fun `rejects duplicate keys and trailing content`() {
        for (raw in listOf("""{"a":1,"a":2}""", """{"a":1} x""", "{}")) {
            if (raw == "{}") {
                assertEquals(emptyMap<String, Any?>(), JsonValue.parse(raw))
                continue
            }
            try {
                JsonValue.parse(raw)
                throw AssertionError("expected $raw to be rejected")
            } catch (_: JsonFormat) {
                // expected
            }
        }
    }

    @Test
    fun `rejects invalid escapes`() {
        try {
            JsonValue.parse("\"\\q\"")
            throw AssertionError("expected invalid escape to be rejected")
        } catch (_: JsonFormat) {
            // expected
        }
    }
}

class PairingInfoTest {
    private val pin = "a".repeat(64)
    private val secret = "c2VjcmV0".repeat(6)

    private fun payload(url: String = "https://192.168.1.5:8443") =
        """{"https_url": "$url", "host_id": "host_${"b".repeat(32)}",
           "host_name": "My Host", "spki_sha256": "$pin",
           "pairing_secret": "$secret", "expires_at": "2026-10-27T00:05:00Z"}"""

    @Test
    fun `parses the local pairing payload`() {
        val info = PairingInfo.parse(payload())
        assertEquals("https://192.168.1.5:8443", info.httpsUrl)
        assertEquals("My Host", info.hostName)
        assertEquals(pin, info.spkiSha256)
        val endpoint = PairingInfo.parseHttpsEndpoint(info.httpsUrl)
        assertEquals("192.168.1.5", endpoint.host)
        assertEquals(8443, endpoint.port)
    }

    @Test
    fun `accepts the printed v1 suffix`() {
        val endpoint = PairingInfo.parseHttpsEndpoint("https://localhost:8443/v1")
        assertEquals("localhost", endpoint.host)
        assertEquals(8443, endpoint.port)
        assertEquals("https://localhost:8443", endpoint.url)
    }

    @Test
    fun `refuses plain http and unexpected paths`() {
        for (url in listOf("http://192.168.1.5:8443",
            "https://host:8443/other", "https://host", "https://:8443",
            "https://host:notaport")) {
            try {
                PairingInfo.parseHttpsEndpoint(url)
                throw AssertionError("expected $url to be rejected")
            } catch (_: PairingError) {
                // expected
            }
        }
    }

    @Test
    fun `refuses a malformed fingerprint and a short secret`() {
        val badPin = payload().replace(pin, "zz")
        try {
            PairingInfo.parse(badPin)
            throw AssertionError("expected bad pin to be refused")
        } catch (_: PairingError) {
        }
        val shortSecret = payload().replace(secret, "abc")
        try {
            PairingInfo.parse(shortSecret)
            throw AssertionError("expected short secret to be refused")
        } catch (_: PairingError) {
        }
    }

    @Test
    fun `refuses non json text`() {
        try {
            PairingInfo.parse("not json")
            throw AssertionError("expected invalid text to be refused")
        } catch (_: PairingError) {
        }
        try {
            PairingInfo.parse("[]")
            throw AssertionError("expected array to be refused")
        } catch (_: PairingError) {
        }
    }
}

class HostnameMatchTest {
    @Test
    fun `dns names match exactly and through one wildcard label`() {
        assertTrue(HostnameMatch.matches("host.local", listOf(2 to "host.local"), null))
        assertFalse(HostnameMatch.matches("host.local", listOf(2 to "other.local"), null))
        assertTrue(HostnameMatch.matches("a.example.com",
            listOf(2 to "*.example.com"), null))
        assertFalse(HostnameMatch.matches("a.b.example.com",
            listOf(2 to "*.example.com"), null))
        assertFalse(HostnameMatch.matches("example.com",
            listOf(2 to "*.example.com"), null))
    }

    @Test
    fun `ip literals match only ip entries`() {
        assertTrue(HostnameMatch.matches("192.168.1.5",
            listOf(7 to "192.168.1.5"), null))
        assertFalse(HostnameMatch.matches("192.168.1.5",
            listOf(2 to "192.168.1.5"), null))
        assertTrue(HostnameMatch.matches("::1", listOf(7 to "0:0:0:0:0:0:0:1"), null))
        assertFalse(HostnameMatch.matches("192.168.1.5",
            listOf(2 to "*.1.5"), null))
    }

    @Test
    fun `common name is used only when no san exists`() {
        assertTrue(HostnameMatch.matches("host.local", emptyList(), "host.local"))
        assertFalse(HostnameMatch.matches("host.local",
            listOf(2 to "elsewhere"), "host.local"))
        assertNullIndividual()
    }

    private fun assertNullIndividual() {
        // A null CN with no SAN cannot match anything.
        assertFalse(HostnameMatch.matches("host.local", emptyList(), null))
        assertFalse(HostnameMatch.matches("", listOf(2 to "host.local"), null))
    }
}