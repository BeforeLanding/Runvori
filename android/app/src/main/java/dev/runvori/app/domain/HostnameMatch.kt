package dev.runvori.app.domain

/**
 * Host-name matching for the pinned Host certificate.
 *
 * JSSE silently skips endpoint identification when the installed trust manager
 * is not an [javax.net.ssl.X509ExtendedTrustManager]. The client therefore
 * performs the check itself, over the certificate's SAN entries, and a Host
 * whose certificate does not name the endpoint is refused.
 */
object HostnameMatch {

    private val IPV4 = Regex("^\\d{1,3}(\\.\\d{1,3}){3}$")

    fun isIpLiteral(host: String): Boolean =
        IPV4.matches(host) || host.contains(':')

    /**
     * @param sans certificate SAN entries as (type, value); type 2 is a DNS
     *   name and type 7 is an IP address (the values `subjectAlternativeNames`
     *   reports).
     * @param commonName the subject CN, used only when the certificate has no
     *   SAN entries at all (legacy certificates).
     */
    fun matches(host: String, sans: List<Pair<Int, String>>,
                commonName: String?): Boolean {
        val target = normalize(host)
        if (target.isEmpty()) return false
        if (sans.isEmpty()) {
            return commonName?.let { matchesName(target, it, allowWildcard = !isIpLiteral(target)) }
                ?: false
        }
        val wantIp = isIpLiteral(target)
        for ((type, value) in sans) {
            if (wantIp && type == 7 && sameAddress(target, normalize(value))) return true
            if (!wantIp && type == 2
                && matchesName(target, value, allowWildcard = true)) return true
        }
        return false
    }

    private fun sameAddress(left: String, right: String): Boolean {
        if (left == right) return true
        return try {
            // `::1` and `0:0:0:0:0:0:0:1` are the same address; compare bytes
            // instead of the textual forms.
            java.net.InetAddress.getByName(left).address
                .contentEquals(java.net.InetAddress.getByName(right).address)
        } catch (_: Exception) {
            false
        }
    }

    private fun normalize(host: String): String {
        val trimmed = host.trim().trimEnd('.').lowercase()
        if (trimmed.startsWith("[") && trimmed.endsWith("]")) {
            return trimmed.substring(1, trimmed.length - 1)
        }
        return trimmed
    }

    private fun matchesName(host: String, pattern: String,
                            allowWildcard: Boolean): Boolean {
        val name = normalize(pattern)
        if (name.isEmpty()) return false
        if (!name.startsWith("*.")) return name == host
        if (!allowWildcard || name.count { it == '*' } != 1) return false
        val suffix = name.substring(1) // ".example.com"
        if (!host.endsWith(suffix) || host == suffix.removePrefix(".")) return false
        // Exactly one label may be replaced by the wildcard.
        val prefix = host.substring(0, host.length - suffix.length)
        return prefix.isNotEmpty() && !prefix.contains('.')
    }
}