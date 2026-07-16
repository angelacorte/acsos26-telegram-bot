"""Tests for the grounded responders and backend selection."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from llm_service import agents
from llm_service.agents import DirectChatAgent, FallbackAgent, create_agent, extract_agent_answer
from llm_service.knowledge import ConferenceKnowledge


def test_direct_chat_agent_makes_one_grounded_call() -> None:
    """The default responder issues a single system-anchored call and returns its content."""
    captured: dict = {}

    class FakeModel:
        """Capture the messages passed to a single-shot model call."""

        def invoke(self, messages: list) -> SimpleNamespace:
            """Record messages and return a canned answer."""
            captured["messages"] = messages
            return SimpleNamespace(content="risposta breve")

    agent = DirectChatAgent(FakeModel())
    result = agent.invoke({"messages": [{"role": "user", "content": "domanda"}]})

    assert extract_agent_answer(result) == "risposta breve"
    assert captured["messages"][0][0] == "system"
    assert captured["messages"][-1] == ("user", "domanda")


def test_fallback_agent_retries_with_ollama_on_primary_failure() -> None:
    """A failed primary request should transparently retry the same payload locally."""
    calls: list[tuple[str, dict]] = []

    class Primary:
        def invoke(self, payload: dict) -> dict:
            calls.append(("gemini", payload))
            raise RuntimeError("quota exceeded")

    class Fallback:
        def invoke(self, payload: dict) -> dict:
            calls.append(("ollama", payload))
            return {"messages": [SimpleNamespace(content="local answer")]}

    payload = {"messages": [{"role": "user", "content": "question"}]}
    result = FallbackAgent(Primary(), Fallback()).invoke(payload)

    assert extract_agent_answer(result) == "local answer"
    assert calls == [("gemini", payload), ("ollama", payload)]


def test_extract_agent_answer_handles_dict_messages() -> None:
    """Answer extraction should also read dict-shaped messages."""
    assert extract_agent_answer({"messages": [{"content": "hello"}]}) == "hello"


def test_extract_agent_answer_reads_text_from_content_blocks() -> None:
    """List-of-block content must yield its text, never the raw signed structure."""
    result = {
        "messages": [
            SimpleNamespace(
                content=[
                    {"type": "text", "text": "Registration is open.", "extras": {"signature": "SIGN=="}},
                ],
            ),
        ],
    }
    answer = extract_agent_answer(result)
    assert answer == "Registration is open."
    assert "signature" not in answer
    assert "type" not in answer


def test_extract_agent_answer_skips_reasoning_blocks() -> None:
    """Signed reasoning blocks must be dropped; only the final text is returned."""
    result = {
        "messages": [
            SimpleNamespace(
                content=[
                    {"type": "thinking", "thinking": "step by step", "signature": "SIGN=="},
                    {"type": "text", "text": "The keynote is by Marco Dorigo."},
                ],
            ),
        ],
    }
    assert extract_agent_answer(result) == "The keynote is by Marco Dorigo."


def test_deep_agent_receives_bounded_analysis_tools(
    monkeypatch: pytest.MonkeyPatch,
    knowledge: ConferenceKnowledge,
) -> None:
    """Tool mode should expose safe arithmetic and structured-data parsing."""
    captured: dict[str, object] = {}

    def fake_create_deep_agent(**kwargs: object) -> object:
        captured.update(kwargs)
        return SimpleNamespace(invoke=lambda payload: payload)

    fake_module = ModuleType("deepagents")
    fake_module.create_deep_agent = fake_create_deep_agent
    monkeypatch.setitem(sys.modules, "deepagents", fake_module)
    monkeypatch.setenv("USE_DEEPAGENTS", "1")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(agents, "create_chat_model", lambda model_name: object())

    created = create_agent(knowledge)

    assert created is not None
    tools = {tool.__name__: tool for tool in captured["tools"]}
    assert {"safe_calculate", "analyze_structured_data", "list_accepted_papers"} <= set(tools)
    # The paper-listing tool exposes the full catalog so the agent can filter by topic.
    listed = tools["list_accepted_papers"]("")
    assert "Multi-Target Tracking via Field-Based Distributed Particle Filtering" in listed


def test_create_agent_returns_none_when_disabled(monkeypatch: pytest.MonkeyPatch, knowledge: ConferenceKnowledge) -> None:
    """The generative responder must be disabled entirely when the operator asks."""
    monkeypatch.setenv("DISABLE_LLM", "1")
    assert create_agent(knowledge) is None
