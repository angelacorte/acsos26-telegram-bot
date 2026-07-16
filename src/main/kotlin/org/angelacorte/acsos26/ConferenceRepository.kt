package org.angelacorte.acsos26

import com.uchuhimo.konf.Config
import com.uchuhimo.konf.ConfigSpec
import com.uchuhimo.konf.Feature

/**
 * Loads versioned conference data used by deterministic commands and by the LLM service.
 */
internal object ConferenceRepository {
    private const val DATA_PATH = "/acsos26/conference.json"

    private object ConferenceSpec : ConfigSpec("") {
        val conference by required<Conference>()
    }

    /**
     * Reads conference data from classpath resources.
     */
    fun load(): Conference =
        checkNotNull(javaClass.getResourceAsStream(DATA_PATH)) {
            "Missing conference data resource: $DATA_PATH"
        }.use { input ->
            Config {
                addSpec(ConferenceSpec)
                disable(Feature.FAIL_ON_UNKNOWN_PATH)
            }.from.json.inputStream(input)[ConferenceSpec.conference]
        }
}

internal data class Conference(
    val name: String,
    val shortName: String,
    val dates: String,
    val location: String,
    val timezone: String,
    val website: String,
    val botUsername: String,
    val programStatus: String,
    val description: String,
    val commands: List<CommandHelp>,
    val tracks: List<Track>,
    val infoPages: List<InfoPage>,
    val socialEvents: List<ConferenceEvent>,
    val sessions: List<Session>,
    val keynotes: List<Keynote>,
    val committees: List<CommitteeMember>,
    val program: ConferenceProgram? = null,
)

internal data class CommandHelp(
    val command: String,
    val description: String,
)

internal data class Track(
    val id: String,
    val command: String,
    val name: String,
    val url: String,
    val status: String,
    val summary: String,
    val acceptedPapers: List<Paper>,
)

internal data class Paper(
    val title: String,
    val authors: List<String>,
)

internal data class InfoPage(
    val id: String,
    val title: String,
    val url: String,
    val body: String,
)

internal data class ConferenceEvent(
    val title: String,
    val whenText: String,
    val whereText: String,
    val fee: String,
    val capacity: String,
    val includes: String,
    val restaurant: String,
    val body: String,
) {
    fun summary(): String =
        listOf(title, whenText, whereText, fee, includes)
            .filter { it.isNotBlank() }
            .joinToString(" - ")
}

internal data class Keynote(
    val speaker: String,
    val affiliation: String,
    val title: String,
    val kind: String,
    val abstract: String,
    val url: String,
)

internal data class CommitteeMember(
    val name: String,
    val role: String,
    val affiliation: String,
    val url: String,
)

internal data class ConferenceProgram(
    val title: String,
    val url: String,
    val status: String,
    val notes: List<String>,
    val days: List<ProgramDay>,
)

internal data class ProgramDay(
    val day: String,
    val date: String,
    val entries: List<ProgramEntry>,
)

internal data class ProgramEntry(
    val time: String,
    val title: String,
    val details: String,
    val category: String,
)

internal data class Session(
    val title: String,
    val trackId: String,
    val day: String,
    val time: String,
    val room: String,
    val papers: List<String>,
) {
    fun summary(): String =
        listOf(day, time, title, room)
            .filter { it.isNotBlank() }
            .joinToString(" - ")
}
