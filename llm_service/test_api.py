"""End-to-end tests for the FastAPI HTTP surface."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from llm_service import app as app_module
from llm_service.pipeline import AnswerService


class FailingAgent:
    """Agent stub that simulates an unavailable model backend."""

    def invoke(self, payload: dict) -> dict:
        """Raise the same way an unavailable LLM backend would."""
        raise RuntimeError("model backend unavailable")


def test_health_reports_mode_and_live_search(client: TestClient) -> None:
    """The health endpoint should describe the active answering mode."""
    payload = client.get("/health").json()
    assert payload["status"] == "ok"
    assert payload["mode"] in {"deterministic", "llm", "fallback"}
    assert payload["live_search"] in {"enabled", "disabled"}


def test_ask_accepts_plain_text_body(client: TestClient) -> None:
    """A tolerant plain-text body should be treated as the question."""
    response = client.post("/ask", content="who are the general chairs?", headers={"content-type": "text/plain"})
    assert response.status_code == 200
    assert "General Chair" in response.json()["answer"]


def test_ask_rejects_empty_body(client: TestClient) -> None:
    """An empty request body should be a clear 422, not a crash."""
    response = client.post("/ask", content="", headers={"content-type": "text/plain"})
    assert response.status_code == 422


def test_ask_falls_back_when_agent_fails(client: TestClient, service: AnswerService) -> None:
    """The service must return deterministic data instead of HTTP 500."""
    service.agent = FailingAgent()
    service.llm_disabled_until = 0.0

    response = client.post("/ask", json={"question": "tell me about accepted papers"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "fallback"
    assert "accepted papers" in payload["answer"].casefold()


def test_ask_skips_agent_during_failure_cooldown(client: TestClient, service: AnswerService) -> None:
    """The service should not reload a broken model on every fallback request."""

    class CountingAgent:
        calls = 0

        def invoke(self, payload: dict) -> dict:
            self.calls += 1
            raise RuntimeError("model backend unavailable")

    counting_agent = CountingAgent()
    service.agent = counting_agent
    service.llm_disabled_until = 0.0

    first = client.post("/ask", json={"question": "tell me about accepted papers"}).json()
    second = client.post("/ask", json={"question": "tell me about accepted papers"}).json()

    assert first["mode"] == "fallback"
    assert second["mode"] == "fallback"
    assert counting_agent.calls == 1


def test_structured_questions_bypass_agent(client: TestClient, service: AnswerService) -> None:
    """High-confidence structured answers should not depend on the model."""
    service.agent = FailingAgent()

    payload = client.post("/ask", json={"question": "what's the title of angela cortecchia's paper"}).json()

    assert payload["mode"] == "deterministic"
    assert "Multi-Target Tracking via Field-Based Distributed Particle Filtering" in payload["answer"]


def test_tuesday_timetable_uses_main_track_program(client: TestClient, service: AnswerService) -> None:
    """The exact reported /ask question must not resolve to the Tuesday social event."""

    class CountingAgent:
        calls = 0

        def invoke(self, payload: dict) -> dict:
            self.calls += 1
            return {"messages": [{"content": "wrong model answer"}]}

    agent = CountingAgent()
    service.agent = agent

    payload = client.post(
        "/ask",
        json={"question": "what is the tentative time table of tuesday"},
    ).json()

    assert payload["mode"] == "deterministic"
    assert "11:00–13:00: Main-track session" in payload["answer"]
    assert "16:30–18:00: Main-track session" in payload["answer"]
    assert "Bertinoro" not in payload["answer"]
    assert agent.calls == 0


def test_common_info_questions_bypass_agent(client: TestClient, service: AnswerService) -> None:
    """Common info-page questions should stay concise and avoid model startup."""
    service.agent = FailingAgent()

    registration = client.post("/ask", json={"question": "how do I register?"}).json()
    venue = client.post("/ask", json={"question": "where is the conference venue?"}).json()

    assert registration["mode"] == "deterministic"
    assert "https://cvent.me/RyXPon" in registration["answer"]
    assert "University of Bologna" not in registration["answer"]
    assert venue["mode"] == "deterministic"
    assert "University of Bologna, Cesena Campus" in venue["answer"]
    assert "register" not in venue["answer"].casefold()


def test_conference_date_questions_are_concise_when_agent_fails(client: TestClient, service: AnswerService) -> None:
    """Conference date questions should not dump live page chunks on LLM failure."""
    service.agent = FailingAgent()
    service.llm_disabled_until = 0.0

    payload = client.post("/ask", json={"question": "when will be held the conference?"}).json()

    assert payload["mode"] == "deterministic"
    assert payload["answer"] == "ACSOS 2026 will be held Mon 7 - Fri 11 September 2026 in Cesena, Italy."
    assert "Important Dates" not in payload["answer"]
    assert "Newsletter" not in payload["answer"]


def test_generation_timeout_falls_back_without_long_cooldown(
    client: TestClient,
    service: AnswerService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow model must not hang the request; it should fall back and recover quickly."""

    class SlowAgent:
        def invoke(self, payload: dict) -> dict:
            time.sleep(1.0)
            return {"messages": [SimpleNamespace(content="too late")]}

    monkeypatch.setenv("LLM_GENERATION_TIMEOUT_SECONDS", "0.1")
    monkeypatch.setenv("LLM_TIMEOUT_COOLDOWN_SECONDS", "0")
    service.agent = SlowAgent()
    service.llm_disabled_until = 0.0

    payload = client.post(
        "/ask",
        json={"question": "summarize the general themes of the conference for a newcomer"},
    ).json()

    assert payload["mode"] == "fallback"
    assert "too late" not in payload["answer"]


def test_ask_requires_api_key_when_configured(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """When LLM_API_KEY is set, requests without the matching header are rejected."""
    monkeypatch.setenv("LLM_API_KEY", "secret")

    unauthorized = client.post("/ask", json={"question": "who are the general chairs?"})
    authorized = client.post(
        "/ask",
        json={"question": "who are the general chairs?"},
        headers={"X-LLM-API-Key": "secret"},
    )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200


def test_max_concurrent_asks_is_always_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid or unsafe concurrency values must retain at least one worker slot."""
    monkeypatch.setenv("LLM_MAX_CONCURRENT_ASKS", "0")
    assert app_module.max_concurrent_asks() == 1
