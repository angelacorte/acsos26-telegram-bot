"""Tests for the ACSOS 2026 LLM HTTP service."""

from __future__ import annotations

import sys
from types import ModuleType

from fastapi.testclient import TestClient

import llm_service.app as service
from llm_service.conference_live import LiveChunk, LiveRetrievalResult


class FailingAgent:
    """Agent stub that simulates an unavailable model backend."""

    def invoke(self, payload: dict) -> dict:
        """Raise the same way an unavailable LLM backend would."""
        raise RuntimeError("model backend unavailable")


def test_ask_falls_back_when_agent_fails() -> None:
    """The service must return deterministic data instead of HTTP 500."""
    original_agent = service.agent
    original_disabled_until = service.llm_disabled_until
    service.agent = FailingAgent()
    try:
        response = TestClient(service.app).post(
            "/ask",
            json={"question": "tell me about accepted papers"},
        )
    finally:
        service.agent = original_agent
        service.llm_disabled_until = original_disabled_until

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "fallback"
    assert "accepted papers" in payload["answer"].casefold()
    assert "LLM fallback reason" not in payload["answer"]


def test_ask_skips_agent_during_failure_cooldown() -> None:
    """The service should not reload a broken model on every fallback request."""

    class CountingAgent:
        """Count invocations while simulating a broken backend."""

        calls = 0

        def invoke(self, payload: dict) -> dict:
            """Fail like an unavailable LLM backend."""
            self.calls += 1
            raise RuntimeError("model backend unavailable")

    original_agent = service.agent
    original_disabled_until = service.llm_disabled_until
    counting_agent = CountingAgent()
    service.agent = counting_agent
    service.llm_disabled_until = 0.0
    try:
        client = TestClient(service.app)
        first = client.post("/ask", json={"question": "tell me about accepted papers"}).json()
        second = client.post("/ask", json={"question": "tell me about accepted papers"}).json()
    finally:
        service.agent = original_agent
        service.llm_disabled_until = original_disabled_until

    assert first["mode"] == "fallback"
    assert second["mode"] == "fallback"
    assert counting_agent.calls == 1


def test_structured_questions_bypass_agent() -> None:
    """High-confidence structured answers should not depend on the model."""
    original_agent = service.agent
    service.agent = FailingAgent()
    try:
        response = TestClient(service.app).post(
            "/ask",
            json={"question": "what's the title of angela cortecchia's paper"},
        )
    finally:
        service.agent = original_agent

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "deterministic"
    assert "Multi-Target Tracking via Field-Based Distributed Particle Filtering" in payload["answer"]


