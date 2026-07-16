"""The answer pipeline that ties retrieval, live lookup, and the LLM together.

`AnswerService` owns one request's worth of decision-making: try a
high-confidence deterministic answer first, optionally enrich with a bounded
live-website lookup, then fall back through the generative model and, on any
failure or timeout, back to grounded local data. It also owns the short cooldown
that stops a broken or slow backend from being retried on every request.
"""

from __future__ import annotations

import asyncio
import logging
import time

from llm_service.agents import extract_agent_answer
from llm_service.config import parse_float_env
from llm_service.conference_live import ConferenceLiveRetriever, LiveRetrievalResult
from llm_service.knowledge import ConferenceKnowledge
from llm_service.schemas import AskResponse, Chunk
from llm_service.text import tokenize

LOGGER = logging.getLogger(__name__)

# A single hard backend failure should not silence the assistant for long: recover in ~1 minute.
DEFAULT_LLM_FAILURE_COOLDOWN_SECONDS = 60.0
# A slow generation should back off only briefly so a transient stall does not disable the model.
DEFAULT_LLM_TIMEOUT_COOLDOWN_SECONDS = 20.0
# Cap how long one answer may take server-side so the request never hangs to the client timeout.
DEFAULT_LLM_GENERATION_TIMEOUT_SECONDS = 30.0
MAX_PROMPT_CONTEXT_CHARS = 7000

# Terms that signal the user explicitly wants recent/verified data from the live site.
LIVE_VERIFICATION_TERMS = {
    "current",
    "currently",
    "latest",
    "live",
    "recent",
    "updated",
    "verify",
}


class AnswerService:
    """Answer one ACSOS 2026 question through the full bounded pipeline."""

    def __init__(
        self,
        knowledge: ConferenceKnowledge,
        live_retriever: ConferenceLiveRetriever,
        agent: object | None,
    ) -> None:
        self.knowledge = knowledge
        self.live_retriever = live_retriever
        self.agent = agent
        # Monotonic deadline before which the LLM is skipped after a recent failure.
        self.llm_disabled_until = 0.0

    @property
    def website(self) -> str:
        """Return the canonical conference website used as a source of last resort."""
        return self.knowledge.data["website"]

    @property
    def mode(self) -> str:
        """Return the currently active answering mode."""
        if self.agent is None:
            return "deterministic"
        if self._llm_is_temporarily_disabled():
            return "fallback"
        return "llm"

    async def answer(self, question: str) -> AskResponse:
        """Run one bounded answer pipeline for an already-validated question."""
        local_chunks = self.knowledge.search(question)
        direct_answer = self.knowledge.high_confidence_answer(question)
        if direct_answer is not None and not asks_for_live_verification(question):
            return direct_answer
        live_result = await self.live_retriever.retrieve(question, local_chunks)
        if direct_answer is not None and not live_result.chunks:
            return direct_answer
        if self.agent is None or self._llm_is_temporarily_disabled():
            return self._fallback_answer(question, direct_answer, local_chunks, live_result)
        return await self._generate_answer(question, direct_answer, local_chunks, live_result)

    async def _generate_answer(
        self,
        question: str,
        direct_answer: AskResponse | None,
        local_chunks: list[Chunk],
        live_result: LiveRetrievalResult,
    ) -> AskResponse:
        """Call the model under a hard timeout, cooling it down on failure."""
        prompt = build_context_prompt(
            question,
            local_chunks,
            live_result,
            catalog=self.knowledge.paper_catalog_text(),
        )
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(self.agent.invoke, {"messages": [{"role": "user", "content": prompt}]}),
                timeout=generation_timeout_seconds(),
            )
            return AskResponse(
                answer=extract_agent_answer(result),
                sources=source_urls(local_chunks, live_result, self.website),
                mode="llm",
            )
        except (asyncio.TimeoutError, TimeoutError):
            LOGGER.warning("LLM generation timed out after %.1fs; using fallback.", generation_timeout_seconds())
            self._disable_llm_after_timeout()
        except Exception as error:
            LOGGER.warning("LLM agent failed; using deterministic fallback: %s", error)
            self._disable_llm_temporarily()
        return self._fallback_answer(question, direct_answer, local_chunks, live_result)

    def _fallback_answer(
        self,
        question: str,
        direct_answer: AskResponse | None,
        local_chunks: list[Chunk],
        live_result: LiveRetrievalResult,
    ) -> AskResponse:
        """Return the best grounded answer available without the model."""
        fallback = direct_answer or self.knowledge.deterministic_answer(question)
        contextual = deterministic_context_answer(question, local_chunks, live_result, fallback, self.website)
        if self.agent is None:
            return contextual
        return AskResponse(answer=contextual.answer, sources=contextual.sources, mode="fallback")

    def _llm_is_temporarily_disabled(self) -> bool:
        """Return whether recent backend failures should skip the LLM."""
        return time.monotonic() < self.llm_disabled_until

    def _disable_llm_temporarily(self) -> None:
        """Skip LLM calls for a short cooldown after a hard backend failure."""
        self._extend_cooldown("LLM_FAILURE_COOLDOWN_SECONDS", DEFAULT_LLM_FAILURE_COOLDOWN_SECONDS)

    def _disable_llm_after_timeout(self) -> None:
        """Back off only briefly after a slow generation so the model recovers quickly."""
        self._extend_cooldown("LLM_TIMEOUT_COOLDOWN_SECONDS", DEFAULT_LLM_TIMEOUT_COOLDOWN_SECONDS)

    def _extend_cooldown(self, cooldown_env: str, default: float) -> None:
        """Extend the LLM cooldown window without ever shortening an existing one."""
        cooldown = parse_float_env(cooldown_env, default)
        if cooldown > 0:
            self.llm_disabled_until = max(self.llm_disabled_until, time.monotonic() + cooldown)


