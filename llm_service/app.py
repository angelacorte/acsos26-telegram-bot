"""FastAPI HTTP entrypoint for the ACSOS 2026 conference assistant.

This module is intentionally thin: it wires the concrete dependencies together
into an :class:`~llm_service.pipeline.AnswerService`, handles the HTTP concerns
(request parsing, optional API-key auth, a concurrency guard), and exposes the
``/health`` and ``/ask`` endpoints. All answering logic lives in the pipeline
and the modules it depends on.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import ValidationError

from llm_service.agents import create_agent
from llm_service.config import DEFAULT_DATA_PATH, parse_int_env
from llm_service.conference_live import (
    ConferenceLiveRetriever,
    ConferencePageCache,
    ConferencePageFetcher,
    ConferenceSiteSearch,
    LiveSearchConfig,
)
from llm_service.knowledge import ConferenceKnowledge
from llm_service.pipeline import AnswerService
from llm_service.schemas import AskRequest, AskResponse

# Keep bursts from starting enough live retrieval and model work to exhaust Ollama.
DEFAULT_LLM_MAX_CONCURRENT_ASKS = 2


def max_concurrent_asks() -> int:
    """Return the positive global limit for concurrent /ask handlers."""
    return max(1, parse_int_env("LLM_MAX_CONCURRENT_ASKS", DEFAULT_LLM_MAX_CONCURRENT_ASKS))


def build_answer_service(data_path: Path) -> AnswerService:
    """Assemble the answer pipeline and its live-retrieval dependencies."""
    knowledge = ConferenceKnowledge(data_path)
    live_config = LiveSearchConfig.from_environment()
    live_cache = ConferencePageCache(live_config.cache_path)
    live_site_search = ConferenceSiteSearch(live_config, knowledge)
    live_fetcher = ConferencePageFetcher(live_config, live_cache)
    live_retriever = ConferenceLiveRetriever(live_config, live_site_search, live_fetcher, data_path)
    agent = create_agent(knowledge)
    return AnswerService(knowledge, live_retriever, agent)


async def parse_ask_request(request: Request) -> AskRequest:
    """Parse JSON or plain text questions from tolerant HTTP clients."""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            payload = await request.json()
            if isinstance(payload, str):
                return AskRequest(question=payload)
            if isinstance(payload, dict):
                return AskRequest.model_validate(payload)
        except (ValueError, ValidationError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        raise HTTPException(status_code=422, detail="JSON body must be an object with a question field.")
    body = (await request.body()).decode("utf-8", errors="replace").strip()
    if not body:
        raise HTTPException(status_code=422, detail="Request body must include a question.")
    return AskRequest(question=body)


def verify_llm_api_key(api_key: str | None) -> None:
    """Require X-LLM-API-Key when LLM_API_KEY is configured."""
    expected = os.getenv("LLM_API_KEY")
    if expected and api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid LLM API key.")


data_path = Path(os.getenv("CONFERENCE_DATA", DEFAULT_DATA_PATH))
service = build_answer_service(data_path)
ask_semaphore = asyncio.Semaphore(max_concurrent_asks())
app = FastAPI(title="ACSOS 2026 conference assistant")


@app.get("/health")
def health() -> dict[str, str]:
    """Return service health and configured answering mode."""
    return {
        "status": "ok",
        "mode": service.mode,
        "data": str(data_path),
        "live_search": "enabled" if service.live_retriever.config.enabled else "disabled",
    }


@app.post("/ask", response_model=AskResponse)
async def ask(
    request: Request,
    x_llm_api_key: str | None = Header(default=None),
) -> AskResponse:
    """Answer an ACSOS 2026 question."""
    verify_llm_api_key(x_llm_api_key)
    ask_request = await parse_ask_request(request)
    async with ask_semaphore:
        return await service.answer(ask_request.question)