def test_paper_location_questions_do_not_match_venue() -> None:
    """Paper schedule questions should not be mistaken for venue questions."""
    response = TestClient(service.app).post(
        "/ask",
        json={"question": "where is Angela Cortecchia's paper?"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "deterministic"
    assert "University of Bologna" not in payload["answer"]
    assert "does not include their day, time, session name, or room yet" in payload["answer"]


def test_common_info_questions_bypass_agent() -> None:
    """Common info-page questions should stay concise and avoid model startup."""
    original_agent = service.agent
    service.agent = FailingAgent()
    try:
        client = TestClient(service.app)
        registration = client.post("/ask", json={"question": "how do I register?"}).json()
        venue = client.post("/ask", json={"question": "where is the conference venue?"}).json()
    finally:
        service.agent = original_agent

    assert registration["mode"] == "deterministic"
    assert "https://cvent.me/RyXPon" in registration["answer"]
    assert "University of Bologna" not in registration["answer"]
    assert venue["mode"] == "deterministic"
    assert "University of Bologna, Cesena Campus" in venue["answer"]
    assert "register" not in venue["answer"].casefold()


def test_conference_date_questions_are_concise_when_agent_fails() -> None:
    """Conference date questions should not dump live page chunks on LLM failure."""
    original_agent = service.agent
    original_disabled_until = service.llm_disabled_until
    service.agent = FailingAgent()
    service.llm_disabled_until = 0.0
    try:
        payload = TestClient(service.app).post(
            "/ask",
            json={"question": "when will be held the conference?"},
        ).json()
    finally:
        service.agent = original_agent
        service.llm_disabled_until = original_disabled_until

    assert payload["mode"] == "deterministic"
    assert payload["answer"] == "ACSOS 2026 will be held Mon 7 - Fri 11 September 2026 in Cesena, Italy."
    assert "Important Dates" not in payload["answer"]
    assert "Newsletter" not in payload["answer"]


def test_live_fallback_does_not_dump_page_chunks() -> None:
    """When live chunks exist but the model is unavailable, fallback stays concise."""
    fallback = service.AskResponse(answer="I do not have a specific answer.", sources=["https://2026.acsos.org/"], mode="deterministic")
    live = LiveRetrievalResult(
        chunks=[
            LiveChunk(
                title="Important Dates",
                text="Important Dates\nWhen\nTrack\nWhat\nWed 15 Jul 2026\nNotification..." * 20,
                source="https://2026.acsos.org/dates",
                score=4,
                fetched_at=123.0,
            ),
        ],
        sources=["https://2026.acsos.org/dates"],
        used_live=True,
    )

    answer = service.deterministic_context_answer("unknown question", [], live, fallback)

    assert answer.mode == "fallback"
    assert "Important Dates\nWhen\nTrack\nWhat" not in answer.answer
    assert "Relevant live source(s): https://2026.acsos.org/dates" in answer.answer


def test_ollama_chat_model_keeps_model_warm(monkeypatch) -> None:
    """Ollama models should be configured to stay loaded between rare requests."""

    class FakeChatOllama:
        """Capture ChatOllama keyword arguments without requiring Ollama."""

        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    fake_module = ModuleType("langchain_ollama")
    fake_module.ChatOllama = FakeChatOllama
    monkeypatch.setitem(sys.modules, "langchain_ollama", fake_module)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama:11434")

    model = service.create_chat_model("ollama:gpt-oss:20b")

    assert isinstance(model, FakeChatOllama)
    assert model.kwargs["model"] == "gpt-oss:20b"
    assert model.kwargs["keep_alive"] == "30m"
    assert model.kwargs["temperature"] == 0.1
    assert model.kwargs["base_url"] == "http://ollama:11434"


def test_deterministic_answers_direct_conference_questions() -> None:
    """Common conference questions should not return unrelated generic chunks."""
    social = service.knowledge.deterministic_answer("which is the Thursday social event?").answer
    assert "ACSOS GP on the Riviera: Racing & Dinner" in social
    assert "Venue: University of Bologna" not in social
    assert "Wine, Views, and Dinner on Romagna" not in social

    keynote = service.knowledge.deterministic_answer("who speaks in the first keynote?").answer
    assert "Marco Dorigo" in keynote
    assert "Bridging Centralized and Decentralized Control" in keynote

    chairs = service.knowledge.deterministic_answer("who are the general chairs?").answer
    assert chairs == "General Chair: Ivana Dusparic, Danilo Pianini"


def test_social_events_are_formatted_for_telegram() -> None:
    """Social event answers should use readable fields instead of dense inline text."""
    social = service.knowledge.deterministic_answer("which are the additional social dinners?").answer

    assert "When: Tuesday, September 8" in social
    assert "Where: Bertinoro" in social
    assert "Fee: €119" in social
    assert "\n\nACSOS GP on the Riviera: Racing & Dinner\nWhen: Thursday, September 10" in social
    assert " - Fee:" not in social


def test_additional_social_activities_return_events_not_organizers() -> None:
    """Generic social-activity questions should list activities, not committee roles."""
    answer = service.knowledge.deterministic_answer("which are the additional social activities?").answer

    assert "Wine, Views, and Dinner on Romagna" in answer
    assert "ACSOS GP on the Riviera: Racing & Dinner" in answer
    assert "Social Experience Chair" not in answer


def test_main_social_event_questions_return_teatro_verdi_not_conference_venue() -> None:
    """Main social event questions should use the dedicated page."""
    event = service.knowledge.deterministic_answer("where will be the main social event")
    dinner = service.knowledge.deterministic_answer("where will be the main social dinner")

    for response in (event, dinner):
        assert response.mode == "deterministic"
        assert response.sources == ["https://2026.acsos.org/attending/main-social-event"]
        assert "Teatro Verdi" in response.answer
        assert "University of Bologna, Cesena Campus" not in response.answer
        assert "Main Track" not in response.answer


def test_person_questions_are_answered_from_known_roles_and_papers() -> None:
    """Person questions should explain why the person appears in the conference data."""
    angela = service.knowledge.deterministic_answer("who is angela cortecchia").answer
    assert "Angela Cortecchia is listed as an author" in angela
    assert "Multi-Target Tracking via Field-Based Distributed Particle Filtering" in angela
    assert "Here is what I found" not in angela

    danilo = service.knowledge.deterministic_answer("who is danilo pianini").answer
    assert "Danilo Pianini is General Chair for ACSOS 2026" in danilo
    assert "University of Bologna Italy" in danilo
    assert "Here is what I found" not in danilo


def test_workshop_questions_are_specific_about_missing_entries() -> None:
    """Workshop questions should not expose raw retrieval chunks."""
    workshops = service.knowledge.deterministic_answer("what are the available workshops?").answer
    assert workshops == (
        "Workshops: Workshop information for ACSOS 2026. "
        "No accepted contributions or timed sessions are listed in the current conference data yet."
    )


def test_live_sources_are_prioritized_in_prompt_and_sources() -> None:
    """Live chunks should be visible to the model and included in final sources."""
    local = [service.Chunk("Registration", "Old local registration text.", "https://2026.acsos.org/attending/Registration")]
    live = LiveRetrievalResult(
        chunks=[
            LiveChunk(
                title="Registration",
                text="Updated live registration text.",
                source="https://2026.acsos.org/attending/Registration",
                score=5,
                fetched_at=123.0,
            ),
        ],
        sources=["https://2026.acsos.org/attending/Registration"],
        used_live=True,
    )

    prompt = service.build_context_prompt("what is the latest registration information?", local, live)
    sources = service.source_urls(local, live, "https://2026.acsos.org")

    assert "Prefer LIVE SOURCE blocks over LOCAL SOURCE blocks" in prompt
    assert "Updated live registration text." in prompt
    assert sources == ["https://2026.acsos.org/attending/Registration"]
