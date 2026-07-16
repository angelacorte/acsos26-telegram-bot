"""Shared pytest fixtures for the ACSOS 2026 LLM service tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from llm_service import app as app_module
from llm_service.config import DEFAULT_DATA_PATH
from llm_service.knowledge import ConferenceKnowledge
from llm_service.pipeline import AnswerService


@pytest.fixture
def anyio_backend() -> str:
    """Run async service tests on the asyncio backend used by the application."""
    return "asyncio"


@pytest.fixture(scope="session")
def knowledge() -> ConferenceKnowledge:
    """Deterministic knowledge base built from the shared conference data."""
    return ConferenceKnowledge(DEFAULT_DATA_PATH)


@pytest.fixture
def service() -> Iterator[AnswerService]:
    """The application's answer service, with agent and cooldown restored after use."""
    svc = app_module.service
    original_agent = svc.agent
    original_disabled_until = svc.llm_disabled_until
    yield svc
    svc.agent = original_agent
    svc.llm_disabled_until = original_disabled_until


@pytest.fixture
def client(service: AnswerService) -> TestClient:
    """HTTP client bound to the FastAPI app; state is restored via ``service``."""
    return TestClient(app_module.app)
