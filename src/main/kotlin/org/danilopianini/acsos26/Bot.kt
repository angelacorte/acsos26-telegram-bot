@file:JvmName("Bot")

package org.angelacorte.acsos26

import com.github.kotlintelegrambot.bot
import com.github.kotlintelegrambot.dispatch
import com.github.kotlintelegrambot.dispatcher.newChatMembers
import com.github.kotlintelegrambot.dispatcher.text
import com.github.kotlintelegrambot.entities.ChatId
import java.lang.management.ManagementFactory
import java.time.ZoneOffset
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter

private const val TELEGRAM_MESSAGE_LIMIT = 4096

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
                    val answer =
                        router.answer(receivedText, botUsername, message.chat.id)
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

private fun com.github.kotlintelegrambot.entities.User.displayName(): String =
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
        take(TELEGRAM_MESSAGE_LIMIT - 20) + "\n\n[message truncated]"
    }
