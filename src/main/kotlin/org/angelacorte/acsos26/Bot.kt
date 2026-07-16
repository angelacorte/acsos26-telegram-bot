@file:JvmName("Bot")

package org.angelacorte.acsos26

import com.github.kotlintelegrambot.Bot
import com.github.kotlintelegrambot.bot
import com.github.kotlintelegrambot.dispatch
import com.github.kotlintelegrambot.dispatcher.newChatMembers
import com.github.kotlintelegrambot.dispatcher.text
import com.github.kotlintelegrambot.entities.ChatAction
import com.github.kotlintelegrambot.entities.ChatId
import com.github.kotlintelegrambot.entities.ReplyParameters
import com.github.kotlintelegrambot.entities.User
import com.github.kotlintelegrambot.types.TelegramBotResult
import java.lang.management.ManagementFactory
import java.time.Instant
import java.time.ZoneOffset
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter

private const val TELEGRAM_MESSAGE_LIMIT = 4096
private const val TELEGRAM_TRUNCATION_SUFFIX = "\n\n[message truncated]"
private const val RATE_LIMIT_MESSAGE = "Please wait a few seconds before asking another question."
private const val STARTUP_GREETING_ENV = "TELEGRAM_STARTUP_GREETING"

/**
 * Telegram bot entrypoint.
 */
fun main() {
    val startupMessageGate = StartupMessageGate(Instant.now().epochSecond)
    val conference = ConferenceRepository.load()
    val llmClient = LlmClient.fromEnvironment()
    val accessControl = AccessControl.fromEnvironment()
    val groupInviteUrl = System.getenv("TELEGRAM_GROUP_INVITE_URL").orEmpty()
    val router = CommandRouter(conference, llmClient, accessControl, groupInviteUrl)
    val chatRateLimiter = ChatRateLimiter.fromEnvironment()
    val botUsername = System.getenv("BOT_USERNAME") ?: conference.botUsername
    val startupGreeting =
        System
            .getenv(STARTUP_GREETING_ENV)
            ?.trim()
            ?.takeIf { it.isNotEmpty() }
            ?: "Hello! The ${conference.shortName} bot is back online."

    val bot =
        bot {
            token =
                checkNotNull(System.getenv("BOT_TOKEN")) {
                    "Please set the BOT_TOKEN environment variable"
                }
            dispatch {
                newChatMembers {
                    val chatId = ChatId.fromId(message.chat.id)
                    if (
                        bot.skipQueuedMessage(
                            startupMessageGate.actionFor(message.chat.id, message.date),
                            chatId,
                            startupGreeting,
                        )
                    ) {
                        return@newChatMembers
                    }
                    val username = message.from?.displayName()
                    if (username != null) {
                        bot.sendMessage(chatId, text = "Welcome to ${conference.shortName}, $username!")
                    }
                }
                text {
                    val chatId = ChatId.fromId(message.chat.id)
                    if (
                        bot.skipQueuedMessage(
                            startupMessageGate.actionFor(message.chat.id, message.date),
                            chatId,
                            startupGreeting,
                        )
                    ) {
                        return@text
                    }
                    val receivedText = message.text.orEmpty()
                    val repliedToBot =
                        message.replyToMessage
                            ?.from
                            ?.username
                            ?.equals(botUsername, ignoreCase = true) == true
                    val triggersAssistant =
                        router.triggersAssistant(receivedText, botUsername, message.chat.type, repliedToBot)
                    if (triggersAssistant && !chatRateLimiter.tryAcquire(message.chat.id)) {
                        bot.sendAnswer(
                            chatId = chatId,
                            text = RATE_LIMIT_MESSAGE,
                            chatType = message.chat.type,
                            messageId = message.messageId,
                            messageThreadId = message.messageThreadId,
                            requester = message.from,
                        )
                        return@text
                    }
                    if (triggersAssistant) {
                        runCatching { bot.sendChatAction(chatId, ChatAction.TYPING) }
                    }
                    val answer =
                        router.answer(receivedText, botUsername, message.chat.id, message.chat.type, repliedToBot)
                            ?: uptimeAnswer(receivedText, botUsername)
                    if (answer != null) {
                        bot.sendAnswer(
                            chatId = chatId,
                            text = answer,
                            chatType = message.chat.type,
                            messageId = message.messageId,
                            messageThreadId = message.messageThreadId,
                            requester = message.from,
                        )
                    }
                }
            }
        }
    Runtime.getRuntime().addShutdownHook(
        Thread({ bot.stopPolling() }, "telegram-bot-shutdown"),
    )
    bot.startPolling()
}

