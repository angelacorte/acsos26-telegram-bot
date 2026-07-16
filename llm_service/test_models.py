"""Tests for the Ollama and Gemini chat-model factories."""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

from llm_service import models


def test_ollama_chat_model_keeps_model_warm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ollama models should be configured to stay loaded between rare requests."""

    class FakeChatOllama:
        """Capture ChatOllama keyword arguments without requiring Ollama."""

        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    fake_module = ModuleType("langchain_ollama")
    fake_module.ChatOllama = FakeChatOllama
    monkeypatch.setitem(sys.modules, "langchain_ollama", fake_module)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama:11434")

    model = models.create_chat_model("ollama:gpt-oss:20b")

    assert isinstance(model, FakeChatOllama)
    assert model.kwargs["model"] == "gpt-oss:20b"
    assert model.kwargs["keep_alive"] == "30m"
    assert model.kwargs["temperature"] == 0.1
    assert model.kwargs["base_url"] == "http://ollama:11434"


def test_non_ollama_model_name_is_passed_through() -> None:
    """A plain model string (e.g. a hosted model) should be returned unchanged."""
    assert models.create_chat_model("gemini-2.5-flash") == "gemini-2.5-flash"


def test_gemini_chat_model_uses_key_and_configured_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gemini configuration should use the dedicated env key without changing Ollama."""

    class FakeChatGoogleGenerativeAI:
        """Capture Gemini client arguments without making a network request."""

        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    fake_module = ModuleType("langchain_google_genai")
    fake_module.ChatGoogleGenerativeAI = FakeChatGoogleGenerativeAI
    monkeypatch.setitem(sys.modules, "langchain_google_genai", fake_module)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")

    model = models.create_gemini_chat_model("secret-key")

    assert isinstance(model, FakeChatGoogleGenerativeAI)
    assert model.kwargs["model"] == "gemini-test"
    assert model.kwargs["api_key"] == "secret-key"
    assert model.kwargs["temperature"] == 0.1
    assert model.kwargs["timeout"] == 10.0
    assert model.kwargs["max_retries"] == 0


def test_gemini_chat_model_missing_dependency_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing Gemini integration must not crash; it leaves Ollama active."""
    monkeypatch.setitem(sys.modules, "langchain_google_genai", None)
    assert models.create_gemini_chat_model("secret-key") is None
