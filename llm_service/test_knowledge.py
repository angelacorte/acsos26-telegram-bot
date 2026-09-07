"""Tests for deterministic retrieval and high-confidence answers."""

from __future__ import annotations

from datetime import date

from llm_service.config import DEFAULT_DATA_PATH
from llm_service.knowledge import ConferenceKnowledge


def test_deterministic_answers_direct_conference_questions(knowledge: ConferenceKnowledge) -> None:
    """Common conference questions should not return unrelated generic chunks."""
    social = knowledge.deterministic_answer("which is the Thursday social event?").answer
    assert "ACSOS GP on the Riviera: Racing & Dinner" in social
    assert "Venue: University of Bologna" not in social
    assert "Wine, Views, and Dinner on Romagna" not in social

    keynote = knowledge.deterministic_answer("who speaks in the first keynote?").answer
    assert "Marco Dorigo" in keynote
    assert "Bridging Centralized and Decentralized Control" in keynote

    chairs = knowledge.deterministic_answer("who are the general chairs?").answer
    assert chairs == "General Chair: Ivana Dusparic, Danilo Pianini"


def test_social_events_are_formatted_for_telegram(knowledge: ConferenceKnowledge) -> None:
    """Social event answers should use readable fields instead of dense inline text."""
    social = knowledge.deterministic_answer("which are the additional social dinners?").answer

    assert "When: Tuesday, September 8" in social
    assert "Where: Bertinoro" in social
    assert "Fee: €119" in social
    assert "\n\nACSOS GP on the Riviera: Racing & Dinner\nWhen: Thursday, September 10" in social
    assert " - Fee:" not in social


def test_additional_social_activities_return_events_not_organizers(knowledge: ConferenceKnowledge) -> None:
    """Generic social-activity questions should list activities, not committee roles."""
    answer = knowledge.deterministic_answer("which are the additional social activities?").answer

    assert "Wine, Views, and Dinner on Romagna" in answer
    assert "ACSOS GP on the Riviera: Racing & Dinner" in answer
    assert "Social Experience Chair" not in answer


def test_main_social_event_questions_return_teatro_verdi_not_conference_venue(knowledge: ConferenceKnowledge) -> None:
    """Main social event questions should use the dedicated page."""
    event = knowledge.deterministic_answer("where will be the main social event")
    dinner = knowledge.deterministic_answer("where will be the main social dinner")

    for response in (event, dinner):
        assert response.mode == "deterministic"
        assert response.sources == ["https://2026.acsos.org/attending/main-social-event"]
        assert "Teatro Verdi" in response.answer
        assert "University of Bologna, Cesena Campus" not in response.answer
        assert "Main Track" not in response.answer


def test_person_questions_are_answered_from_known_roles_and_papers(knowledge: ConferenceKnowledge) -> None:
    """Person questions should explain why the person appears in the conference data."""
    angela = knowledge.deterministic_answer("who is angela cortecchia").answer
    assert "Angela Cortecchia is listed as an author" in angela
    assert "Multi-Target Tracking via Field-Based Distributed Particle Filtering" in angela
    assert "Here is what I found" not in angela

    danilo = knowledge.deterministic_answer("who is danilo pianini").answer
    assert "Danilo Pianini is General Chair for ACSOS 2026" in danilo
    assert "University of Bologna Italy" in danilo
    assert "Here is what I found" not in danilo


def retrieved(knowledge: ConferenceKnowledge, question: str) -> str:
    """The context the model is grounded on for one question."""
    return "\n".join(f"{chunk.title}\n{chunk.text}" for chunk in knowledge.search(question))


def test_what_is_acsos_reaches_the_model_with_the_description(
    knowledge: ConferenceKnowledge,
) -> None:
    """The overview must be the top source for "what is ACSOS", not an unrelated ACSOS chunk."""
    chunks = knowledge.search("what is ACSOS")

    assert chunks[0].title == "ACSOS 2026 conference overview"
    assert "autonomic computing" in chunks[0].text
    assert "ICAC" in chunks[0].text and "SASO" in chunks[0].text


def test_workshop_questions_retrieve_every_workshop(knowledge: ConferenceKnowledge) -> None:
    """A workshop question must ground the model on all the accepted workshops."""
    context = retrieved(knowledge, "which workshops are there?")

    for workshop in knowledge.data["workshops"]:
        assert workshop["acronym"] in context, workshop["acronym"]


