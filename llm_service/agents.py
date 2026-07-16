"""Grounded responders and the backend selection that wires them together.

The default responder (`DirectChatAgent`) makes exactly one constrained model
call with the retrieved context already in the prompt -- fast and hard to derail.
`FallbackAgent` layers a cloud model in front of the local one, retrying the
same grounded request locally on any failure. `create_agent` assembles whichever
of these is available given the environment, and can optionally build the
tool-calling Deep Agent behind ``USE_DEEPAGENTS=1``.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from llm_service.formatting import social_event_summary
from llm_service.knowledge import ConferenceKnowledge
from llm_service.models import DEFAULT_MODEL, create_chat_model, create_gemini_chat_model
from llm_service.tools import analyze_structured_data, safe_calculate

LOGGER = logging.getLogger(__name__)

# A fixed reply for anything outside the conference's scope, so off-topic questions
# never reach the model's general knowledge.
OFF_TOPIC_REPLY = "I can only answer questions about the ACSOS 2026 conference."

# Anchor the model to its role; the full grounded context is supplied in the user message.
DIRECT_SYSTEM_PROMPT = (
    "You are the ACSOS 2026 conference assistant. Only answer questions about the ACSOS 2026 "
    "conference, and answer strictly from the source blocks provided in the user message. Never "
    "rely on prior knowledge and never guess. When a question filters conference items by topic "
    "(for example 'papers about AI'), select every matching item from the provided list by meaning, "
    "not just by exact wording. If the question is not about ACSOS 2026, reply exactly: "
    f"'{OFF_TOPIC_REPLY}'. If the answer is not in the sources, say the information is not available "
    "in the ACSOS 2026 data yet. Always answer in English, in at most three short sentences, and "
    "never add unrelated information."
)

_TRUE_FLAGS = {"1", "true", "yes"}


class DirectChatAgent:
    """Single-shot, source-grounded chat over the configured model.

    Unlike a tool-calling agent, this makes exactly one constrained model call with the
    retrieved context already in the prompt. That removes the freewheeling reasoning loop
    that could drift off-topic, and it is markedly faster because there is no tool round-trip.
    """

    def __init__(self, model: Any, system_prompt: str = DIRECT_SYSTEM_PROMPT) -> None:
        self.model = model
        self.system_prompt = system_prompt

    def invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Answer using the provided messages, returning a Deep-Agents-compatible result."""
        messages: list[tuple[str, str]] = [("system", self.system_prompt)]
        for message in payload.get("messages", []):
            role = message.get("role", "user")
            messages.append(("user" if role not in {"system", "assistant"} else role, message.get("content", "")))
        result = self.model.invoke(messages)
        return {"messages": [result]}


class FallbackAgent:
    """Try the configured cloud responder, then transparently retry with Ollama."""

    def __init__(self, primary: Any, fallback: Any) -> None:
        self.primary = primary
        self.fallback = fallback

    def invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Invoke Gemini first and retry the same grounded request locally on failure."""
        try:
            return self.primary.invoke(payload)
        except Exception as error:
            LOGGER.warning("Gemini responder failed; retrying with Ollama (%s).", type(error).__name__)
            return self.fallback.invoke(payload)


def extract_agent_answer(result: Any) -> str:
    """Extract the final user-facing text from a Deep Agents invocation result."""
    if isinstance(result, dict) and result.get("messages"):
        last_message = result["messages"][-1]
        content = getattr(last_message, "content", None)
        if content is None and isinstance(last_message, dict):
            content = last_message.get("content")
        text = message_content_text(content)
        if text:
            return text
    return str(result)


# Reasoning/redacted blocks carry no user-facing answer and must never be surfaced.
_NON_TEXT_BLOCK_TYPES = {"thinking", "reasoning", "redacted_thinking", "tool_use", "tool_result"}


def message_content_text(content: Any) -> str:
    """Flatten a chat message's ``content`` into plain text.

    Backends return ``content`` either as a plain string or as a list of content
    blocks (e.g. a ``{"type": "text", "text": ...}`` block alongside a signed
    reasoning block). Concatenate only the textual blocks so signatures and
    reasoning metadata never leak into the answer.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        return str(content.get("text", "")).strip()
    if isinstance(content, (list, tuple)):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") not in _NON_TEXT_BLOCK_TYPES and block.get("text"):
                parts.append(str(block["text"]))
        return "\n".join(part for part in parts if part).strip()
    return str(content).strip()


def llm_disabled_by_env() -> bool:
    """Return whether the operator has turned the generative assistant off entirely."""
    flags = (os.getenv("DISABLE_LLM", ""), os.getenv("DISABLE_DEEPAGENTS", ""))
    return any(flag.lower() in {"1", "true", "yes"} for flag in flags)


