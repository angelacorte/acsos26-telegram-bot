@file:JvmName("Bot")

package org.angelacorte.acsos26

import com.github.kotlintelegrambot.bot
import com.github.kotlintelegrambot.dispatch
import com.github.kotlintelegrambot.dispatcher.newChatMembers
import com.github.kotlintelegrambot.dispatcher.text
import com.github.kotlintelegrambot.entities.ChatAction
import com.github.kotlintelegrambot.entities.ChatId
import com.github.kotlintelegrambot.entities.User
import java.lang.management.ManagementFactory
import java.time.ZoneOffset
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter

private const val TELEGRAM_MESSAGE_LIMIT = 4096
private const val TELEGRAM_TRUNCATION_SUFFIX = "\n\n[message truncated]"

/**
 * Telegram bot entrypoint.
 */
fun main() {
    val conference = ConferenceRepository.load()
    val llmClient = LlmClient.fromEnvironment()
    val accessControl = AccessControl.fromEnvironment()
    val router = CommandRouter(conference, llmClient, accessControl)
    val botUsername = System.getenv("BOT_USERNAME") ?: conference.botUsername

    val bot =
        bot {
            token =
                checkNotNull(System.getenv("BOT_TOKEN")) {
                    "Please set the BOT_TOKEN environment variable"
                }
            dispatch {
                newChatMembers {
                    val chatId = ChatId.fromId(message.chat.id)
                    val username = message.from?.displayName()
                    if (username != null) {
                        bot.sendMessage(chatId, text = "Welcome to ${conference.shortName}, $username!")
                    }
                }
                text {
                    val chatId = ChatId.fromId(message.chat.id)
                    val receivedText = message.text.orEmpty()
                    val repliedToBot =
                        message.replyToMessage
                            ?.from
                            ?.username
                            ?.equals(botUsername, ignoreCase = true) == true
                    if (router.triggersAssistant(receivedText, botUsername, message.chat.type, repliedToBot)) {
                        runCatching { bot.sendChatAction(chatId, ChatAction.TYPING) }
                    }
                    val answer =
                        router.answer(receivedText, botUsername, message.chat.id, message.chat.type, repliedToBot)
                            ?: uptimeAnswer(receivedText, botUsername)
                    if (answer != null) {
                        bot.sendMessage(
                            chatId,
                            text = answer.telegramSafe(),
                        )
                    }
                }
            }
        }
    bot.startPolling()
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
