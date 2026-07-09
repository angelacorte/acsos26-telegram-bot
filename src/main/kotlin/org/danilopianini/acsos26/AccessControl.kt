package org.angelacorte.acsos26

import java.util.concurrent.ConcurrentHashMap

/**
 * In-memory chat authorization for private bot deployments.
 */
internal class AccessControl(
    private val accessKey: String?,
) {
    private val authorizedChats = ConcurrentHashMap.newKeySet<Long>()

    /**
     * True when a user-facing access key is configured.
     */
    val isEnabled: Boolean = !accessKey.isNullOrBlank()

    /**
     * Authorizes a Telegram chat when the submitted key matches the configured key.
     */
    fun authorize(
        chatId: Long?,
        submittedKey: String,
    ): Boolean =
        if (!isEnabled) {
            true
        } else if (chatId != null && submittedKey == accessKey) {
            authorizedChats.add(chatId)
            true
        } else {
            false
        }

    /**
     * Returns true when the chat can use private bot features.
     */
    fun isAuthorized(chatId: Long?): Boolean = !isEnabled || chatId in authorizedChats

    companion object {
        /**
         * Builds access control from BOT_ACCESS_KEY. Empty means public mode.
         */
        fun fromEnvironment(): AccessControl = AccessControl(System.getenv("BOT_ACCESS_KEY"))

        /**
         * Access control disabled for tests and local public mode.
         */
        fun disabled(): AccessControl = AccessControl(null)
    }
}
