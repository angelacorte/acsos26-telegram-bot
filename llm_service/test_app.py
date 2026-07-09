"""Tests for the ACSOS 2026 LLM HTTP service."""

from __future__ import annotations

from fastapi.testclient import TestClient

import llm_service.app as service


class FailingAgent:
    """Agent stub that simulates an unavailable model backend."""

    def invoke(self, payload: dict) -> dict:
        """Raise the same way an unavailable LLM backend would."""
        raise RuntimeError("model backend unavailable")


def test_ask_falls_back_when_agent_fails() -> None:
    """The service must return deterministic data instead of HTTP 500."""
    original_agent = service.agent
    service.agent = FailingAgent()
    try:
        response = TestClient(service.app).post(
            "/ask",
            json={"question": "tell me about registration"},
        )
    finally:
        service.agent = original_agent

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "fallback"
    assert "Registration" in payload["answer"]
    assert "LLM fallback reason" not in payload["answer"]


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


def test_deterministic_answers_direct_conference_questions() -> None:
    """Common conference questions should not return unrelated generic chunks."""
    social = service.knowledge.deterministic_answer("which is the Thursday social event?").answer
    assert "ACSOS GP on the Riviera: Racing & Dinner" in social
    assert "Venue: University of Bologna" not in social

    keynote = service.knowledge.deterministic_answer("who speaks in the first keynote?").answer
    assert "Marco Dorigo" in keynote
    assert "Bridging Centralized and Decentralized Control" in keynote

    chairs = service.knowledge.deterministic_answer("who are the general chairs?").answer
    assert chairs == "General Chair: Ivana Dusparic, Danilo Pianini"
