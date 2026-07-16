"""Tests for the answer pipeline and its grounded-prompt helpers."""

from __future__ import annotations

import pytest

from llm_service.conference_live import LiveChunk, LiveRetrievalResult
from llm_service.knowledge import ConferenceKnowledge
from llm_service.pipeline import (
    AnswerService,
    asks_for_live_verification,
    build_context_prompt,
    deterministic_context_answer,
    source_urls,
)
from llm_service.schemas import AskResponse, Chunk


class _StubLiveRetriever:
    """Live retriever that returns a fixed result without any network access."""

    def __init__(self, result: LiveRetrievalResult) -> None:
        self.result = result

    async def retrieve(self, question: str, local_chunks: list) -> LiveRetrievalResult:
        return self.result


def _no_live() -> LiveRetrievalResult:
    return LiveRetrievalResult(chunks=[], sources=[], used_live=False)


def test_asks_for_live_verification_detects_recency_terms() -> None:
    """Explicit recency/verification terms should request a live lookup."""
    assert asks_for_live_verification("what is the latest registration info?")
    assert asks_for_live_verification("please verify the current program")
    assert not asks_for_live_verification("who are the general chairs?")


def test_live_sources_are_prioritized_in_prompt_and_sources() -> None:
    """Live chunks should be visible to the model and included in final sources."""
    local = [Chunk("Registration", "Old local registration text.", "https://2026.acsos.org/attending/Registration")]
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

    prompt = build_context_prompt("what is the latest registration information?", local, live)
    sources = source_urls(local, live, "https://2026.acsos.org")

    assert "Prefer LIVE SOURCE blocks over LOCAL SOURCE blocks" in prompt
    assert "Updated live registration text." in prompt
    assert sources == ["https://2026.acsos.org/attending/Registration"]


def test_prompt_includes_paper_catalog_for_semantic_filtering() -> None:
    """The full paper list must reach the model so it can filter papers by topic."""
    catalog = "- Learning to Adapt: an AI approach — A. Author (Main Track)"
    prompt = build_context_prompt("which papers are about AI?", [], _no_live(), catalog=catalog)

    assert "FULL LIST OF ACCEPTED PAPERS" in prompt
    assert catalog in prompt
    assert "select every match by meaning" in prompt


def test_prompt_enforces_conference_only_scope() -> None:
    """Off-topic questions must be refused with the fixed reply, not answered."""
    prompt = build_context_prompt("what is the capital of France?", [], _no_live())
    assert "I can only answer questions about the ACSOS 2026 conference." in prompt


def test_deterministic_context_answer_does_not_dump_page_chunks() -> None:
    """When live chunks exist but the model is unavailable, the fallback stays concise."""
    fallback = AskResponse(answer="I do not have a specific answer.", sources=["https://2026.acsos.org/"], mode="deterministic")
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

    answer = deterministic_context_answer("unknown question", [], live, fallback, "https://2026.acsos.org/")

    assert answer.mode == "fallback"
    assert "Important Dates\nWhen\nTrack\nWhat" not in answer.answer
    assert "Relevant live source(s): https://2026.acsos.org/dates" in answer.answer


@pytest.mark.anyio
async def test_service_falls_back_when_agent_fails(knowledge: ConferenceKnowledge) -> None:
    """A failing model backend should yield grounded fallback data, never an error."""

    class FailingAgent:
        def invoke(self, payload: dict) -> dict:
            raise RuntimeError("model backend unavailable")

    service = AnswerService(knowledge, _StubLiveRetriever(_no_live()), FailingAgent())
    response = await service.answer("tell me about accepted papers")

    assert response.mode == "fallback"
    assert "accepted papers" in response.answer.casefold()


@pytest.mark.anyio
async def test_service_answers_tuesday_timetable_without_calling_agent(
    knowledge: ConferenceKnowledge,
) -> None:
    """The reported /ask wording should use the structured timetable directly."""

    class CountingAgent:
        calls = 0

        def invoke(self, payload: dict) -> dict:
            self.calls += 1
            return {"messages": [{"content": "wrong model answer"}]}

    agent = CountingAgent()
    service = AnswerService(knowledge, _StubLiveRetriever(_no_live()), agent)

    response = await service.answer("what is the tentative time table of tuesday")

    assert response.mode == "deterministic"
    assert "11:00–13:00: Main-track session" in response.answer
    assert "16:30–18:00: Main-track session" in response.answer
    assert "Bertinoro" not in response.answer
    assert agent.calls == 0


@pytest.mark.anyio
async def test_service_skips_agent_during_failure_cooldown(knowledge: ConferenceKnowledge) -> None:
    """After one failure the broken backend must not be retried during the cooldown."""

    class CountingAgent:
        calls = 0

        def invoke(self, payload: dict) -> dict:
            self.calls += 1
            raise RuntimeError("model backend unavailable")

    agent = CountingAgent()
    service = AnswerService(knowledge, _StubLiveRetriever(_no_live()), agent)

    first = await service.answer("tell me about accepted papers")
    second = await service.answer("tell me about accepted papers")

    assert first.mode == "fallback"
    assert second.mode == "fallback"
    assert agent.calls == 1


@pytest.mark.anyio
async def test_service_mode_reflects_agent_and_cooldown(knowledge: ConferenceKnowledge) -> None:
    """The reported mode should track whether a healthy agent is available."""
    deterministic = AnswerService(knowledge, _StubLiveRetriever(_no_live()), None)
    assert deterministic.mode == "deterministic"

    class Agent:
        def invoke(self, payload: dict) -> dict:
            return {"messages": [{"content": "ok"}]}

    with_agent = AnswerService(knowledge, _StubLiveRetriever(_no_live()), Agent())
    assert with_agent.mode == "llm"
    with_agent._disable_llm_temporarily()
    assert with_agent.mode == "fallback"
