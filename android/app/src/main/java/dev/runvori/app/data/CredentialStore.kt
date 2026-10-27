package dev.runvori.app.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Device credentials, encrypted with a non-exportable Android Keystore key.
 *
 * The token and pairing metadata live in private app storage inside an AES-GCM
 * envelope; only the ciphertext is on disk and the key never leaves the
 * Keystore. The application manifest excludes this storage from backup, so a
 * restore to another phone cannot silently re-authorise Runvori.
 *
 * A Keystore key that no longer decrypts (device restore, key invalidation) is
 * treated as "not paired" and the stored bytes are discarded. That is the only
 * safe failure direction: a credential the Host cannot be shown is gone.
 */
interface CredentialStore {
    fun load(): StoredCredential?
    fun save(credential: StoredCredential)
    fun clear()
}

data class StoredCredential(
    val hostId: String,
    val hostName: String,
    val httpsUrl: String,
    val spkiSha256: String,
    val deviceId: String,
    val token: ByteArray,
)

class KeystoreCredentialStore(
    context: Context,
    private val alias: String = "runvori-device-credential",
) : CredentialStore {
    private val preferences = context.getSharedPreferences(
        "runvori.credentials", Context.MODE_PRIVATE)

    override fun load(): StoredCredential? {
        val encoded = preferences.getString(KEY_CIPHERTEXT, null) ?: return null
        val packed = try {
            Base64.decode(encoded, Base64.NO_WRAP)
        } catch (_: IllegalArgumentException) {
            clear()
            return null
        }
        if (packed.size < HEADER_BYTES + 12 + 16) {
            clear()
            return null
        }
        return try {
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(Cipher.DECRYPT_MODE, secretKey(),
                GCMParameterSpec(TAG_BITS, packed, HEADER_BYTES, IV_BYTES))
            val plain = cipher.doFinal(packed, HEADER_BYTES + IV_BYTES,
                packed.size - HEADER_BYTES - IV_BYTES)
            decode(plain)
        } catch (_: Exception) {
            // A credential this device can no longer decrypt is unusable;
            // pretending it exists would only fail later at the Host.
            clear()
            null
        }
    }

    override fun save(credential: StoredCredential) {
        require(credential.token.size == 32) { "invalid_token" }
        require(credential.spkiSha256.length == 64) { "invalid_pin" }
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, secretKey())
        val sealed = cipher.doFinal(encode(credential))
        val packed = ByteArray(HEADER_BYTES + cipher.iv.size + sealed.size)
        packed[0] = VERSION
        System.arraycopy(cipher.iv, 0, packed, HEADER_BYTES, cipher.iv.size)
        System.arraycopy(sealed, 0, packed,
            HEADER_BYTES + cipher.iv.size, sealed.size)
        preferences.edit()
            .putString(KEY_CIPHERTEXT, Base64.encodeToString(packed, Base64.NO_WRAP))
            .commit()
    }

    override fun clear() {
        preferences.edit().remove(KEY_CIPHERTEXT).commit()
        try {
            val store = KeyStore.getInstance("AndroidKeyStore")
            store.load(null)
            if (store.containsAlias(alias)) store.deleteEntry(alias)
        } catch (_: Exception) {
            // Clearing storage is what matters; a leftover key alone holds
            // no credential material.
        }
    }

    private fun secretKey(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore")
        store.load(null)
        (store.getEntry(alias, null) as? KeyStore.SecretKeyEntry)?.let {
            return it.secretKey
        }
        val generator = KeyGenerator.getInstance(
            KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(
            KeyGenParameterSpec.Builder(alias,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                // No user-authentication flag: the user authenticates with
                // the device lock, and a prompt per request would make the
                // approval flow unusable. Nothing is exported off the device.
                .build())
        return generator.generateKey()
    }

    /**
     * Length-prefixed fields, so a host name containing spaces, separators or
     * newlines round-trips exactly and cannot shift the field boundaries.
     */
    private fun encode(credential: StoredCredential): ByteArray {
        val fields = listOf(
            credential.hostId, credential.hostName, credential.httpsUrl,
            credential.spkiSha256, credential.deviceId,
            Base64.encodeToString(credential.token, Base64.NO_WRAP),
        )
        val builder = StringBuilder()
        for (field in fields) {
            // Length is in UTF-8 bytes; the field itself is kept as characters.
            builder.append(field.toByteArray(Charsets.UTF_8).size)
            builder.append(':')
            builder.append(field)
        }
        return builder.toString().toByteArray(Charsets.UTF_8)
    }

    private fun decode(plain: ByteArray): StoredCredential? {
        val text = String(plain, Charsets.UTF_8)
        val parts = ArrayList<String>(6)
        var index = 0
        repeat(6) {
            val colon = text.indexOf(':', index)
            if (colon <= index) return null
            val length = text.substring(index, colon).toIntOrNull() ?: return null
            if (length < 0) return null
            val start = colon + 1
            var cursor = start
            var consumed = 0
            while (cursor < text.length && consumed < length) {
                val width = text[cursor].toString().toByteArray(Charsets.UTF_8).size
                if (consumed + width > length) return null
                consumed += width
                cursor++
            }
            if (consumed != length) return null
            parts.add(text.substring(start, cursor))
            index = cursor
        }
        if (index != text.length) return null
        val token = try {
            Base64.decode(parts[5], Base64.NO_WRAP)
        } catch (_: IllegalArgumentException) {
            return null
        }
        if (token.size != 32) return null
        if (parts[0].isEmpty() || parts[3].length != 64) return null
        return StoredCredential(
            hostId = parts[0], hostName = parts[1], httpsUrl = parts[2],
            spkiSha256 = parts[3], deviceId = parts[4], token = token,
        )
    }

    private companion object {
        const val KEY_CIPHERTEXT = "credential"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val TAG_BITS = 128
        const val IV_BYTES = 12
        const val HEADER_BYTES = 1  // version byte, ahead of the GCM IV
        const val VERSION: Byte = 1
    }
}