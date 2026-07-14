package org.angelacorte.acsos26

private const val MAX_PAPERS_IN_COMMAND_REPLY = 8

/**
 * Maps Telegram text messages to conference answers.
 */
internal class CommandRouter(
    private val conference: Conference,
    private val llmClient: LlmClient,
    private val accessControl: AccessControl = AccessControl.disabled(),
) {
    /**
     * Returns the answer for a message, or null when the bot should stay silent.
     */
    fun answer(
        message: String,
        botUsername: String = conference.botUsername,
        chatId: Long? = null,
    ): String? {
        val trimmed = message.trim()
        val command =
            TelegramCommand.parse(trimmed, botUsername)
                ?: return freeFormAnswer(trimmed, botUsername, chatId)
        if (command.name in setOf("start", "auth")) {
            return authenticate(command.argument, chatId)
        }
        if (!accessControl.isAuthorized(chatId)) {
            return privateAccessRequired()
        }
        return when (command.name) {
            "help" -> help()
            "about" -> about()
            "tracks" -> tracks()
            "program" -> program(command.argument)
            "sessions" -> sessions()
            "social" -> social()
            "venue" -> page("venue")
            "registration" -> page("registration")
            "ask" -> ask(command.argument)
            "maintrack" -> track("main")
            "artifacts" -> track("artifacts")
            "doctoral" -> track("doctoral")
            "posters" -> track("posters")
            "tutorials" -> track("tutorials")
            "workshops" -> track("workshops")
            else -> unknownCommand(command.name)
        }
    }

    private fun freeFormAnswer(
        message: String,
        botUsername: String,
        chatId: Long?,
    ): String? {
        if (message.isBlank() || message.startsWith("/")) {
            return null
        }
        if (!accessControl.isAuthorized(chatId)) {
            return privateAccessRequired()
        }
        val question =
            if (message.mentions(botUsername)) {
                message.replace(Regex("@$botUsername", RegexOption.IGNORE_CASE), "").trim()
            } else {
                message
            }
        return if (question.isBlank()) help() else ask(question)
    }

    private fun authenticate(
        key: String,
        chatId: Long?,
    ): String =
        when {
            !accessControl.isEnabled -> help()
            key.isBlank() -> privateAccessRequired()
            accessControl.authorize(chatId, key.trim()) -> "Access granted. Use /help to see the available commands."
            else -> "Invalid access key."
        }

    private fun privateAccessRequired(): String =
        "This bot is private. Send /start <access-key> to enable it in this chat."

    private fun help(): String =
        buildString {
            appendLine("${conference.shortName} bot")
            appendLine()
            conference.commands.forEach { appendLine("${it.command} - ${it.description}") }
            appendLine()
            appendLine("For free-form questions, send the question directly or use /ask followed by the question.")
        }.trim()

    private fun about(): String =
        """
        ${conference.name}
        ${conference.dates}
        ${conference.location}

        ${conference.description}

        Website: ${conference.website}
        """.trimIndent()

    private fun tracks(): String =
        conference.tracks.joinToString(prefix = "Tracks:\n", separator = "\n") {
            "- ${it.name}: /${it.command}"
        }

    private fun program(argument: String): String {
        val normalized = argument.trim().lowercase()
        if (normalized.isBlank() || normalized == "all") {
            return buildString {
                appendLine("Program status")
                appendLine(conference.programStatus)
                appendLine()
                appendLine("Available tracks:")
                conference.tracks.forEach { appendLine("- ${it.name}: /${it.command}") }
                if (conference.sessions.isNotEmpty()) {
                    appendLine()
                    appendLine("Sessions:")
                    conference.sessions.forEach { appendLine("- ${it.summary()}") }
                }
            }.trim()
        }
        return track(normalized)
    }

    private fun track(trackIdOrCommand: String): String {
        val track =
            conference.tracks.firstOrNull {
                it.id == trackIdOrCommand || it.command == trackIdOrCommand
            } ?: return "I do not know that track yet. Use /tracks to see the available tracks."
        val sessions = conference.sessions.filter { it.trackId == track.id }
        return buildString {
            appendLine(track.name)
            appendLine(track.summary)
            appendLine()
            appendLine("Status: ${track.status}")
            if (sessions.isEmpty()) {
                appendLine("Timed sessions and rooms are not available in the bot data yet.")
            } else {
                appendLine("Sessions:")
                sessions.forEach { appendLine("- ${it.summary()}") }
            }
            if (track.acceptedPapers.isNotEmpty()) {
                appendLine()
                appendLine("Accepted papers:")
                track.acceptedPapers.take(MAX_PAPERS_IN_COMMAND_REPLY).forEach {
                    appendLine("- ${it.title}")
                }
                val remaining = track.acceptedPapers.size - MAX_PAPERS_IN_COMMAND_REPLY
                if (remaining > 0) {
                    appendLine("...and $remaining more. Ask /ask about a specific paper title.")
                }
            }
            appendLine()
            appendLine("Details: ${track.url}")
        }.trim()
    }

    private fun sessions(): String =
        if (conference.sessions.isEmpty()) {
            "Session times, rooms, and paper-to-session assignments are not available in the bot data yet."
        } else {
            conference.sessions.joinToString(prefix = "Sessions:\n", separator = "\n") { "- ${it.summary()}" }
        }

    private fun social(): String =
        if (conference.socialEvents.isEmpty()) {
            "Social event details are not available in the bot data yet. Check ${conference.website} for updates."
        } else {
            conference.socialEvents.joinToString(prefix = "Social events:\n", separator = "\n") {
                "- ${it.summary()}"
            }
        }

    private fun page(id: String): String {
        val page =
            conference.infoPages.firstOrNull { it.id == id }
                ?: return "I do not have that information yet."
        return buildString {
            appendLine(page.title)
            appendLine(page.body)
            appendLine()
            appendLine(page.url)
        }.trim()
    }

    private fun ask(question: String): String =
        if (question.isBlank()) {
            "Please add a question after /ask."
        } else {
            llmClient.ask(question).getOrElse {
                "The conference assistant is not available right now. ${it.message.orEmpty()}".trim()
            }
        }

    private fun unknownCommand(command: String): String =
        "Unknown command /$command. Use /help to see the available commands."
}

private data class TelegramCommand(
    val name: String,
    val argument: String,
) {
    companion object {
        fun parse(
            message: String,
            botUsername: String,
        ): TelegramCommand? {
            val trimmed = message.trim()
            if (!trimmed.startsWith("/")) {
                return null
            }
            val commandToken = trimmed.substringBefore(" ")
            val commandName =
                commandToken
                    .removePrefix("/")
                    .substringBefore("@")
                    .lowercase()
            val addressedBot = commandToken.substringAfter("@", missingDelimiterValue = botUsername)
            return if (addressedBot.equals(botUsername, ignoreCase = true)) {
                TelegramCommand(commandName, trimmed.removePrefix(commandToken).trim())
            } else {
                null
            }
        }
    }
}

internal fun String.mentions(botUsername: String): Boolean = contains("@$botUsername", ignoreCase = true)
