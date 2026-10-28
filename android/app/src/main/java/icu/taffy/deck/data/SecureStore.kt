package icu.taffy.deck.data

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
 * 本地小仓库：登录态 cookie 这类敏感串一律用 Android Keystore 里的 AES-GCM 密钥加密后再落
 * SharedPreferences。密钥不出安全硬件（有 TEE 的机器上），备份也带不走（allowBackup=false）。
 *
 * Keystore 不可用（极老的定制 ROM）时退回明文，保证功能不挂；正常设备走不到这条分支。
 */
class SecureStore(context: Context) {

    private val prefs = context.applicationContext
        .getSharedPreferences("deck_secure", Context.MODE_PRIVATE)

    fun putString(key: String, value: String) {
        val cipher = runCatching { encrypt(value) }.getOrNull()
        if (cipher == null) {
            prefs.edit().putString(key, PLAIN_PREFIX + value).apply()
        } else {
            prefs.edit().putString(key, cipher).apply()
        }
    }

    fun getString(key: String): String? {
        val raw = prefs.getString(key, null) ?: return null
        if (raw.startsWith(PLAIN_PREFIX)) return raw.removePrefix(PLAIN_PREFIX)
        return runCatching { decrypt(raw) }.getOrNull()
    }

    fun remove(key: String) {
        prefs.edit().remove(key).apply()
    }

    // ---------------- 加解密 ----------------

    private fun secretKey(): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        val existing = keyStore.getEntry(KEY_ALIAS, null) as? KeyStore.SecretKeyEntry
        if (existing != null) return existing.secretKey

        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(
                KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build()
        )
        return generator.generateKey()
    }

    /** 返回 base64(iv || ciphertext)。 */
    private fun encrypt(plain: String): String {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, secretKey())
        val iv = cipher.iv
        val body = cipher.doFinal(plain.toByteArray(Charsets.UTF_8))
        val out = ByteArray(iv.size + body.size)
        iv.copyInto(out, 0)
        body.copyInto(out, iv.size)
        return Base64.encodeToString(out, Base64.NO_WRAP)
    }

    private fun decrypt(encoded: String): String {
        val all = Base64.decode(encoded, Base64.NO_WRAP)
        val iv = all.copyOfRange(0, IV_LENGTH)
        val body = all.copyOfRange(IV_LENGTH, all.size)
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.DECRYPT_MODE, secretKey(), GCMParameterSpec(128, iv))
        return String(cipher.doFinal(body), Charsets.UTF_8)
    }

    private companion object {
        const val ANDROID_KEYSTORE = "AndroidKeyStore"
        const val KEY_ALIAS = "deck_cookie_key"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val IV_LENGTH = 12
        const val PLAIN_PREFIX = "plain:"
    }
}
