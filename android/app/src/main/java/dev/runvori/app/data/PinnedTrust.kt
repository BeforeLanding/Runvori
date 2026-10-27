package dev.runvori.app.data

import java.io.ByteArrayInputStream
import java.security.MessageDigest
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLEngine
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509ExtendedTrustManager
import javax.net.ssl.X509TrustManager
import dev.runvori.app.domain.HostnameMatch

/**
 * Endpoint-bound TLS identity for exactly one Host.
 *
 * The pairing payload names a SubjectPublicKeyInfo digest. A connection is
 * accepted only when the presented leaf certificate has that digest, is
 * currently valid, and names the endpoint the client dialled. The pin is
 * supplied by the local pairing screen, never fetched from the server first.
 *
 * There is deliberately no branch that accepts an arbitrary certificate and no
 * flag that disables a check. [X509ExtendedTrustManager] is used because JSSE
 * silently skips endpoint identification for a plain trust manager.
 */
class PinnedTrust(
    private val expectedSpkiSha256: String,
    private val expectedHost: String,
) : X509ExtendedTrustManager() {

    override fun checkClientTrusted(
        chain: Array<out X509Certificate>?, authType: String?,
        socket: java.net.Socket?,
    ) = refuse("client_auth_not_supported")

    override fun checkClientTrusted(
        chain: Array<out X509Certificate>?, authType: String?,
        engine: SSLEngine?,
    ) = refuse("client_auth_not_supported")

    override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) =
        refuse("client_auth_not_supported")

    override fun checkServerTrusted(
        chain: Array<out X509Certificate>?, authType: String?, socket: java.net.Socket?,
    ) = verify(chain)

    override fun checkServerTrusted(
        chain: Array<out X509Certificate>?, authType: String?, engine: SSLEngine?,
    ) = verify(chain)

    override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) =
        verify(chain)

    private fun verify(chain: Array<out X509Certificate>?) {
        if (chain.isNullOrEmpty()) throw PinMismatch("empty_chain")
        val leaf = chain[0]
        // `original` is the digest of the key exactly as the Host advertised.
        // Recomputing it from the key encoding round-trips identically, and a
        // mismatch is a refusal rather than a "continue anyway" prompt.
        val actual = spkiSha256(leaf)
        if (!MessageDigest.isEqual(
                actual.toByteArray(Charsets.US_ASCII),
                expectedSpkiSha256.lowercase().toByteArray(Charsets.US_ASCII))) {
            throw PinMismatch("spki_pin_mismatch")
        }
        try {
            leaf.checkValidity()
        } catch (_: java.security.cert.CertificateExpiredException) {
            throw PinMismatch("certificate_expired")
        } catch (_: java.security.cert.CertificateNotYetValidException) {
            throw PinMismatch("certificate_not_yet_valid")
        }
        val sans = leaf.subjectAlternativeNames.orEmpty()
            .mapNotNull { entry ->
                val type = entry.getOrNull(0) as? Int ?: return@mapNotNull null
                val value = entry.getOrNull(1) as? String ?: return@mapNotNull null
                type to value
            }
        val commonName = leaf.subjectX500Principal.name
            .split(',')
            .map { it.trim() }
            .firstOrNull { it.startsWith("CN=") }
            ?.removePrefix("CN=")
        if (!HostnameMatch.matches(expectedHost, sans, commonName)) {
            throw PinMismatch("certificate_host_mismatch")
        }
    }

    override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()

    private fun refuse(code: String): Nothing =
        throw java.security.cert.CertificateException(code)

    companion object {
        fun socketFactory(pin: String, host: String): SSLSocketFactory {
            val context = SSLContext.getInstance("TLS")
            // A TLS client context that does auth *and* key exchange securely by
        // default; passing an empty SecureRandom keeps the JDK defaults.
        context.init(null, arrayOf(PinnedTrust(pin, host)),
            java.security.SecureRandom())
            return context.socketFactory
        }

        /** SHA-256 over the DER SubjectPublicKeyInfo of a certificate. */
        fun spkiSha256(certificate: X509Certificate): String =
            spkiSha256(certificate.publicKey.encoded)

        fun spkiSha256(spkiDer: ByteArray): String =
            MessageDigest.getInstance("SHA-256").digest(spkiDer)
                .joinToString("") { byte -> "%02x".format(byte) }

        fun parseCertificate(der: ByteArray): X509Certificate {
            val factory = CertificateFactory.getInstance("X.509")
            return factory.generateCertificate(ByteArrayInputStream(der))
                as X509Certificate
        }

        /** Whether the platform trust store accepts this leaf as-is. */
        fun isSystemTrusted(der: ByteArray, host: String): Boolean = try {
            val factory = TrustManagerFactory.getInstance(
                TrustManagerFactory.getDefaultAlgorithm())
            factory.init(null as java.security.KeyStore?)
            val manager = factory.trustManagers
                .filterIsInstance<X509TrustManager>().first()
            manager.checkServerTrusted(arrayOf(parseCertificate(der)), host)
            true
        } catch (_: Exception) {
            false
        }
    }
}

class PinMismatch(val code: String) : java.security.cert.CertificateException(code)

/**
 * Open a TLS socket with the pin and the endpoint check applied.
 *
 * The handshake runs inside this call, so a pin mismatch surfaces before any
 * request byte is written.
 */
fun connectPinned(
    host: String,
    port: Int,
    pin: String,
    connectTimeoutMs: Int = 10_000,
    readTimeoutMs: Int = 35_000,
): SSLSocket {
    val socket = PinnedTrust.socketFactory(pin, host).createSocket() as SSLSocket
    try {
        socket.soTimeout = readTimeoutMs
        socket.useClientMode = true
        socket.connect(java.net.InetSocketAddress(host, port), connectTimeoutMs)
        socket.startHandshake()
        return socket
    } catch (error: Exception) {
        try {
            socket.close()
        } catch (_: Exception) {
            // Closing a failed socket must not mask the original refusal.
        }
        throw error
    }
}