def test_a_workshop_acronym_alone_retrieves_that_workshop(knowledge: ConferenceKnowledge) -> None:
    """"tell me about AI4AS" must surface the AI4AS workshop, not just its sessions."""
    chunks = knowledge.search("tell me about AI4AS")

    assert "AI4AS" in chunks[0].title
    assert "Organizers" in chunks[0].text or "ai4as.github.io" in chunks[0].text


def test_deadline_questions_retrieve_the_deadline_table(knowledge: ConferenceKnowledge) -> None:
    """Deadline questions must ground the model on the scraped deadline table."""
    context = retrieved(knowledge, "when is the camera ready deadline?")

    assert "important dates" in context
    assert "Jul 2026" in context


def test_day_questions_retrieve_that_days_timetable(knowledge: ConferenceKnowledge) -> None:
    """A named day must put that day's schedule in front of the model.

    Lexical scoring alone ranks accepted papers that merely mention the weekday above the
    timetable, which is what made day questions unanswerable.
    """
    chunks = knowledge.search("what is on wednesday?")

    assert chunks[0].title == "Schedule for Wednesday, 9 September"
    assert "Vision Papers" in chunks[0].text
    assert 'Aula Magna "Carmen Tura"' in chunks[0].text


def test_keynotes_are_linked_to_their_scheduled_session(knowledge: ConferenceKnowledge) -> None:
    """Keynote sessions carry no talk rows, so the speaker must be joined to them explicitly."""
    for keynote in knowledge.data["keynotes"]:
        session = knowledge.find_session_for_keynote(keynote)
        assert session is not None, keynote["speaker"]
        assert session["time"] and session["room"]

    context = retrieved(knowledge, "where and when is the Dorigo keynote?")
    assert "Wednesday, 9 September" in context
    assert 'Aula Magna "Carmen Tura"' in context


def test_topic_words_match_across_singular_and_plural(knowledge: ConferenceKnowledge) -> None:
    """"robot swarms" must reach the swarm keynote even though tokens are not stemmed."""
    titles = [chunk.title for chunk in knowledge.search("which keynote is about robot swarms")]

    assert any("Robot Swarm" in title or "Marco Dorigo" in title for title in titles)


def test_paper_count_questions_are_answered_deterministically(knowledge: ConferenceKnowledge) -> None:
    """'How many papers' questions must be counted, not sent to the model or mismatched."""
    overall = knowledge.deterministic_answer("how many accepted papers")
    assert overall.mode == "deterministic"
    assert f"{len(knowledge.accepted_papers())} accepted papers" in overall.answer

    main = knowledge.deterministic_answer("how many papers are accepted in main track?")
    main_track = next(track for track in knowledge.data["tracks"] if track["id"] == "main")
    assert main.answer == f"Main Track: {len(main_track['acceptedPapers'])} accepted paper(s)."

    workshops = knowledge.deterministic_answer("how many papers in the workshops track?")
    workshops_track = next(track for track in knowledge.data["tracks"] if track["id"] == "workshops")
    assert workshops.answer == f"Workshops: {len(workshops_track['acceptedPapers'])} accepted paper(s)."


def test_paper_question_is_not_captured_by_social_events(knowledge: ConferenceKnowledge) -> None:
    """A question about papers must never be answered with a social event (kart) block."""
    answer = knowledge.deterministic_answer("how many papers are accepted in main track?")
    assert "Riviera" not in answer.answer
    assert "kart" not in answer.answer.casefold()


def test_tuesday_timetable_returns_main_track_instead_of_social_event(
    knowledge: ConferenceKnowledge,
) -> None:
    """A weekday schedule question must resolve to paper sessions, not the Tuesday dinner."""
    question = "what is the time table of tuesday"

    answer = knowledge.high_confidence_answer(question)

    assert answer is not None
    assert answer.mode == "deterministic"
    assert "Tuesday, 8 September" in answer.answer
    # Real sessions from the published schedule, with their rooms.
    assert "Conference Opening" in answer.answer
    assert "Aula Magna" in answer.answer
    # The programme is published: the answer must not hedge about it.
    for hedge in ("Tentative", "not published yet", "subject to change", "not available yet"):
        assert hedge not in answer.answer
    assert "Wine, Views, and Dinner" not in answer.answer
    assert "Bertinoro" not in answer.answer
    assert knowledge.social_event_answer(question) is None


