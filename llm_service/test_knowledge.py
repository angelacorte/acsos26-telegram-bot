"""Tests for deterministic retrieval and high-confidence answers."""

from __future__ import annotations

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


def test_workshop_questions_are_specific_about_missing_entries(knowledge: ConferenceKnowledge) -> None:
    """Workshop questions should not expose raw retrieval chunks."""
    workshops = knowledge.deterministic_answer("what are the available workshops?").answer
    assert workshops == (
        "Workshops: Workshop information for ACSOS 2026. "
        "No accepted contributions or timed sessions are listed in the current conference data yet."
    )


def test_paper_count_questions_are_answered_deterministically(knowledge: ConferenceKnowledge) -> None:
    """'How many papers' questions must be counted, not sent to the model or mismatched."""
    overall = knowledge.deterministic_answer("how many accepted papers")
    assert overall.mode == "deterministic"
    assert "27 accepted papers" in overall.answer

    main = knowledge.deterministic_answer("how many papers are accepted in main track?")
    assert main.answer == "Main Track: 27 accepted paper(s)."

    workshops = knowledge.deterministic_answer("how many papers in the workshops track?")
    assert workshops.answer == "Workshops: 0 accepted paper(s)."


def test_paper_question_is_not_captured_by_social_events(knowledge: ConferenceKnowledge) -> None:
    """A question about papers must never be answered with a social event (kart) block."""
    answer = knowledge.deterministic_answer("how many papers are accepted in main track?")
    assert "Riviera" not in answer.answer
    assert "kart" not in answer.answer.casefold()


def test_tuesday_timetable_returns_main_track_instead_of_social_event(
    knowledge: ConferenceKnowledge,
) -> None:
    """A weekday schedule question must resolve to paper sessions, not the Tuesday dinner."""
    question = "what is the tentative time table of tuesday"

    answer = knowledge.high_confidence_answer(question)

    assert answer is not None
    assert answer.mode == "deterministic"
    assert answer.sources == ["https://2026.acsos.org/info/program-at-a-glance"]
    assert "Tentative Main Track timetable" in answer.answer
    assert "Tuesday, 8 September" in answer.answer
    assert "11:00–13:00: Main-track session" in answer.answer
    assert "16:30–18:00: Main-track session" in answer.answer
    assert "Individual paper assignments and rooms are not published yet" in answer.answer
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

    assert len(papers) == 27
    assert catalog.count("\n") == len(papers) - 1
    assert "Multi-Target Tracking via Field-Based Distributed Particle Filtering" in catalog
    # Every catalog line carries the paper's track for grounding.
    assert "(Main Track)" in catalog


def test_paper_location_question_reports_missing_schedule(knowledge: ConferenceKnowledge) -> None:
    """Paper schedule questions should not be mistaken for venue questions."""
    answer = knowledge.high_confidence_answer("where is Angela Cortecchia's paper?")
    assert answer is not None
    assert answer.mode == "deterministic"
    assert "University of Bologna" not in answer.answer
    assert "does not include their day, time, session name, or room yet" in answer.answer
