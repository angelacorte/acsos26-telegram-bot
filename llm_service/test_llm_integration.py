"""Opt-in integration tests that exercise a real LLM backend.

These are skipped by default so the fast suite stays hermetic and offline. Enable
them with ``RUN_LLM_TESTS=1`` once an Ollama server (or ``GEMINI_API_KEY``) is
reachable -- for example::

    RUN_LLM_TESTS=1 OLLAMA_BASE_URL=http://localhost:11434 pytest -m llm

The test builds the real generative responder via :func:`create_agent` and drives
a grounded question through the full answer pipeline, asserting the model both
produced text and stayed grounded in the supplied ACSOS 2026 sources.
"""

from __future__ import annotations

import os

import pytest

from llm_service.agents import create_agent
from llm_service.conference_live import LiveRetrievalResult
from llm_service.knowledge import ConferenceKnowledge
from llm_service.pipeline import AnswerService

pytestmark = [
    pytest.mark.llm,
    pytest.mark.anyio,
    pytest.mark.skipif(
        os.getenv("RUN_LLM_TESTS", "").strip().lower() not in {"1", "true", "yes", "on"},
        reason="Set RUN_LLM_TESTS=1 (with a reachable Ollama/Gemini backend) to run LLM integration tests.",
    ),
]


class _NoLiveRetriever:
    """Skip live website retrieval so the test isolates the model itself."""

    async def retrieve(self, question: str, local_chunks: list) -> LiveRetrievalResult:
        return LiveRetrievalResult(chunks=[], sources=[], used_live=False)


async def test_real_backend_answers_a_grounded_open_question(knowledge: ConferenceKnowledge) -> None:
    """A real model should return grounded text for an open-ended question."""
    agent = create_agent(knowledge)
    if agent is None:
        pytest.skip("No LLM backend is configured/available in this environment.")

    service = AnswerService(knowledge, _NoLiveRetriever(), agent)
    response = await service.answer("summarize what ACSOS 2026 is about for a newcomer")

    assert response.mode in {"llm", "fallback"}
    assert response.answer.strip()
    # The model must not have invented an off-topic answer: the sources stay on the ACSOS site.
    assert all("acsos" in source.lower() for source in response.sources)
