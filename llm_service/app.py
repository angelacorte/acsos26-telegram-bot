"""HTTP service for the ACSOS 2026 conference assistant."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

DEFAULT_DATA_PATH = Path(__file__).resolve().parents[1] / "src/main/resources/acsos26/conference.json"
MAX_CONTEXT_CHUNKS = 6
LOGGER = logging.getLogger(__name__)
STOPWORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "at",
    "by",
    "for",
    "from",
    "in",
    "is",
    "of",
    "on",
    "the",
    "this",
    "to",
    "what",
    "which",
    "who",
    "with",
}


class AskRequest(BaseModel):
    """Question received from the Telegram bot."""

    question: str = Field(min_length=1, max_length=1500)


class AskResponse(BaseModel):
    """Answer returned to the Telegram bot."""

    answer: str
    sources: list[str]
    mode: str


@dataclass(frozen=True)
class Chunk:
    """Searchable conference fact."""

    title: str
    text: str
    source: str


class ConferenceKnowledge:
    """Small deterministic retrieval layer over the shared conference JSON file."""

    def __init__(self, data_path: Path) -> None:
        self.data_path = data_path
        self.data = json.loads(data_path.read_text(encoding="utf-8"))["conference"]
        self.chunks = self._build_chunks()

    def search(self, query: str, limit: int = MAX_CONTEXT_CHUNKS) -> list[Chunk]:
        """Return the most relevant conference facts for a user query."""
        query_terms = set(tokenize(query))
        if not query_terms:
            return self.chunks[:limit]
        scored = []
        for chunk in self.chunks:
            title_terms = set(tokenize(chunk.title))
            text_terms = set(tokenize(chunk.text))
            score = (3 * len(query_terms & title_terms)) + len(query_terms & text_terms)
            if score:
                scored.append((score, chunk))
        best_score = max((score for score, _ in scored), default=0)
        if best_score < 2:
            return []
        return [
            chunk
            for score, chunk in sorted(scored, key=lambda item: item[0], reverse=True)[:limit]
            if score >= max(2, best_score - 1)
        ]

    def find_paper(self, query: str) -> dict[str, Any] | None:
        """Find an accepted paper by exact or partial title match."""
        normalized_query = normalize(query)
        for track in self.data["tracks"]:
            for paper in track["acceptedPapers"]:
                title = paper["title"]
                normalized_title = normalize(title)
                if normalized_query in normalized_title or normalized_title in normalized_query:
                    return {"track": track, "paper": paper}
        return None

    def find_papers_by_author(self, query: str) -> list[dict[str, Any]]:
        """Find accepted papers by author name."""
        normalized_query = normalize(query)
        matches = []
        for track in self.data["tracks"]:
            for paper in track["acceptedPapers"]:
                if any(normalize(author) in normalized_query for author in paper["authors"]):
                    matches.append({"track": track, "paper": paper})
        return matches

    def find_social_events(self, query: str) -> list[dict[str, Any]]:
        """Find social events by title, date, location, or weekday."""
        query_terms = set(tokenize(query))
        if not query_terms:
            return []
        events = []
        for event in self.data.get("socialEvents", []):
            event_terms = set(tokenize(f"{event['title']} {event['whenText']} {event['whereText']}"))
            if query_terms & event_terms:
                events.append(event)
        return events

    def find_committee_members_by_role(self, query: str) -> list[dict[str, str]]:
        """Find organizing committee members by role."""
        query_terms = set(tokenize(query))
        matches = []
        for person in self.data.get("committees", []):
            role_terms = set(tokenize(person["role"])) - {"chair", "chairs", "co"}
            if role_terms and role_terms <= query_terms:
                matches.append(person)
        return matches

    def keynote_answer(self, question: str) -> AskResponse | None:
        """Answer direct keynote questions."""
        normalized_question = normalize(question)
        keynotes = self.data.get("keynotes", [])
        if "keynote" not in normalized_question:
            return None
        if not keynotes:
            return AskResponse(
                answer="Keynote information is not available in the ACSOS 2026 data yet.",
                sources=[self.data["website"]],
                mode="deterministic",
            )
        if "first" in normalized_question or "opening" in normalized_question:
            keynote = keynotes[0]
            return AskResponse(
                answer=keynote_summary(keynote),
                sources=[keynote["url"]],
                mode="deterministic",
            )
        lines = [keynote_summary(keynote) for keynote in keynotes]
        return AskResponse(
            answer="ACSOS 2026 keynotes:\n" + "\n".join(f"- {line}" for line in lines),
            sources=sorted({keynote["url"] for keynote in keynotes}),
            mode="deterministic",
        )

    def social_event_answer(self, question: str) -> AskResponse | None:
        """Answer direct social-event questions."""
        normalized_question = normalize(question)
        if not any(term in normalized_question for term in ["social", "event", "dinner", "thursday", "tuesday", "friday"]):
            return None
        events = self.find_social_events(question)
        if not events:
            return None
        lines = [concise_social_event_summary(event) for event in events]
        return AskResponse(
            answer="\n".join(lines),
            sources=[self.data["website"]],
            mode="deterministic",
        )

    def committee_answer(self, question: str) -> AskResponse | None:
        """Answer direct committee-role questions."""
        normalized_question = normalize(question)
        if not any(term in normalized_question for term in ["chair", "committee", "organizer"]):
            return None
        people = self.find_committee_members_by_role(question)
        if not people:
            return None
        grouped = {}
        for person in people:
            grouped.setdefault(person["role"], []).append(person)
        lines = []
        for role, members in grouped.items():
            names = ", ".join(member["name"] for member in members)
            lines.append(f"{role}: {names}")
        return AskResponse(
            answer="\n".join(lines),
            sources=sorted({person["url"] for person in people}),
            mode="deterministic",
        )

    def deterministic_answer(self, question: str) -> AskResponse:
        """Answer from retrieved data without calling an LLM."""
        direct_answer = self.high_confidence_answer(question)
        if direct_answer is not None:
            return direct_answer
        chunks = self.search(question)
        if not chunks:
            return AskResponse(
                answer=(
                    "I do not have a specific answer for that in the ACSOS 2026 data yet. "
                    f"Please check {self.data['website']} for updates."
                ),
                sources=[self.data["website"]],
                mode="deterministic",
            )
        facts = "\n".join(f"- {chunk.title}: {chunk.text}" for chunk in chunks[:2])
        return AskResponse(
            answer=f"Here is what I found in the ACSOS 2026 data:\n{facts}",
            sources=sorted({chunk.source for chunk in chunks}),
            mode="deterministic",
        )

    def high_confidence_answer(self, question: str) -> AskResponse | None:
        """Answer structured questions that should bypass generative reasoning."""
        for direct_answer in (
            self.social_event_answer(question),
            self.keynote_answer(question),
            self.committee_answer(question),
        ):
            if direct_answer is not None:
                return direct_answer
        paper_match = self.find_paper(question)
        if paper_match is not None:
            paper = paper_match["paper"]
            track = paper_match["track"]
            authors = ", ".join(paper["authors"])
            return AskResponse(
                answer=(
                    f"'{paper['title']}' is listed as an accepted paper in {track['name']}."
                    f" Authors: {authors}. The current conference data does not include its day,"
                    " time, session name, or room yet."
                ),
                sources=[track["url"]],
                mode="deterministic",
            )
        author_matches = self.find_papers_by_author(question)
        if author_matches:
            lines = []
            sources = set()
            for match in author_matches:
                paper = match["paper"]
                track = match["track"]
                lines.append(f"- {paper['title']} ({track['name']})")
                sources.add(track["url"])
            return AskResponse(
                answer="I found these accepted papers by that author:\n" + "\n".join(lines),
                sources=sorted(sources),
                mode="deterministic",
            )
        return None

    def _build_chunks(self) -> list[Chunk]:
        chunks = [
            Chunk(
                title="Conference overview",
                text=(
                    f"{self.data['name']} takes place {self.data['dates']} in "
                    f"{self.data['location']}. {self.data['description']}"
                ),
                source=self.data["website"],
            ),
            Chunk(
                title="Program status",
                text=self.data["programStatus"],
                source=self.data["website"],
            ),
        ]
        for page in self.data["infoPages"]:
            chunks.append(Chunk(page["title"], page["body"], page["url"]))
        for track in self.data["tracks"]:
            chunks.append(Chunk(track["name"], f"{track['summary']} {track['status']}", track["url"]))
            for paper in track["acceptedPapers"]:
                chunks.append(
                    Chunk(
                        paper["title"],
                        f"Accepted paper in {track['name']}. Authors: {', '.join(paper['authors'])}.",
                        track["url"],
                    ),
                )
        for event in self.data["socialEvents"]:
            chunks.append(
                Chunk(
                    event["title"],
                    social_event_summary(event),
                    self.data["website"],
                ),
            )
        for keynote in self.data.get("keynotes", []):
            chunks.append(Chunk(f"Keynote: {keynote['speaker']}", keynote_summary(keynote), keynote["url"]))
        for person in self.data.get("committees", []):
            chunks.append(
                Chunk(
                    f"{person['role']}: {person['name']}",
                    f"{person['name']} is {person['role']}. Affiliation: {person['affiliation']}",
                    person["url"],
                ),
            )
        for session in self.data["sessions"]:
            chunks.append(
                Chunk(
                    session["title"],
                    (
                        f"{session['day']} {session['time']} {session['room']} "
                        f"Papers: {', '.join(session['papers'])}"
                    ),
                    self.data["website"],
                ),
            )
        return chunks


def tokenize(text: str) -> list[str]:
    """Split text into lowercase searchable terms."""
    return [term for term in re.findall(r"[a-z0-9]+", normalize(text)) if term not in STOPWORDS]


def normalize(text: str) -> str:
    """Normalize text for robust, dependency-free matching."""
    return text.casefold().replace("-", " ").replace(":", " ")


def social_event_summary(event: dict[str, str]) -> str:
    """Format a concise social event answer."""
    fields = [
        event["title"],
        event["whenText"],
        event["whereText"],
        event["body"],
    ]
    return " - ".join(field for field in fields if field)


def concise_social_event_summary(event: dict[str, str]) -> str:
    """Format a short social event answer for Telegram users."""
    details = [
        event["title"],
        event["whenText"],
        event["whereText"],
    ]
    facts = [
        f"Fee: {event['fee']}" if event.get("fee") else "",
        f"Includes: {event['includes']}" if event.get("includes") else "",
    ]
    return " - ".join([field for field in details if field] + facts)


def keynote_summary(keynote: dict[str, str]) -> str:
    """Format a concise keynote answer."""
    title = f": {keynote['title']}" if keynote["title"] else ""
    return f"{keynote['speaker']} ({keynote['affiliation']}){title}"


def create_agent(knowledge: ConferenceKnowledge) -> Any | None:
    """Create a Deep Agents instance when dependencies and model configuration are available."""
    if os.getenv("DISABLE_DEEPAGENTS", "").lower() in {"1", "true", "yes"}:
        return None
    try:
        from deepagents import create_deep_agent
    except ImportError:
        return None

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

    return create_deep_agent(
        model=os.getenv("DEEPAGENTS_MODEL", "ollama:gpt-oss:20b"),
        tools=[search_conference_data, lookup_paper, lookup_social_events, lookup_keynotes, lookup_committee_role],
        system_prompt=(
            "You answer questions about ACSOS 2026 only. Use the most specific tool before answering: "
            "lookup_social_events for social events, lookup_keynotes for keynotes, "
            "lookup_committee_role for chairs or committees, lookup_paper for papers, and general search only last. "
            "If day, time, room, or session data is missing, say it is not available yet; "
            "do not infer or invent schedule details. Keep answers concise and do not include unrelated venue or overview facts."
        ),
    )


def extract_agent_answer(result: Any) -> str:
    """Extract the final text from a Deep Agents invocation result."""
    if isinstance(result, dict) and result.get("messages"):
        last_message = result["messages"][-1]
        content = getattr(last_message, "content", None)
        if content:
            return str(content)
        if isinstance(last_message, dict) and last_message.get("content"):
            return str(last_message["content"])
    return str(result)


data_path = Path(os.getenv("CONFERENCE_DATA", DEFAULT_DATA_PATH))
knowledge = ConferenceKnowledge(data_path)
agent = create_agent(knowledge)
app = FastAPI(title="ACSOS 2026 conference assistant")


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


@app.get("/health")
def health() -> dict[str, str]:
    """Return service health and configured answering mode."""
    return {
        "status": "ok",
        "mode": "deepagents" if agent is not None else "deterministic",
        "data": str(data_path),
    }


@app.post("/ask", response_model=AskResponse)
async def ask(
    request: Request,
    x_llm_api_key: str | None = Header(default=None),
) -> AskResponse:
    """Answer an ACSOS 2026 question."""
    verify_llm_api_key(x_llm_api_key)
    ask_request = await parse_ask_request(request)
    direct_answer = knowledge.high_confidence_answer(ask_request.question)
    if direct_answer is not None:
        return direct_answer
    if agent is None:
        return knowledge.deterministic_answer(ask_request.question)
    try:
        result = agent.invoke({"messages": [{"role": "user", "content": ask_request.question}]})
        chunks = knowledge.search(ask_request.question)
        return AskResponse(
            answer=extract_agent_answer(result),
            sources=sorted({chunk.source for chunk in chunks}) or [knowledge.data["website"]],
            mode="deepagents",
        )
    except Exception as error:
        LOGGER.warning("LLM agent failed; using deterministic fallback: %s", error)
        fallback = knowledge.deterministic_answer(ask_request.question)
        return AskResponse(
            answer=fallback.answer,
            sources=fallback.sources,
            mode="fallback",
        )
