package dev.runvori.app.data

import dev.runvori.app.domain.JsonValue
import dev.runvori.app.domain.PairedDevice
import dev.runvori.app.domain.ProtocolError
import java.io.IOException
import java.io.InputStream
import java.net.SocketTimeoutException
import android.util.Base64
import javax.net.ssl.SSLSocket

/**
 * One request/response exchange with the Host over a pinned TLS socket.
 *
 * There is no retry inside this class: a write was either not sent, or its
 * outcome is unknown. Deciding what to do next (query an operation, re-read a
 * snapshot) belongs to the caller, because retrying a decision blindly could
 * apply it twice.
 */
class HostEndpoint(
    val host: String,
    val port: Int,
    val pin: String,
) {
    fun exchange(
        method: String,
        path: String,
        body: Map<String, Any?>? = null,
        token: ByteArray? = null,
        protocol: String = "1",
        timeoutMs: Int = 35_000,
        connectTimeoutMs: Int = 10_000,
    ): HttpResult {
        // Only a connection whose request bytes were never written may be
        // retried: no side effect can exist yet. Once bytes are on the wire the
        // outcome is unknown and retrying belongs to the caller (typically a
        // status query with the same request id, never a resubmission).
        val write = WriteTracker()
        var lastError: Exception? = null
        repeat(CONNECT_ATTEMPTS) { attempt ->
            try {
                return exchangeOnce(method, path, body, token, protocol,
                    timeoutMs, connectTimeoutMs, write)
            } catch (error: java.io.IOException) {
                lastError = error
                val retryable = !write.started
                if (!retryable || attempt + 1 >= CONNECT_ATTEMPTS) throw error
                Thread.sleep(HANDSHAKE_RETRY_DELAY_MS)
            }
        }
        throw lastError ?: java.io.IOException("exchange_failed")
    }

    /** Whether this exchange reached the point of writing request bytes. */
    private class WriteTracker {
        @Volatile
        var started = false
    }

    private fun exchangeOnce(
        method: String,
        path: String,
        body: Map<String, Any?>?,
        token: ByteArray?,
        protocol: String,
        timeoutMs: Int,
        connectTimeoutMs: Int,
        write: WriteTracker,
    ): HttpResult {
        val socket = connectPinned(host, port, pin, connectTimeoutMs, timeoutMs)
        try {
            val payload = if (body == null) null
            else JsonValue.stringify(body).toByteArray(Charsets.UTF_8)
            val head = StringBuilder()
            head.append(method).append(' ').append(path).append(" HTTP/1.1\r\n")
            head.append("Host: ").append(hostHeader(host, port)).append("\r\n")
            head.append("X-Runvori-Protocol: ").append(protocol).append("\r\n")
            if (token != null) {
                head.append("Authorization: Bearer ")
                    .append(Base64.encodeToString(token, Base64.NO_WRAP))
                    .append("\r\n")
            }
            if (payload != null) {
                head.append("Content-Type: application/json\r\n")
                head.append("Content-Length: ").append(payload.size).append("\r\n")
            }
            head.append("Accept: application/json, application/octet-stream\r\n")
            head.append("Connection: close\r\n\r\n")
            write.started = true
            val out = socket.outputStream
            out.write(head.toString().toByteArray(Charsets.ISO_8859_1))
            if (payload != null) out.write(payload)
            out.flush()
            return readResponse(socket)
        } finally {
            try {
                socket.close()
            } catch (_: IOException) {
                // The response is already read; teardown failures are noise.
            }
        }
    }

    private fun readResponse(socket: SSLSocket): HttpResult {
        val input = socket.inputStream
        val headerBytes = readUntilDoubleCrlf(input, MAX_HEADER_BYTES)
        val headerText = String(headerBytes, Charsets.ISO_8859_1)
        val lines = headerText.split("\r\n")
        val statusLine = lines.firstOrNull() ?: throw IOException("empty_response")
        val parts = statusLine.split(' ')
        if (parts.size < 2) throw IOException("invalid_status_line")
        val status = parts[1].toIntOrNull() ?: throw IOException("invalid_status")
        val headers = LinkedHashMap<String, String>()
        for (line in lines.drop(1)) {
            val colon = line.indexOf(':')
            if (colon <= 0) continue
            headers[line.substring(0, colon).trim().lowercase()] =
                line.substring(colon + 1).trim()
        }
        val length = headers["content-length"]?.toIntOrNull()
            ?: throw IOException("missing_content_length")
        if (length < 0 || length > MAX_BODY_BYTES) throw IOException("body_too_large")
        val body = readExactly(input, length)
        val contentType = headers["content-type"].orEmpty().lowercase()
        @Suppress("UNCHECKED_CAST")
        val decoded = if (body.isEmpty() || !contentType.startsWith("application/json"))
            emptyMap<String, Any?>()
        else JsonValue.parse(String(body, Charsets.UTF_8)) as? Map<String, Any?>
            ?: throw IOException("invalid_body")
        return HttpResult(status, decoded, headers, body)
    }

    private fun readUntilDoubleCrlf(input: InputStream, limit: Int): ByteArray {
        val buffer = java.io.ByteArrayOutputStream()
        var matched = 0
        while (true) {
            val value = input.read()
            if (value < 0) throw IOException("unexpected_eof")
            buffer.write(value)
            matched = when {
                matched == 0 && value == '\r'.code -> 1
                matched == 1 && value == '\n'.code -> 2
                matched == 2 && value == '\r'.code -> 3
                matched == 3 && value == '\n'.code -> 4
                value == '\r'.code -> 1
                else -> 0
            }
            if (matched == 4) return buffer.toByteArray()
            if (buffer.size() > limit) throw IOException("header_too_large")
        }
    }

    private fun readExactly(input: InputStream, length: Int): ByteArray {
        val body = ByteArray(length)
        var offset = 0
        while (offset < length) {
            val read = try {
                input.read(body, offset, length - offset)
            } catch (_: SocketTimeoutException) {
                throw IOException("response_timeout")
            }
            if (read < 0) throw IOException("unexpected_eof")
            offset += read
        }
        return body
    }

    private fun hostHeader(host: String, port: Int): String =
        if (host.contains(':')) "[$host]:$port" else "$host:$port"

    companion object {
        const val MAX_HEADER_BYTES = 32 * 1024
        const val MAX_BODY_BYTES = 1024 * 1024

        const val CONNECT_ATTEMPTS = 2
        const val HANDSHAKE_RETRY_DELAY_MS = 250L

        /** Rebuild the dial target from a stored `https://host:port` URL. */
        fun fromUrl(url: String, pin: String): HostEndpoint? {
            val endpoint = try {
                dev.runvori.app.domain.PairingInfo.parseHttpsEndpoint(url)
            } catch (_: Exception) {
                return null
            }
            return HostEndpoint(endpoint.host, endpoint.port, pin)
        }
    }
}