def generation_timeout_seconds() -> float:
    """Return the server-side cap on how long one generative answer may take."""
    return parse_float_env("LLM_GENERATION_TIMEOUT_SECONDS", DEFAULT_LLM_GENERATION_TIMEOUT_SECONDS)


def asks_for_live_verification(question: str) -> bool:
    """Return true when the user explicitly asks for recent or verified data."""
    return bool(set(tokenize(question)) & LIVE_VERIFICATION_TERMS)


def build_context_prompt(
    question: str,
    local_chunks: list[Chunk],
    live_result: LiveRetrievalResult,
    catalog: str = "",
) -> str:
    """Build a compact source-grounded prompt for the configured LLM.

    ``catalog`` is the full accepted-paper list; including it lets the model
    answer topic-filter questions ("papers about AI") from the complete set
    rather than only the lexically retrieved chunks.
    """
    context_blocks = []
    for chunk in local_chunks:
        context_blocks.append(f"LOCAL SOURCE\nTitle: {chunk.title}\nURL: {chunk.source}\nText: {chunk.text}")
    for chunk in live_result.chunks:
        context_blocks.append(
            "LIVE SOURCE\n"
            f"Title: {chunk.title}\n"
            f"URL: {chunk.source}\n"
            f"Fetched at unix time: {chunk.fetched_at:.0f}\n"
            f"Text: {chunk.text}",
        )
    context = "\n\n---\n\n".join(context_blocks)
    if len(context) > MAX_PROMPT_CONTEXT_CHARS:
        context = context[:MAX_PROMPT_CONTEXT_CHARS] + "\n[context truncated]"
    catalog_section = (
        "\n\nFULL LIST OF ACCEPTED PAPERS (use it to answer questions that filter papers by topic, "
        f"e.g. AI, by meaning):\n{catalog}"
        if catalog
        else ""
    )
    live_note = (
        "Live ACSOS website retrieval was attempted but did not return usable pages in time."
        if live_result.used_live and live_result.error and not live_result.chunks
        else ""
    )
    return (
        "Answer this ACSOS 2026 question using ONLY the source blocks below.\n"
        "Only answer questions about the ACSOS 2026 conference; if this question is not about ACSOS 2026, "
        "reply exactly: 'I can only answer questions about the ACSOS 2026 conference.'\n"
        "Do not use any outside or prior knowledge, and do not guess.\n"
        "Prefer LIVE SOURCE blocks over LOCAL SOURCE blocks if they conflict.\n"
        "When the question filters items by topic, select every match by meaning, not just exact wording.\n"
        "Do not invent dates, people, events, places, session details, or registration details.\n"
        "If the sources do not contain the answer, reply only that the information is not available "
        "in the ACSOS 2026 data yet and suggest checking https://2026.acsos.org/ ; do not improvise.\n"
        "Reply in English.\n"
        "Keep the answer to at most three short sentences and stay strictly on the asked topic.\n"
        "End with a short 'Sources:' list containing only URLs you actually used.\n\n"
        f"{live_note}\n\n"
        f"Question: {question}\n\n"
        f"Sources:\n{context}{catalog_section}"
    )


def source_urls(local_chunks: list[Chunk], live_result: LiveRetrievalResult, fallback: str) -> list[str]:
    """Return response sources, prioritizing live pages when present."""
    urls = {chunk.source for chunk in local_chunks}
    urls.update(live_result.sources)
    return sorted(urls) or [fallback]


def deterministic_context_answer(
    question: str,
    local_chunks: list[Chunk],
    live_result: LiveRetrievalResult,
    fallback: AskResponse,
    website: str,
) -> AskResponse:
    """Return a source-grounded answer when no LLM is available."""
    if live_result.chunks:
        sources = live_result.sources or source_urls(local_chunks, live_result, website)
        return AskResponse(
            answer=(
                f"{fallback.answer}\n\n"
                "I could not generate a concise live-verified answer before the model failed, "
                "so this answer uses the local conference data. "
                "Relevant live source(s): "
                + ", ".join(sources[:3])
            ),
            sources=sources,
            mode="fallback",
        )
    if live_result.used_live and live_result.error:
        return AskResponse(
            answer=(
                f"{fallback.answer}\n\n"
                "I could not verify the ACSOS website live in time, so this answer uses the local conference data."
            ),
            sources=fallback.sources,
            mode="fallback",
        )
    return fallback
