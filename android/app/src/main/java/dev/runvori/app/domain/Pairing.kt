package dev.runvori.app.domain

/**
 * One local pairing payload as issued by the Host's local administration pipe.
 *
 * The payload names the Host, the HTTPS endpoint and the identity digest. The
 * phone must compare the digest with what it pins before any secret leaves the
 * device; a mismatch is a refusal, never a prompt to continue.
 */
data class PairingInfo(
    val httpsUrl: String,
    val hostId: String,
    val hostName: String,
    val spkiSha256: String,
    val pairingSecret: String,
    val expiresAt: String?,
) {
    companion object {
        private val HEX_64 = Regex("^[0-9a-f]{64}$")

        /** Accepts the exact JSON the `pairing-info` admin command prints. */
        fun parse(raw: String): PairingInfo {
            val parsed = try {
                JsonValue.parse(raw)
            } catch (_: JsonFormat) {
                throw PairingError("invalid_pairing_text")
            }
            val value = parsed as? Map<*, *>
                ?: throw PairingError("invalid_pairing_text")
            val url = value["https_url"] as? String
                ?: throw PairingError("invalid_pairing_text")
            val hostId = value["host_id"] as? String
                ?: throw PairingError("invalid_pairing_text")
            val hostName = value["host_name"] as? String ?: "Runvori Host"
            val pin = (value["spki_sha256"] as? String)?.lowercase()
                ?: throw PairingError("invalid_pairing_text")
            val secret = value["pairing_secret"] as? String
                ?: throw PairingError("invalid_pairing_text")
            val expiresAt = value["expires_at"] as? String
            if (!HEX_64.matches(pin)) {
                throw PairingError("invalid_pairing_fingerprint")
            }
            if (!hostId.startsWith("host_") || hostId.length < 12) {
                throw PairingError("invalid_pairing_host_id")
            }
            val endpoint = parseHttpsEndpoint(url)
            if (secret.length < 32) {
                throw PairingError("invalid_pairing_secret")
            }
            return PairingInfo(
                httpsUrl = endpoint.url,
                hostId = hostId,
                hostName = hostName,
                spkiSha256 = pin,
                pairingSecret = secret,
                expiresAt = expiresAt,
            )
        }

        /**
         * A pairing link must be HTTPS with an explicit port. The Host prints
         * the base URL with a `/v1` suffix; that suffix is accepted and
         * stripped, while any other path is refused.
         */
        fun parseHttpsEndpoint(value: String): PairingEndpoint {
            var trimmed = value.trim().trimEnd('/')
            if (!trimmed.startsWith("https://")) {
                throw PairingError("pairing_url_not_https")
            }
            if (trimmed.endsWith("/v1")) trimmed = trimmed.dropLast(3)
            val rest = trimmed.removePrefix("https://")
            if (rest.isEmpty() || rest.contains('/') || rest.contains('?')
                || rest.contains('#')) {
                throw PairingError("pairing_url_invalid")
            }
            val colon = rest.lastIndexOf(':')
            // The Host always prints `https://host:port`; an omitted port
            // would silently change meaning, so it is refused.
            if (colon <= 0 || colon == rest.length - 1) {
                throw PairingError("pairing_url_invalid")
            }
            val host = rest.substring(0, colon)
            val portText = rest.substring(colon + 1)
            if (host.isEmpty() || host.startsWith("[") != host.endsWith("]")) {
                throw PairingError("pairing_url_invalid")
            }
            val port = portText.toIntOrNull()
                ?: throw PairingError("pairing_url_invalid")
            if (port !in 1..65535) throw PairingError("pairing_url_invalid")
            return PairingEndpoint(
                host = host.removeSurrounding("[", "]"),
                port = port,
                url = "https://$host:$port",
            )
        }
    }
}

data class PairingEndpoint(val host: String, val port: Int, val url: String)

class PairingError(val code: String, message: String = code) : Exception(message)

class ProtocolError(
    val code: String,
    val status: Int,
    message: String = code,
    val retryable: Boolean = false,
    val requestId: String? = null,
) : Exception(message)

/** Credentials the Host issued; the token is never written to a log. */
data class PairedDevice(
    val deviceId: String,
    val hostId: String,
    val token: ByteArray,
    val protocol: String,
) {
    override fun equals(other: Any?): Boolean =
        other is PairedDevice && deviceId == other.deviceId &&
            hostId == other.hostId && token.contentEquals(other.token) &&
            protocol == other.protocol

    override fun hashCode(): Int =
        listOf(deviceId, hostId, protocol).hashCode() * 31 + token.contentHashCode()
}