data class HttpResult(
    val status: Int,
    val body: Map<String, Any?>,
    val headers: Map<String, String>,
    val rawBody: ByteArray = ByteArray(0),
) {
    fun error(): ProtocolError? {
        if (status in 200..299) return null
        val error = body["error"] as? Map<*, *>
        val code = error?.get("code") as? String ?: "http_$status"
        val retryable = error?.get("retryable") == true
        val requestId = error?.get("request_id") as? String
        return ProtocolError(code, status, code, retryable, requestId)
    }

    fun text(name: String): String? = body[name] as? String

    fun long(name: String): Long? = (body[name] as? Number)?.toLong()

    fun int(name: String): Int? = (body[name] as? Number)?.toInt()

    fun map(name: String): Map<String, Any?>? {
        @Suppress("UNCHECKED_CAST")
        return body[name] as? Map<String, Any?>
    }

    @Suppress("UNCHECKED_CAST")
    fun list(name: String): List<Any?> = body[name] as? List<Any?> ?: emptyList()
}

/** Resource wrappers for the v1 payload shapes the app reads. */
object ProtocolView {
    fun paired(response: HttpResult): PairedDevice {
        val deviceId = response.text("device_id") ?: throw ProtocolError(
            "invalid_pairing_response", response.status)
        val hostId = response.text("host_id") ?: throw ProtocolError(
            "invalid_pairing_response", response.status)
        val encoded = response.text("device_token") ?: throw ProtocolError(
            "invalid_pairing_response", response.status)
        val token = try {
            Base64.decode(encoded, Base64.NO_WRAP)
        } catch (_: IllegalArgumentException) {
            throw ProtocolError("invalid_pairing_response", response.status)
        }
        if (token.size != 32) {
            throw ProtocolError("invalid_pairing_response", response.status)
        }
        return PairedDevice(
            deviceId = deviceId,
            hostId = hostId,
            token = token,
            protocol = response.text("protocol") ?: "1",
        )
    }

    fun sessionEvent(value: Any?): dev.runvori.app.domain.SessionEvent {
        val map = value as? Map<*, *>
            ?: throw ProtocolError("invalid_event", 200)
        val seq = (map["seq"] as? Number)?.toLong()
            ?: throw ProtocolError("invalid_event", 200)
        val type = map["type"] as? String
            ?: throw ProtocolError("invalid_event", 200)
        @Suppress("UNCHECKED_CAST")
        val payload = (map["payload"] as? Map<String, Any?>) ?: emptyMap()
        return dev.runvori.app.domain.SessionEvent(
            seq = seq,
            type = type,
            runId = map["run_id"] as? String,
            payload = payload,
            recordedAt = map["recorded_at"] as? String,
        )
    }
}