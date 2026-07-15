package org.angelacorte.acsos26

private const val MAX_PAPERS_IN_COMMAND_REPLY = 8
private const val PRIVATE_CHAT_TYPE = "private"
private const val COMMUNITY_LINKS_URL = "https://linktr.ee/acsosconf"
private const val PRIVATE_ACCESS_REQUIRED_MESSAGE =
    "This bot is private. Send /start <access-key> to enable it in this chat."
private const val GROUP_LINK_NOT_CONFIGURED_MESSAGE =
    "The Telegram group invite link is not configured."

private val AUTH_COMMANDS = setOf("start", "auth")

/**
 * Maps Telegram text messages to conference answers.
 */
internal class CommandRouter(
    private val conference: Conference,
    private val llmClient: LlmClient,
    private val accessControl: AccessControl = AccessControl.disabled(),
    private val groupInviteUrl: String = "",
) {
    /**
     * Returns the answer for a message, or null when the bot should stay silent.
     */
    fun answer(
        message: String,
        botUsername: String = conference.botUsername,
        chatId: Long? = null,
        chatType: String = PRIVATE_CHAT_TYPE,
        addressed: Boolean = false,
    ): String? {
        val trimmed = message.trim()
        val command =
            TelegramCommand.parse(trimmed, botUsername)
                ?: return freeFormAnswer(trimmed, botUsername, chatId, chatType, addressed)
        return commandAnswer(command, chatId)
    }

    /**
     * Returns true when a message will reach the (slower) LLM assistant, so the caller can show a
     * "typing" indicator. Errs on the side of showing it; a spurious indicator is harmless.
     * [addressed] is true when the message is a reply to the bot (another way of "tagging" it).
     */
    fun triggersAssistant(
        message: String,
        botUsername: String = conference.botUsername,
        chatType: String = PRIVATE_CHAT_TYPE,
        addressed: Boolean = false,
    ): Boolean {
        val trimmed = message.trim()
        if (trimmed.isBlank()) return false
        val command = TelegramCommand.parse(trimmed, botUsername)
        if (command != null) return command.name == "ask" && command.argument.isNotBlank()
        if (trimmed.startsWith("/")) return false
        return chatType.isPrivateChat() || trimmed.mentions(botUsername) || addressed
    }

    private fun commandAnswer(
        command: TelegramCommand,
        chatId: Long?,
    ): String =
        when {
            command.name in AUTH_COMMANDS -> authenticate(command.argument, chatId)
            !accessControl.isAuthorized(chatId) -> PRIVATE_ACCESS_REQUIRED_MESSAGE
            else -> publicCommandAnswer(command)
        }

    private fun publicCommandAnswer(command: TelegramCommand): String =
        generalCommandAnswer(command)
            ?: trackCommandAnswer(command.name)
            ?: unknownCommand(command.name)

    private fun generalCommandAnswer(command: TelegramCommand): String? =
        when (command.name) {
            "help" -> help()
            "about" -> about()
            "site" -> conference.website
            "links" -> COMMUNITY_LINKS_URL
            "group" -> groupInviteUrl.ifBlank { GROUP_LINK_NOT_CONFIGURED_MESSAGE }
            "tracks" -> tracks()
            "program" -> program(command.argument)
            "sessions" -> sessions()
            "social" -> social()
            "venue" -> page("venue")
            "registration" -> page("registration")
            "ask" -> ask(command.argument)
            else -> null
        }

    private fun trackCommandAnswer(command: String): String? =
        when (command) {
            "maintrack" -> track("main")
            "artifacts" -> track("artifacts")
            "doctoral" -> track("doctoral")
            "posters" -> track("posters")
            "tutorials" -> track("tutorials")
            "workshops" -> track("workshops")
            else -> null
        }

    private fun freeFormAnswer(
        message: String,
        botUsername: String,
        chatId: Long?,
        chatType: String,
        addressed: Boolean,
    ): String? {
        val mentionsBot = message.mentions(botUsername)
        val addressedToBot = mentionsBot || addressed
        return when {
            message.isBlank() || message.startsWith("/") -> null
            !chatType.isPrivateChat() && !addressedToBot -> null
            !accessControl.isAuthorized(chatId) -> PRIVATE_ACCESS_REQUIRED_MESSAGE
            else -> freeFormQuestionAnswer(message, botUsername, mentionsBot)
        }
    }

    private fun freeFormQuestionAnswer(
        message: String,
        botUsername: String,
        addressedToBot: Boolean,
    ): String {
        val question =
            if (addressedToBot) {
                message.withoutMention(botUsername)
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
            key.isBlank() -> PRIVATE_ACCESS_REQUIRED_MESSAGE
            accessControl.authorize(chatId, key.trim()) -> "Access granted. Use /help to see the available commands."
            else -> "Invalid access key."
        }

    private fun help(): String =
        buildString {
            appendLine("${conference.shortName} bot")
            appendLine()
            conference.commands.forEach { appendLine("${it.command} - ${it.description}") }
            appendLine()
            appendLine("For free-form questions, send the question directly in private chats.")
            appendLine("In groups, mention @${conference.botUsername} or use /ask followed by the question.")
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
            resolveTrack(trackIdOrCommand)
                ?: return "I do not know that track yet. Use /tracks to see the available tracks."
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

    /**
     * Resolves a free-form track token tolerantly: exact id/command, known synonyms
     * (e.g. "poster" -> Posters and Demos, "phd" -> Doctoral Symposium), and loose
     * singular/plural or prefix matches against the id, command, and name words.
     */
    private fun resolveTrack(query: String): Track? {
        val normalized = query.trim().lowercase()
        if (normalized.isBlank()) return null
        conference.tracks.firstOrNull { it.id == normalized || it.command == normalized }?.let { return it }
        TRACK_SYNONYMS[normalized]?.let { id ->
            conference.tracks.firstOrNull { it.id == id }?.let { return it }
        }
        if (normalized.length < 3) return null
        val stem = normalized.stripPlural()
        return conference.tracks.firstOrNull { it.matchesLoosely(normalized, stem) }
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

private val TRACK_SYNONYMS =
    mapOf(
        "main" to "main",
        "maintrack" to "main",
        "paper" to "main",
        "papers" to "main",
        "artifact" to "artifacts",
        "artifacts" to "artifacts",
        "ae" to "artifacts",
        "doctoral" to "doctoral",
        "phd" to "doctoral",
        "symposium" to "doctoral",
        "ds" to "doctoral",
        "poster" to "posters",
        "posters" to "posters",
        "demo" to "posters",
        "demos" to "posters",
        "tutorial" to "tutorials",
        "tutorials" to "tutorials",
        "workshop" to "workshops",
        "workshops" to "workshops",
    )

private val TRACK_NAME_STOPWORDS = setOf("and", "the", "of", "for", "on")

private fun String.stripPlural(): String = if (length > 3 && endsWith("s")) dropLast(1) else this

private fun Track.matchesLoosely(
    query: String,
    stem: String,
): Boolean {
    val keys =
        (listOf(id, command) + name.lowercase().split(Regex("[^a-z0-9]+")))
            .filter { it.length >= 3 && it !in TRACK_NAME_STOPWORDS }
    return keys.any { key ->
        key == query || key.stripPlural() == stem || key.startsWith(query) || query.startsWith(key)
    }
}

internal fun String.mentions(botUsername: String): Boolean = contains("@$botUsername", ignoreCase = true)

private fun String.withoutMention(botUsername: String): String =
    replace(Regex("@${Regex.escape(botUsername)}\\b", RegexOption.IGNORE_CASE), "").trim()

private fun String.isPrivateChat(): Boolean = equals(PRIVATE_CHAT_TYPE, ignoreCase = true)