private fun Bot.skipQueuedMessage(
    action: StartupMessageAction,
    chatId: ChatId,
    startupGreeting: String,
): Boolean {
    if (action == StartupMessageAction.PROCESS) return false
    if (action == StartupMessageAction.GREET_AND_SKIP) {
        sendMessage(chatId, text = startupGreeting).logDeliveryError("startup greeting")
    }
    return true
}

/**
 * Sends an answer as a direct group reply and falls back to an addressed message if Telegram
 * rejects the reply metadata. Delivery errors never include the user's question in logs.
 */
private fun Bot.sendAnswer(
    chatId: ChatId,
    text: String,
    chatType: String,
    messageId: Long,
    messageThreadId: Long?,
    requester: User?,
) {
    val replyParameters = groupReplyParameters(chatType, messageId)
    val safeText = text.telegramSafe()
    val replyResult =
        sendMessage(
            chatId,
            text = safeText,
            messageThreadId = messageThreadId,
            replyParameters = replyParameters,
        )
    if (!replyResult.isError || replyParameters == null) {
        replyResult.logDeliveryError("message")
        return
    }

    replyResult.logDeliveryError("group reply")
    val fallbackText = safeText.addressedTo(requester).telegramSafe()
    val topicFallback = sendMessage(chatId, text = fallbackText, messageThreadId = messageThreadId)
    if (!topicFallback.isError || messageThreadId == null) {
        topicFallback.logDeliveryError("group fallback")
        return
    }

    topicFallback.logDeliveryError("topic fallback")
    sendMessage(chatId, text = fallbackText).logDeliveryError("unthreaded fallback")
}

private fun String.addressedTo(user: User?): String =
    user
        ?.displayName()
        ?.takeIf { it.isNotBlank() }
        ?.let { "$it, $this" }
        ?: this

private fun TelegramBotResult<*>.logDeliveryError(context: String) {
    if (this is TelegramBotResult.Error) {
        System.err.println("Telegram $context delivery failed: $this")
    }
}

/** Returns reply metadata only for Telegram group conversations. */
internal fun groupReplyParameters(
    chatType: String,
    messageId: Long,
): ReplyParameters? =
    if (chatType.equals("group", ignoreCase = true) || chatType.equals("supergroup", ignoreCase = true)) {
        ReplyParameters(messageId = messageId, allowSendingWithoutReply = true)
    } else {
        null
    }

private fun User.displayName(): String =
    username?.let { "@$it" } ?: listOfNotNull(firstName, lastName).joinToString(" ")

private fun uptimeAnswer(
    message: String,
    botUsername: String,
): String? =
    if (message.mentions(botUsername) && message.contains("up", ignoreCase = true)) {
        """
        I have been up for ${ManagementFactory.getRuntimeMXBean().uptime} ms, since ${
            ZonedDateTime.now(ZoneOffset.UTC).format(DateTimeFormatter.ISO_INSTANT)
        }
        """.trimIndent()
    } else {
        null
    }

private fun String.telegramSafe(): String =
    if (length <= TELEGRAM_MESSAGE_LIMIT) {
        this
    } else {
        take(TELEGRAM_MESSAGE_LIMIT - TELEGRAM_TRUNCATION_SUFFIX.length) + TELEGRAM_TRUNCATION_SUFFIX
    }
