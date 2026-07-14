"""HTTP service for the ACSOS 2026 conference assistant."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from llm_service.conference_live import (
    ConferenceLiveRetriever,
    ConferencePageCache,
    ConferencePageFetcher,
    ConferenceSiteSearch,
    LiveRetrievalResult,
    LiveSearchConfig,
)

DEFAULT_DATA_PATH = Path(__file__).resolve().parents[1] / "src/main/resources/acsos26/conference.json"
DEFAULT_LLM_FAILURE_COOLDOWN_SECONDS = 600.0
DEFAULT_OLLAMA_KEEP_ALIVE = "30m"
DEFAULT_LLM_TEMPERATURE = 0.1
MAX_CONTEXT_CHUNKS = 6
MAX_PROMPT_CONTEXT_CHARS = 7000
LOGGER = logging.getLogger(__name__)
GENERIC_SOCIAL_TERMS = {
    "activities",
    "activity",
    "additional",
    "available",
    "dinner",
    "dinners",
    "event",
    "events",
    "fee",
    "fees",
    "main",
    "social",
    "when",
    "where",
}
STOPWORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "at",
    "be",
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
    "will",
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

    def find_committee_members_by_name(self, query: str) -> list[dict[str, str]]:
        """Find organizing committee members by person name."""
        normalized_query = normalize(query)
        return [
            person
            for person in self.data.get("committees", [])
            if normalize(person["name"]) in normalized_query
        ]

    def find_tracks(self, query: str) -> list[dict[str, Any]]:
        """Find conference tracks mentioned in a user query."""
        query_terms = set(tokenize(query))
        matches = []
        for track in self.data["tracks"]:
            track_terms = set(tokenize(f"{track['id']} {track['command']} {track['name']}"))
            if query_terms & track_terms:
                matches.append(track)
        return matches

    def find_social_events(self, query: str) -> list[dict[str, Any]]:
        """Find social events by title, date, location, or weekday."""
        query_terms = set(tokenize(query))
        if not query_terms:
            return []
        specific_terms = query_terms - GENERIC_SOCIAL_TERMS
        events = []
        for event in self.data.get("socialEvents", []):
            event_terms = set(tokenize(f"{event['title']} {event['whenText']} {event['whereText']}"))
            if specific_terms and specific_terms & event_terms:
                events.append(event)
        return events if specific_terms else self.data.get("socialEvents", [])

    def find_committee_members_by_role(self, query: str) -> list[dict[str, str]]:
        """Find organizing committee members by role."""
        query_terms = set(tokenize(query))
        matches = []
        for person in self.data.get("committees", []):
            role_terms = set(tokenize(person["role"])) - {"chair", "chairs", "co"}
            if role_terms and role_terms <= query_terms:
                matches.append(person)
        return matches

    def find_info_page(self, title: str) -> dict[str, str] | None:
        """Find a conference information page by title."""
        normalized_title = normalize(title)
        for page in self.data.get("infoPages", []):
            if normalize(page["title"]) == normalized_title:
                return page
        return None

    def registration_answer(self, question: str) -> AskResponse | None:
        """Answer direct registration questions without invoking the LLM."""
        query_terms = set(tokenize(question))
        if not query_terms & {"register", "registration", "fee", "fees"}:
            return None
        page = self.find_info_page("Registration")
        if page is None:
            return None
        registration_url = next(iter(re.findall(r"https?://\S+", page["body"])), page["url"])
        return AskResponse(
            answer=(
                "Registration for ACSOS 2026 is open. "
                f"Register here: {registration_url}. "
                "Fees are in USD and include taxes. "
                "For registration assistance, email ieeecs-reg+ACSOS@computer.org."
            ),
            sources=[page["url"]],
            mode="deterministic",
        )

    def conference_dates_answer(self, question: str) -> AskResponse | None:
        """Answer direct questions about when the conference is held."""
        query_terms = set(tokenize(question))
        asks_when = bool(query_terms & {"date", "dates", "held", "when"})
        asks_conference = bool(query_terms & {"acsos", "conference"}) or "conference" in normalize(question)
        asks_deadline = bool(query_terms & {"camera", "deadline", "notification", "submission"})
        if not asks_when or not asks_conference or asks_deadline:
            return None
        return AskResponse(
            answer=f"ACSOS 2026 will be held {self.data['dates']} in {self.data['location']}.",
            sources=[self.data["website"]],
            mode="deterministic",
        )

    def venue_answer(self, question: str) -> AskResponse | None:
        """Answer direct venue and location questions without invoking the LLM."""
        query_terms = set(tokenize(question))
        if query_terms & {"dinner", "social"}:
            return None
        venue_terms = {"venue", "location", "address", "campus", "room", "rooms", "aula", "cesena"}
        asks_conference_location = "where" in query_terms and bool(query_terms & {"acsos", "conference", "event"})
        if not (query_terms & venue_terms or asks_conference_location):
            return None
        page = self.find_info_page("Venue: University of Bologna, Cesena Campus")
        if page is None:
            return None
        return AskResponse(
            answer=(
                "ACSOS 2026 takes place at the University of Bologna, Cesena Campus, "
                "Via dell'Universita, 50, 47521 Cesena, Italy. "
                "The listed conference room is Aula Magna \"Carmen Tura\" (Room 3.4), "
                "on the first floor of the university building."
            ),
            sources=[page["url"]],
            mode="deterministic",
        )

    def main_social_event_answer(self, question: str) -> AskResponse | None:
        """Answer questions about the main conference social event."""
        query_terms = set(tokenize(question))
        social_terms = {"dinner", "event", "reception", "social"}
        if "main" not in query_terms or not query_terms & social_terms:
            return None
        page = self.find_info_page("Main Social Event")
        if page is None:
            return None
        return AskResponse(
            answer=main_social_event_summary(page["body"]),
            sources=[page["url"]],
            mode="deterministic",
        )

    def track_answer(self, question: str) -> AskResponse | None:
        """Answer direct track and workshop questions without invoking the LLM."""
        query_terms = set(tokenize(question))
        asks_for_tracks = query_terms & {"track", "tracks", "workshop", "workshops"}
        if not asks_for_tracks:
            return None
        wants_list = query_terms & {"available", "list", "all"}
        if "tracks" in query_terms and wants_list:
            lines = [f"- {track['name']}: {track['status']}" for track in self.data["tracks"]]
            return AskResponse(
                answer="ACSOS 2026 tracks:\n" + "\n".join(lines),
                sources=sorted({track["url"] for track in self.data["tracks"]}),
                mode="deterministic",
            )
        tracks = self.find_tracks(question)
        if not tracks:
            return None
        lines = [track_summary(track) for track in tracks]
        return AskResponse(
            answer="\n".join(lines),
            sources=sorted({track["url"] for track in tracks}),
            mode="deterministic",
        )

    def person_answer(self, question: str) -> AskResponse | None:
        """Answer direct person questions from conference roles and accepted papers."""
        normalized_question = normalize(question)
        if not re.search(r"\b(who|person|profile)\b", normalized_question):
            return None
        committee_people = self.find_committee_members_by_name(question)
        author_matches = self.find_papers_by_author(question)
        if not committee_people and not author_matches:
            return None

        lines = []
        sources = set()
        for person in committee_people:
            lines.append(f"{person['name']} is {person['role']} for ACSOS 2026. Affiliation: {person['affiliation']}.")
            sources.add(person["url"])
        if author_matches:
            author_name = matched_author_name(question, author_matches)
            paper_lines = []
            for match in author_matches:
                paper = match["paper"]
                track = match["track"]
                paper_lines.append(f"- {paper['title']} ({track['name']})")
                sources.add(track["url"])
            lines.append(f"{author_name} is listed as an author of these accepted ACSOS 2026 papers:\n" + "\n".join(paper_lines))
        return AskResponse(
            answer="\n".join(lines),
            sources=sorted(sources),
            mode="deterministic",
        )

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
        if not any(
            term in normalized_question
            for term in [
                "activity",
                "activities",
                "social",
                "event",
                "events",
                "dinner",
                "dinners",
                "thursday",
                "tuesday",
                "friday",
            ]
        ):
            return None
        events = self.find_social_events(question)
        if not events:
            return None
        prefix = (
            "The current ACSOS 2026 data does not mark one dinner as the main social dinner. "
            "These are the listed dinner/social events:"
            if "main" in set(tokenize(question)) and len(events) > 1
            else "ACSOS 2026 social events:"
            if len(events) > 1
            else ""
        )
        lines = [telegram_social_event_summary(event) for event in events]
        return AskResponse(
            answer="\n\n".join([prefix, *lines] if prefix else lines),
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
            self.main_social_event_answer(question),
            self.social_event_answer(question),
            self.conference_dates_answer(question),
            self.keynote_answer(question),
            self.committee_answer(question),
            self.registration_answer(question),
            self.venue_answer(question),
            self.track_answer(question),
            self.person_answer(question),
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
            schedule_terms = {"where", "when", "room", "time", "session", "day"}
            suffix = (
                "\nThe current conference data does not include their day, time, session name, or room yet."
                if set(tokenize(question)) & schedule_terms
                else ""
            )
            return AskResponse(
                answer="I found these accepted papers by that author:\n" + "\n".join(lines) + suffix,
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


def telegram_social_event_summary(event: dict[str, str]) -> str:
    """Format a social event as a readable Telegram block."""
    lines = [
        event["title"],
        f"When: {event['whenText']}" if event.get("whenText") else "",
        f"Where: {event['whereText']}" if event.get("whereText") else "",
        f"Fee: {event['fee']}" if event.get("fee") else "",
        f"Includes: {event['includes']}" if event.get("includes") else "",
        f"Capacity: {event['capacity']}" if event.get("capacity") else "",
    ]
    return "\n".join(line for line in lines if line)


def main_social_event_summary(body: str) -> str:
    """Format the main social event page as a concise Telegram answer."""
    sentences = split_sentences(body)
    location_sentence = next((sentence for sentence in sentences if "Teatro Verdi" in sentence), sentences[0] if sentences else body)
    walking_sentence = next((sentence for sentence in sentences if "walking" in sentence or "bus" in sentence), "")
    details = " ".join(sentence for sentence in [location_sentence, walking_sentence] if sentence)
    return details or "The ACSOS 2026 main social event details are available on the conference website."


def split_sentences(text: str) -> list[str]:
    """Split compact page text into readable sentences."""
    return [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", text) if sentence.strip()]


def keynote_summary(keynote: dict[str, str]) -> str:
    """Format a concise keynote answer."""
    title = f": {keynote['title']}" if keynote["title"] else ""
    return f"{keynote['speaker']} ({keynote['affiliation']}){title}"


def track_summary(track: dict[str, Any]) -> str:
    """Format a concise track answer."""
    if track["acceptedPapers"]:
        return f"{track['name']}: {track['status']}"
    return (
        f"{track['name']}: {track['summary']} "
        "No accepted contributions or timed sessions are listed in the current conference data yet."
    )


def matched_author_name(query: str, matches: list[dict[str, Any]]) -> str:
    """Return the author name from matches that appears in the query."""
    normalized_query = normalize(query)
    for match in matches:
        for author in match["paper"]["authors"]:
            if normalize(author) in normalized_query:
                return author
    return "This person"


def parse_float_env(name: str, default: float) -> float:
    """Read a float environment variable, falling back to a safe default."""
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        LOGGER.warning("Invalid %s=%r; using %s.", name, value, default)
        return default


def create_chat_model(model_name: str) -> Any:
    """Create the configured chat model, keeping Ollama models warm when possible."""
    if not model_name.startswith("ollama:"):
        return model_name
    try:
        from langchain_ollama import ChatOllama
    except ImportError:
        LOGGER.warning("langchain-ollama is unavailable; falling back to model string.")
        return model_name

    base_url = os.getenv("OLLAMA_BASE_URL") or os.getenv("OLLAMA_HOST")
    model_kwargs: dict[str, Any] = {
        "model": model_name.removeprefix("ollama:"),
        "keep_alive": os.getenv("OLLAMA_KEEP_ALIVE", DEFAULT_OLLAMA_KEEP_ALIVE),
        "temperature": parse_float_env("LLM_TEMPERATURE", DEFAULT_LLM_TEMPERATURE),
    }
    if base_url:
        model_kwargs["base_url"] = base_url
    return ChatOllama(**model_kwargs)


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
        model=create_chat_model(os.getenv("DEEPAGENTS_MODEL", "ollama:gpt-oss:20b")),
        tools=[search_conference_data, lookup_paper, lookup_social_events, lookup_keynotes, lookup_committee_role],
        system_prompt=(
            "You answer questions about ACSOS 2026 only. Use the retrieved ACSOS 2026 sources and the most "
            "specific tool before answering: "
            "lookup_social_events for social events, lookup_keynotes for keynotes, "
            "lookup_committee_role for chairs or committees, lookup_paper for papers, and general search only last. "
            "Prefer live sources over older cached sources when they conflict. If day, time, room, session, date, "
            "speaker, location, or registration data is missing, say what could not be verified; do not infer or "
            "invent details. At the end, include only the most relevant source URLs. Keep answers to at most three "
            "short sentences, and do not include unrelated venue, overview, or track facts."
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
live_config = LiveSearchConfig.from_environment()
live_cache = ConferencePageCache(live_config.cache_path)
live_site_search = ConferenceSiteSearch(live_config, knowledge)
live_fetcher = ConferencePageFetcher(live_config, live_cache)
live_retriever = ConferenceLiveRetriever(live_config, live_site_search, live_fetcher, data_path)
agent = create_agent(knowledge)
llm_disabled_until = 0.0
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


def llm_is_temporarily_disabled() -> bool:
    """Return whether recent backend failures should skip the LLM."""
    return time.monotonic() < llm_disabled_until


def disable_llm_temporarily() -> None:
    """Skip LLM calls for a short cooldown after backend failures."""
    global llm_disabled_until
    cooldown = parse_float_env("LLM_FAILURE_COOLDOWN_SECONDS", DEFAULT_LLM_FAILURE_COOLDOWN_SECONDS)
    if cooldown > 0:
        llm_disabled_until = time.monotonic() + cooldown


def answering_mode() -> str:
    """Return the currently active answering mode."""
    if agent is None:
        return "deterministic"
    if llm_is_temporarily_disabled():
        return "fallback"
    return "deepagents"


def build_context_prompt(
    question: str,
    local_chunks: list[Chunk],
    live_result: LiveRetrievalResult,
) -> str:
    """Build a compact source-grounded prompt for the configured LLM."""
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
    live_note = (
        "Live ACSOS website retrieval was attempted but did not return usable pages in time."
        if live_result.used_live and live_result.error and not live_result.chunks
        else ""
    )
    return (
        "Answer this ACSOS 2026 question using only the source blocks below.\n"
        "Prefer LIVE SOURCE blocks over LOCAL SOURCE blocks if they conflict.\n"
        "Do not invent dates, people, events, places, session details, or registration details.\n"
        "If the sources are insufficient, state what could not be verified.\n"
        "End with a short 'Sources:' list containing only URLs used.\n\n"
        f"{live_note}\n\n"
        f"Question: {question}\n\n"
        f"Sources:\n{context}"
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
) -> AskResponse:
    """Return a source-grounded answer when no LLM is available."""
    if live_result.chunks:
        sources = live_result.sources or source_urls(local_chunks, live_result, knowledge.data["website"])
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


def asks_for_live_verification(question: str) -> bool:
    """Return true when the user explicitly asks for recent or verified data."""
    terms = set(tokenize(question))
    return bool(
        terms
        & {
            "attualmente",
            "current",
            "currently",
            "latest",
            "live",
            "oggi",
            "recent",
            "updated",
            "ultimo",
            "verify",
            "verifica",
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    """Return service health and configured answering mode."""
    return {
        "status": "ok",
        "mode": answering_mode(),
        "data": str(data_path),
        "live_search": "enabled" if live_config.enabled else "disabled",
    }


@app.post("/ask", response_model=AskResponse)
async def ask(
    request: Request,
    x_llm_api_key: str | None = Header(default=None),
) -> AskResponse:
    """Answer an ACSOS 2026 question."""
    verify_llm_api_key(x_llm_api_key)
    ask_request = await parse_ask_request(request)
    local_chunks = knowledge.search(ask_request.question)
    direct_answer = knowledge.high_confidence_answer(ask_request.question)
    if direct_answer is not None and not asks_for_live_verification(ask_request.question):
        return direct_answer
    live_result = await live_retriever.retrieve(ask_request.question, local_chunks)
    if direct_answer is not None and not live_result.chunks:
        return direct_answer
    if agent is None:
        fallback = direct_answer or knowledge.deterministic_answer(ask_request.question)
        return deterministic_context_answer(ask_request.question, local_chunks, live_result, fallback)
    if llm_is_temporarily_disabled():
        fallback = direct_answer or knowledge.deterministic_answer(ask_request.question)
        contextual = deterministic_context_answer(ask_request.question, local_chunks, live_result, fallback)
        return AskResponse(
            answer=contextual.answer,
            sources=contextual.sources,
            mode="fallback",
        )
    try:
        prompt = build_context_prompt(ask_request.question, local_chunks, live_result)
        result = agent.invoke({"messages": [{"role": "user", "content": prompt}]})
        return AskResponse(
            answer=extract_agent_answer(result),
            sources=source_urls(local_chunks, live_result, knowledge.data["website"]),
            mode="deepagents",
        )
    except Exception as error:
        LOGGER.warning("LLM agent failed; using deterministic fallback: %s", error)
        disable_llm_temporarily()
        fallback = direct_answer or knowledge.deterministic_answer(ask_request.question)
        contextual = deterministic_context_answer(ask_request.question, local_chunks, live_result, fallback)
        return AskResponse(
            answer=contextual.answer,
            sources=contextual.sources,
            mode="fallback",
        )