def create_agent(knowledge: ConferenceKnowledge) -> Any | None:
    """Create the generative responder, defaulting to a fast grounded single-shot model call.

    The tool-calling Deep Agent is still available behind USE_DEEPAGENTS=1 for cases that need
    it, but the default favours reliability and latency: one grounded completion that either
    answers from the sources or says the information is unavailable.
    """
    if llm_disabled_by_env():
        return None
    ollama_model = create_chat_model(os.getenv("DEEPAGENTS_MODEL", DEFAULT_MODEL))
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    gemini_model = create_gemini_chat_model(gemini_key) if gemini_key else None
    use_deepagents = os.getenv("USE_DEEPAGENTS", "").lower() in _TRUE_FLAGS

    tools = _build_tools(knowledge)
    system_prompt = _TOOL_SYSTEM_PROMPT

    def responder_for(model: Any) -> Any | None:
        if model is None or isinstance(model, str):
            return None
        if not use_deepagents:
            return DirectChatAgent(model)
        try:
            from deepagents import create_deep_agent
        except Exception as error:
            LOGGER.warning("deepagents unavailable; using the direct grounded responder instead: %s", error)
            return DirectChatAgent(model)
        return create_deep_agent(model=model, tools=tools, system_prompt=system_prompt)

    ollama_responder = responder_for(ollama_model)
    gemini_responder = responder_for(gemini_model)
    if gemini_responder is not None and ollama_responder is not None:
        return FallbackAgent(gemini_responder, ollama_responder)
    if gemini_responder is not None:
        return gemini_responder
    if ollama_responder is None:
        LOGGER.warning("No chat model client available; staying deterministic.")
    return ollama_responder


_TOOL_SYSTEM_PROMPT = (
    "You answer questions about the ACSOS 2026 conference only. If a question is not about ACSOS 2026, "
    f"reply exactly: '{OFF_TOPIC_REPLY}'. Use the most specific tool before answering: "
    "lookup_social_events for social events, lookup_keynotes for keynotes, "
    "lookup_committee_role for chairs or committees, lookup_paper for a single paper by title, "
    "list_accepted_papers to filter papers by topic (e.g. all papers about AI: read the full list and "
    "select the ones whose title matches the topic by meaning), and general search only last. "
    "Use safe_calculate for arithmetic and analyze_structured_data for bounded JSON or CSV parsing; these "
    "tools cannot execute Python, access files or the network, or run shell commands. "
    "Prefer live sources over older cached sources when they conflict. If day, time, room, session, date, "
    "speaker, location, or registration data is missing, say what could not be verified; do not infer or "
    "invent details. At the end, include only the most relevant source URLs. Answer in English, keep answers "
    "to at most three short sentences, and do not include unrelated venue, overview, or track facts."
)


def _build_tools(knowledge: ConferenceKnowledge) -> list[Any]:
    """Build the bounded tool set exposed to the tool-calling agent."""

    def search_conference_data(query: str) -> str:
        """Search ACSOS 2026 facts, papers, tracks, sessions, venue, and social events."""
        chunks = knowledge.search(query)
        if not chunks:
            return "No matching ACSOS 2026 facts were found."
        return "\n\n".join(f"{chunk.title}\n{chunk.text}\nSource: {chunk.source}" for chunk in chunks)

    def lookup_social_events(query: str) -> str:
        """Look up ACSOS 2026 social events by weekday, date, location, or title."""
        events = knowledge.find_social_events(query)
        if not events:
            return "No matching social events were found."
        return "\n".join(social_event_summary(event) for event in events)

    def lookup_keynotes(query: str) -> str:
        """Look up ACSOS 2026 keynote speakers and titles."""
        answer = knowledge.keynote_answer(query)
        if answer is None:
            return "No matching keynote information was found."
        return answer.answer

    def lookup_committee_role(query: str) -> str:
        """Look up ACSOS 2026 organizing committee members by role."""
        answer = knowledge.committee_answer(query)
        if answer is None:
            return "No matching committee role was found."
        return answer.answer

    def lookup_paper(title: str) -> str:
        """Look up an accepted paper by title and return its known schedule metadata."""
        match = knowledge.find_paper(title)
        if match is None:
            return "No matching accepted paper was found in the ACSOS 2026 data."
        paper = match["paper"]
        track = match["track"]
        return (
            f"Title: {paper['title']}\n"
            f"Track: {track['name']}\n"
            f"Authors: {', '.join(paper['authors'])}\n"
            "Schedule: day, time, session, and room are not available in the data yet.\n"
            f"Source: {track['url']}"
        )

    def list_accepted_papers(_: str = "") -> str:
        """List every accepted ACSOS 2026 paper, so papers can be filtered by topic or author."""
        catalog = knowledge.paper_catalog_text()
        return catalog or "No accepted papers are listed in the ACSOS 2026 data yet."

    return [
        search_conference_data,
        lookup_paper,
        list_accepted_papers,
        lookup_social_events,
        lookup_keynotes,
        lookup_committee_role,
        safe_calculate,
        analyze_structured_data,
    ]