def test_distinctive_social_event_terms_are_matched_in_body(knowledge: ConferenceKnowledge) -> None:
    """Questions about a distinctive detail (kart/karting/race) must find the right event."""
    for question in (
        "when is the kart activity?",
        "when is the kart race event?",
        "when is the karting?",
        "quand e la kart activity",
    ):
        answer = knowledge.social_event_answer(question)
        assert answer is not None, question
        assert "Thursday, September 10" in answer.answer, question
        assert "Riccione" in answer.answer, question

    # A single distinctive event must not be shadowed by unrelated deterministic answers.
    assert knowledge.social_event_answer("where is the conference venue?") is None
    assert knowledge.social_event_answer("who are the general chairs?") is None


def test_paper_catalog_lists_every_accepted_paper(knowledge: ConferenceKnowledge) -> None:
    """The catalog is the full candidate set the model filters semantically over."""
    papers = knowledge.accepted_papers()
    catalog = knowledge.paper_catalog_text()

    expected_count = sum(len(track["acceptedPapers"]) for track in knowledge.data["tracks"])
    assert len(papers) == expected_count
    assert catalog.count("\n") == len(papers) - 1
    assert "Multi-Target Tracking via Field-Based Distributed Particle Filtering" in catalog
    # Every catalog line carries the paper's track for grounding.
    assert "(Main Track)" in catalog


def test_paper_location_question_reports_scheduled_session(
    knowledge: ConferenceKnowledge,
) -> None:
    """Paper questions should report the scheduled session and room when available."""
    answer = knowledge.high_confidence_answer("where is Angela Cortecchia's paper?")
    assert answer is not None
    assert answer.mode == "deterministic"
    assert "University of Bologna" not in answer.answer
    assert "Decentralised Coordination and Collective Learning" in answer.answer
    assert "Aula Magna" in answer.answer
    assert "Thursday, 10 September" in answer.answer


def test_today_and_tomorrow_resolve_to_conference_days() -> None:
    """During the conference, "what is on today" must ground the model on that day."""
    knowledge = ConferenceKnowledge(DEFAULT_DATA_PATH, today=lambda: date(2026, 9, 9))

    # The live path: retrieval must hand the model the right day's timetable.
    assert knowledge.search("what is happening today")[0].title == "Schedule for Wednesday, 9 September"
    assert knowledge.search("what is on tomorrow")[0].title == "Schedule for Thursday, 10 September"

    # The model-unavailable path must resolve the same days.
    today = knowledge.high_confidence_answer("what is happening today")
    tomorrow = knowledge.high_confidence_answer("what is on tomorrow")
    assert today is not None and "Wednesday, 9 September" in today.answer
    assert tomorrow is not None and "Thursday, 10 September" in tomorrow.answer


def test_today_outside_the_conference_does_not_invent_a_day() -> None:
    """Away from 7-11 September there is no "today" in the programme."""
    knowledge = ConferenceKnowledge(DEFAULT_DATA_PATH, today=lambda: date(2026, 1, 1))

    assert knowledge.program_answer("what is happening today") is None


def test_the_coarse_program_grid_is_not_indexed(knowledge: ConferenceKnowledge) -> None:
    """Retrieval must use the real session names, not the at-a-glance block labels."""
    titles = [chunk.title for chunk in knowledge.chunks]

    assert not any(title.startswith("Program for ") for title in titles)
    assert "Main-track session" not in " ".join(chunk.text for chunk in knowledge.chunks)


def test_logistics_questions_retrieve_the_right_page(knowledge: ConferenceKnowledge) -> None:
    """Questions use verbs and synonyms; the info pages are titled with the site's nouns."""
    expected = {
        "where do I register and how much does it cost?": "Registration",
        "where can I stay?": "Accommodation",
        "do I need a visa?": "Visa Information",
        "how do I get to Cesena?": "Cesena Campus",
    }
    for question, wanted in expected.items():
        top = knowledge.search(question)[0].title
        assert wanted in top, f"{question!r} retrieved {top!r}"
