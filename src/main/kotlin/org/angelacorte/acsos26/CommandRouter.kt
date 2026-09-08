package org.angelacorte.acsos26

import java.time.LocalDate
import java.time.ZoneId

private const val PRIVATE_CHAT_TYPE = "private"
private const val CATERING_TRACK_ID = "catering"
private const val ASSISTANT_UNAVAILABLE_MESSAGE =
    "The conference assistant is not available right now. Please try again in a moment, " +
        "or use /program, /tracks, /venue and /registration in the meantime."
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
    private val today: () -> LocalDate = { LocalDate.now(ZoneId.of(conference.timezone)) },
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
        val command = TelegramCommand.parse(trimmed, botUsername)
        return when {
            trimmed.isBlank() -> false
            command != null -> command.name == "ask" && command.argument.isNotBlank()
            trimmed.startsWith("/") -> false
            else -> chatType.isPrivateChat() || trimmed.mentions(botUsername) || addressed
        }
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
            "sessions" -> program(command.argument)
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
            "inpractice" -> track("inpractice")
            "socialprogram" -> track("social")
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

    /**
     * Shows today's sessions by default, a named day on request ("/program wed"), and the whole
     * week for "/program all". Anything else is treated as a track name. Session names only:
     * rooms, papers and speakers are left to /ask, which answers them from the same data.
     */
    private fun program(argument: String): String {
        val normalized = argument.trim().lowercase()
        val scheduled = conference.scheduledSessions()
        if (scheduled.isEmpty()) {
            return "The program is not in the bot data. See ${conference.website} for the schedule."
        }
        return when {
            normalized == "all" -> {
                weekProgram(scheduled)
            }

            normalized.isBlank() || normalized == "today" -> {
                dayProgram(scheduled, today().toString()) ?: weekProgram(scheduled)
            }

            else -> {
                resolveDate(scheduled, normalized)?.let { dayProgram(scheduled, it) } ?: track(normalized)
            }
        }
    }

    private fun weekProgram(scheduled: List<Session>): String =
        buildString {
            appendLine("${conference.shortName} program - ${conference.dates}")
            scheduled.groupBy { it.date }.toSortedMap().forEach { (_, daySessions) ->
                appendLine()
                appendLine(daySessions.first().day)
                daySessions.forEach { appendLine(it.compactLine(withRoom = false)) }
            }
            appendLine()
            appendLine(askHint("which papers are presented on Wednesday morning?"))
        }.trim()

    private fun dayProgram(
        scheduled: List<Session>,
        date: String,
    ): String? {
        val daySessions = scheduled.filter { it.date == date }
        if (daySessions.isEmpty()) return null
        val otherDays =
            scheduled
                .filterNot { it.date == date }
                .distinctBy { it.date }
                .sortedBy { it.date }
                .map {
                    it.day
                        .substringBefore(",")
                        .take(3)
                        .lowercase()
                }
        return buildString {
            appendLine("${conference.shortName} program - ${daySessions.first().day}")
            daySessions.forEach { appendLine(it.compactLine()) }
            appendLine()
            if (otherDays.isNotEmpty()) {
                appendLine("Other days: ${otherDays.joinToString(" · ") { "/program $it" }}")
                appendLine("Whole week: /program all")
            }
            appendLine(askHint("what is in room 2.4 after lunch?"))
        }.trim()
    }

    /** Resolves "mon", "monday", "wed" or an ISO date against the days that have sessions. */
    private fun resolveDate(
        scheduled: List<Session>,
        token: String,
    ): String? {
        if (token.length < 3) return null
        return scheduled.firstOrNull { it.date == token }?.date
            ?: scheduled.firstOrNull { it.day.lowercase().startsWith(token) }?.date
    }

    private fun askHint(example: String): String = "Details, rooms and papers: /ask $example"

    private fun track(trackIdOrCommand: String): String {
        val track =
            resolveTrack(trackIdOrCommand)
                ?: return "I do not know that track. Use /tracks to see the available tracks."
        val sessions = conference.sessions.filter { it.trackId == track.id }
        return buildString {
            appendLine(track.name)
            appendLine(track.summary)
            appendLine()
            appendLine(track.status)
            if (sessions.isNotEmpty()) {
                appendLine()
                appendLine("Sessions:")
                sessions.forEach { appendLine("${it.shortDay()}, ${it.time}  ${it.title} - ${it.room}") }
            }
            appendLine()
            appendLine("Details: ${track.url}")
            appendLine(askHint("which papers are in ${track.name}?"))
        }.trim()
    }

    /**
     * Resolves a free-form track token tolerantly: exact id/command, known synonyms
     * (e.g. "poster" -> Posters and Demos, "phd" -> Doctoral Symposium), and loose
     * singular/plural or prefix matches against the id, command, and name words.
     */
    private fun resolveTrack(query: String): Track? {
        val normalized = query.trim().lowercase()
        val exactMatch = conference.tracks.firstOrNull { it.id == normalized || it.command == normalized }
        val synonymMatch =
            TRACK_SYNONYMS[normalized]?.let { id ->
                conference.tracks.firstOrNull { it.id == id }
            }
        val stem = normalized.stripPlural()
        return when {
            normalized.isBlank() -> null
            exactMatch != null -> exactMatch
            synonymMatch != null -> synonymMatch
            normalized.length < 3 -> null
            else -> conference.tracks.firstOrNull { it.matchesLoosely(normalized, stem) }
        }
    }

    private fun social(): String =
        if (conference.socialEvents.isEmpty()) {
            "See ${conference.website} for the social programme."
        } else {
            buildString {
                appendLine("Social events:")
                conference.socialEvents.forEach { appendLine("- ${it.summary()}") }
                appendLine()
                appendLine(askHint("what is included in the banquet?"))
            }.trim()
        }

    private fun page(id: String): String {
        val page =
            conference.infoPages.firstOrNull { it.id == id }
                ?: return "I do not have that page. Ask me directly with /ask, or see ${conference.website}."
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
            llmClient.ask(question).getOrElse { error ->
                System.err.println("LLM request failed: ${error.message.orEmpty()}")
                ASSISTANT_UNAVAILABLE_MESSAGE
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

/** Sessions that belong in the program view: everything except coffee breaks and lunches. */
private fun Conference.scheduledSessions(): List<Session> =
    sessions.filter { it.trackId != CATERING_TRACK_ID && it.title.isNotBlank() }
