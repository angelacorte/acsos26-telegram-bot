package org.angelacorte.acsos26

import java.util.concurrent.ConcurrentHashMap

internal enum class StartupMessageAction {
    PROCESS,
    GREET_AND_SKIP,
    SKIP,
}

/**
 * Identifies messages that Telegram queued before this process started. The first queued message
 * from each chat triggers a greeting; the remaining queued messages from that chat are skipped.
 */
internal class StartupMessageGate(
    private val startupEpochSeconds: Long,
) {
    private val greetedChats = ConcurrentHashMap.newKeySet<Long>()

    fun actionFor(
        chatId: Long,
        messageEpochSeconds: Long,
    ): StartupMessageAction =
        when {
            messageEpochSeconds > startupEpochSeconds -> StartupMessageAction.PROCESS
            greetedChats.add(chatId) -> StartupMessageAction.GREET_AND_SKIP
            else -> StartupMessageAction.SKIP
        }
}
