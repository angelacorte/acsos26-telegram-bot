package org.angelacorte.acsos26

import java.time.Duration
import java.util.concurrent.ConcurrentHashMap

private const val DEFAULT_CHAT_COOLDOWN_SECONDS = 3L

/**
 * Applies a small per-chat cooldown to requests that can reach the LLM service.
 *
 * The map is safe for concurrent Telegram callbacks. Entries are bounded by the number of chats
 * seen by this single bot process, which is intentionally deployed as one replica.
 */
internal class ChatRateLimiter(
    cooldown: Duration,
    private val nanoTime: () -> Long = System::nanoTime,
) {
    private val cooldownNanos = cooldown.toNanos().coerceAtLeast(0)
    private val nextAllowedAt = ConcurrentHashMap<Long, Long>()

    /** Returns true and records the request when the chat is outside its cooldown window. */
    fun tryAcquire(chatId: Long): Boolean {
        val now = nanoTime()
        var acquired = false
        nextAllowedAt.compute(chatId) { _, currentDeadline ->
            if (currentDeadline == null || now >= currentDeadline) {
                acquired = true
                now + cooldownNanos
            } else {
                currentDeadline
            }
        }
        return acquired
    }

    companion object {
        /** Builds the limiter from BOT_CHAT_COOLDOWN_SECONDS, defaulting to three seconds. */
        fun fromEnvironment(): ChatRateLimiter {
            val seconds =
                System
                    .getenv("BOT_CHAT_COOLDOWN_SECONDS")
                    ?.toLongOrNull()
                    ?.coerceAtLeast(0)
                    ?: DEFAULT_CHAT_COOLDOWN_SECONDS
            return ChatRateLimiter(Duration.ofSeconds(seconds))
        }
    }
}
