"""Deterministic retrieval and answering over the shared conference JSON file.

`ConferenceKnowledge` is the source of truth for everything the service can
answer without a model: a small lexical index (`search`) plus a set of
high-confidence, hand-written answers for the questions users ask most often
(dates, venue, registration, tracks, papers, keynotes, committees, social
events). Keeping these deterministic makes the common paths fast, reliable, and
independent of any LLM backend.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from llm_service.formatting import (
    keynote_summary,
    main_social_event_summary,
    matched_author_name,
    social_event_search_text,
    social_event_summary,
    telegram_social_event_summary,
    track_summary,
)
from llm_service.schemas import AskResponse, Chunk
from llm_service.text import normalize, tokenize

MAX_CONTEXT_CHUNKS = 6
# Common social words that must not, on their own, pin a question to one event.
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
WEEKDAY_TERMS = {"monday", "tuesday", "wednesday", "thursday", "friday"}
EXPLICIT_SOCIAL_TERMS = {
    "activities",
    "activity",
    "banquet",
    "dinner",
    "dinners",
    "event",
    "events",
    "reception",
    "social",
}
PROGRAM_QUERY_TERMS = {
    "agenda",
    "program",
    "programme",
    "schedule",
    "session",
    "sessions",
    "table",
    "time",
    "times",
    "timetable",
    "timing",
}
FULL_PROGRAM_TERMS = {"all", "complete", "full", "whole"}
SEARCH_STOPWORDS = {
    "a", "an", "the", "in", "on", "at", "to", "for", "of", "and", "or", "is", "are",
    "was", "were", "be", "been", "will", "would", "can", "could", "should", "do",
    "does", "did", "what", "which", "who", "whom", "when", "where", "why", "how",
    "this", "that", "these", "those", "it", "its", "by", "from", "with", "about", "as",
}


class ConferenceKnowledge:
    """Small deterministic retrieval layer over the shared conference JSON file."""

    def __init__(self, data_path: Path) -> None:
        self.data_path = data_path
        self.data = json.loads(data_path.read_text(encoding="utf-8"))["conference"]
        self.chunks = self._build_chunks()
        # Terms that appear anywhere OTHER than social events. A social-event term is only
        # "distinctive" (able to trigger a social answer without a social keyword) if it is
        # NOT in here -- otherwise common words like "track" or "papers" would false-match.
        self.non_social_terms = self._build_non_social_terms()

    def search(self, query: str, limit: int = MAX_CONTEXT_CHUNKS) -> list[Chunk]:
        """Return the most relevant conference facts for a user query."""
        all_terms = set(tokenize(query))
        if not all_terms:
            return self.chunks[:limit]
        substantive_terms = all_terms - SEARCH_STOPWORDS
        query_terms = substantive_terms or all_terms
        scored = []
        for chunk in self.chunks:
            title_terms = set(tokenize(chunk.title))
            text_terms = set(tokenize(chunk.text))
            sub_title = len(query_terms & title_terms)
            sub_text = len(query_terms & text_terms)
            all_title = len(all_terms & title_terms)
            all_text = len(all_terms & text_terms)
            score = (5 * sub_title) + (3 * sub_text) + all_title + all_text
            if score:
                scored.append((score, chunk))
        if not scored:
            return []
        return [
            chunk
            for score, chunk in sorted(scored, key=lambda item: item[0], reverse=True)[:limit]
        ]

    def accepted_papers(self) -> list[dict[str, Any]]:
        """Return every accepted paper together with the track it belongs to."""
        return [
            {"title": paper["title"], "authors": paper["authors"], "track": track["name"], "url": track["url"]}
            for track in self.data["tracks"]
            for paper in track["acceptedPapers"]
        ]

    def paper_catalog_text(self) -> str:
        """Render the full accepted-paper list as compact grounding text.

        This is the complete candidate set a model needs to answer topic-filter
        questions ("papers about AI") by meaning rather than by exact wording.
        """
        return "\n".join(
            f"- {paper['title']} — {', '.join(paper['authors'])} ({paper['track']})"
            for paper in self.accepted_papers()
        )

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
        """Find conference tracks mentioned in a user query.

        The generic words "track"/"tracks" are ignored so a phrase like "workshops track"
        does not also match the Main Track just because its name contains "Track".
        """
        generic = {"track", "tracks"}
        query_terms = set(tokenize(query)) - generic
        matches = []
        for track in self.data["tracks"]:
            track_terms = set(tokenize(f"{track['id']} {track['command']} {track['name']}")) - generic
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
            event_terms = set(tokenize(social_event_search_text(event)))
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

    def find_program_days(self, question: str) -> list[dict[str, Any]]:
        """Find program-at-a-glance days explicitly named in a question."""
        query_terms = set(tokenize(question))
        return [
            day
            for day in self.data.get("program", {}).get("days", [])
            if normalize(day.get("day", "")) in query_terms
        ]

    def published_rooms_for_track(self, track: dict[str, Any]) -> list[str]:
        """Return rooms published for program entries belonging to a track."""
        track_id = track.get("id", "")
        rooms: list[str] = []
        for day in self.data.get("program", {}).get("days", []):
            for entry in day.get("entries", []):
                if not program_entry_matches_track(entry, track_id):
                    continue
                for room in program_rooms_for_track(entry.get("room", ""), track_id):
                    if room and room not in rooms:
                        rooms.append(room)
        return rooms

    def published_track_rooms_summary(self, tracks: list[dict[str, Any]]) -> str:
        """Format deduplicated, track-level room information for paper answers."""
        summaries = []
        seen_track_ids = set()
        for track in tracks:
            if track.get("id") in seen_track_ids:
                continue
            seen_track_ids.add(track.get("id"))
            rooms = self.published_rooms_for_track(track)
            if rooms:
                summaries.append(f"{track['name']}: {', '.join(rooms)}")
        return "; ".join(summaries)

    def program_answer(self, question: str) -> AskResponse | None:
        """Answer tentative timetable questions from the structured program-at-a-glance data."""
        query_terms = set(tokenize(question))
        if not query_terms & PROGRAM_QUERY_TERMS or query_terms & EXPLICIT_SOCIAL_TERMS:
            return None
        program = self.data.get("program", {})
        requested_days = self.find_program_days(question)
        if not program or not requested_days:
            return None

        requested_category = program_category_for_query(query_terms)
        wants_full_program = bool(query_terms & FULL_PROGRAM_TERMS) or (
            "program" in query_terms
            and not query_terms & {"schedule", "session", "sessions", "table", "time", "times", "timetable", "timing"}
        )
        category = None if wants_full_program else (requested_category or "main")
        category_name = program_category_name(category)
        day_blocks = []
        for day in requested_days:
            entries = [
                entry
                for entry in day.get("entries", [])
                if (category is None or entry.get("category") == category)
                and entry.get("title", "").strip() not in {"", "—", "-"}
            ]
            if not entries:
                continue
            day_label = ", ".join(part for part in [day.get("day", ""), day.get("date", "")] if part)
            lines = "\n".join(f"- {program_entry_summary(entry)}" for entry in entries)
            day_blocks.append(f"{day_label}:\n{lines}")
        if not day_blocks:
            return None

        heading = f"Tentative {category_name} timetable" if category_name else "Tentative program"
        answer = f"{heading}:\n" + "\n\n".join(day_blocks)
        included_rooms = any(
            entry.get("room")
            for day in requested_days
            for entry in day.get("entries", [])
            if category is None or entry.get("category") == category
        )
        assignment_status = (
            "Individual paper assignments are not published yet"
            if included_rooms
            else "Individual paper assignments and rooms are not published yet"
        )
        answer += f"\n\n{assignment_status}; the timetable is subject to change."
        return AskResponse(
            answer=answer,
            sources=[program["url"]],
            mode="deterministic",
        )

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
        query_terms = set(tokenize(question))
        social_gate = bool(query_terms & EXPLICIT_SOCIAL_TERMS)
        events = self.find_social_events(question)
        if not events:
            return None
        all_events = self.data.get("socialEvents", [])
        if not social_gate:
            # Without a social keyword, only answer when a term that is DISTINCTIVE to social
            # events (e.g. "kart", "karting", "racing") uniquely identifies one event. Common
            # words like "track" or "papers" appear in event bodies but must not trigger this.
            specific_terms = set(tokenize(question)) - GENERIC_SOCIAL_TERMS
            distinctive = specific_terms - self.non_social_terms - WEEKDAY_TERMS - PROGRAM_QUERY_TERMS
            matched = [
                event
                for event in all_events
                if distinctive & set(tokenize(social_event_search_text(event)))
            ]
            if not (distinctive and len(matched) == 1):
                return None
            events = matched
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

    def find_session_for_paper(self, paper_title: str) -> dict[str, Any] | None:
        """Find the timed session hosting an accepted paper."""
        normalized_title = normalize(paper_title)
        for session in self.data.get("sessions", []):
            for paper in session.get("papers", []):
                norm_p = normalize(paper)
                if normalized_title in norm_p or norm_p in normalized_title:
                    return session
        return None

    def paper_count_answer(self, question: str) -> AskResponse | None:
        """Answer 'how many papers' questions deterministically, overall or per track."""
        terms = set(tokenize(question))
        asks_count = ("how" in terms and "many" in terms) or bool(terms & {"count", "number", "total"})
        if not asks_count or not (terms & {"paper", "papers"}):
            return None
        count_stopwords = {
            "how", "many", "count", "number", "total", "paper", "papers", "accepted",
            "in", "the", "are", "there", "track", "tracks", "acsos", "2026", "of",
            "what", "about", "is", "for", "all", "published", "list", "and", "a", "an",
            "to", "do", "we", "have", "current", "currently", "data", "conference",
        }
        topic_terms = terms - count_stopwords
        tracks = self.find_tracks(question)
        for track in tracks:
            topic_terms -= set(tokenize(f"{track['id']} {track['command']} {track['name']}"))
        if topic_terms:
            return None
        if tracks:
            lines = [f"{track['name']}: {len(track.get('acceptedPapers', []))} accepted paper(s)." for track in tracks]
            return AskResponse(
                answer="\n".join(lines),
                sources=sorted({track["url"] for track in tracks}),
                mode="deterministic",
            )
        total = sum(len(track.get("acceptedPapers", [])) for track in self.data["tracks"])
        breakdown = [
            f"- {track['name']}: {len(track['acceptedPapers'])}"
            for track in self.data["tracks"]
            if track.get("acceptedPapers")
        ]
        answer = f"ACSOS 2026 has {total} accepted papers in the current conference data"
        answer += (":\n" + "\n".join(breakdown) + ".") if breakdown else "."
        return AskResponse(answer=answer, sources=[self.data["website"]], mode="deterministic")

    def high_confidence_answer(self, question: str) -> AskResponse | None:
        """Answer structured questions that should bypass generative reasoning."""
        for direct_answer in (
            self.paper_count_answer(question),
            self.main_social_event_answer(question),
            self.program_answer(question),
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
            session = self.find_session_for_paper(paper["title"])
            lines = [
                f"**Title:** {paper['title']}",
                f"• **Track:** {track['name']}",
                f"• **Authors:** {authors}",
            ]
            if session:
                if session.get("title"):
                    lines.append(f"• **Session:** {session['title']}")
                time_info = f"{session['day']} at {session['time']}" if session.get("time") else f"{session['day']}"
                lines.append(f"• **Schedule:** {time_info}")
                if session.get("room"):
                    lines.append(f"• **Room:** {session['room']}")
            else:
                rooms = self.published_track_rooms_summary([track])
                if rooms:
                    lines.append(f"• **Track Rooms:** {rooms}")
                lines.append("• **Schedule:** Exact day, time, and session not yet published.")
            return AskResponse(
                answer="\n".join(lines),
                sources=[track["url"]],
                mode="deterministic",
            )
        author_matches = self.find_papers_by_author(question)
        if author_matches:
            author_name = matched_author_name(question, author_matches)
            heading = f"Accepted papers by **{author_name}**:" if author_name != "This person" else "Accepted papers by author:"
            blocks = []
            sources = set()
            has_scheduled = False
            for match in author_matches:
                paper = match["paper"]
                track = match["track"]
                session = self.find_session_for_paper(paper["title"])
                paper_lines = [
                    f"• **{paper['title']}**",
                    f"  - **Track:** {track['name']}",
                ]
                if session:
                    has_scheduled = True
                    time_info = f"{session['day']} at {session['time']}" if session.get("time") else f"{session['day']}"
                    if session.get("title"):
                        paper_lines.append(f"  - **Session:** {session['title']}")
                    paper_lines.append(f"  - **Schedule:** {time_info}")
                    if session.get("room"):
                        paper_lines.append(f"  - **Room:** {session['room']}")
                blocks.append("\n".join(paper_lines))
                sources.add(track["url"])
            answer = f"{heading}\n\n" + "\n\n".join(blocks)
            schedule_terms = {"where", "when", "room", "time", "session", "day", "present", "schedule", "timetable"}
            if set(tokenize(question)) & schedule_terms and not has_scheduled:
                rooms = self.published_track_rooms_summary([match["track"] for match in author_matches])
                suffix = f"\n\nPublished track-level rooms: {rooms}." if rooms else ""
                suffix += "\nExact day, time, and session are not yet published."
                answer += suffix
            return AskResponse(
                answer=answer,
                sources=sorted(sources),
                mode="deterministic",
            )
        return None

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

    def _build_non_social_terms(self) -> set[str]:
        """Collect tokens from all non-social conference data for distinctiveness checks."""
        parts: list[str] = []
        data = self.data
        for key in ("name", "shortName", "description", "programStatus", "location"):
            parts.append(str(data.get(key, "")))
        for track in data.get("tracks", []):
            parts += [track.get("id", ""), track.get("command", ""), track.get("name", ""), track.get("summary", ""), track.get("status", "")]
            for paper in track.get("acceptedPapers", []):
                parts.append(paper.get("title", ""))
                parts += list(paper.get("authors", []))
        for person in data.get("committees", []):
            parts += [person.get("name", ""), person.get("role", ""), person.get("affiliation", "")]
        for page in data.get("infoPages", []):
            parts += [page.get("title", ""), page.get("body", "")]
        for keynote in data.get("keynotes", []):
            parts += [keynote.get("speaker", ""), keynote.get("title", ""), keynote.get("affiliation", ""), keynote.get("abstract", "")]
        program = data.get("program", {})
        parts += [program.get("title", ""), program.get("status", ""), *program.get("notes", [])]
        for day in program.get("days", []):
            parts += [day.get("day", ""), day.get("date", "")]
            for entry in day.get("entries", []):
                parts += [
                    entry.get("time", ""),
                    entry.get("title", ""),
                    entry.get("details", ""),
                    entry.get("room", ""),
                    entry.get("category", ""),
                ]
        for session in data.get("sessions", []):
            parts.append(session.get("title", ""))
        terms: set[str] = set()
        for part in parts:
            terms.update(tokenize(part))
        return terms

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
        program = self.data.get("program", {})
        for day in program.get("days", []):
            entries = "; ".join(program_entry_summary(entry) for entry in day.get("entries", []))
            chunks.append(
                Chunk(
                    f"Tentative program for {day.get('day', '')}, {day.get('date', '')}",
                    f"{program.get('status', '')} {entries}".strip(),
                    program.get("url", self.data["website"]),
                ),
            )
        for track in self.data["tracks"]:
            chunks.append(Chunk(track["name"], f"{track['summary']} {track['status']}", track["url"]))
            for paper in track["acceptedPapers"]:
                session = self.find_session_for_paper(paper["title"])
                if session:
                    time_info = f" {session['day']} at {session['time']}" if session.get("time") else f" {session['day']}"
                    room_info = f" (Room: {session['room']})" if session.get("room") else ""
                    session_info = f" Scheduled:{time_info} in session '{session['title']}'{room_info}."
                else:
                    rooms = self.published_rooms_for_track(track)
                    session_info = f" Published track-level rooms: {', '.join(rooms)}." if rooms else ""
                chunks.append(
                    Chunk(
                        paper["title"],
                        f"Accepted paper in {track['name']}. Authors: {', '.join(paper['authors'])}.{session_info}",
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


def program_category_for_query(query_terms: set[str]) -> str | None:
    """Map explicit program vocabulary to the corresponding table category."""
    category_terms = (
        ("main", {"main", "paper", "papers"}),
        ("keynote", {"keynote", "keynotes"}),
        ("workshop", {"workshop", "workshops"}),
        ("tutorial", {"tutorial", "tutorials"}),
        ("poster", {"demo", "demos", "poster", "posters"}),
        ("phd", {"doctoral", "phd"}),
        ("panel", {"panel", "panels"}),
    )
    return next((category for category, terms in category_terms if query_terms & terms), None)


def program_category_name(category: str | None) -> str:
    """Return a reader-facing name for a program category."""
    return {
        "main": "Main Track",
        "keynote": "keynote",
        "workshop": "workshop",
        "tutorial": "tutorial",
        "poster": "poster/demo",
        "phd": "Doctoral Symposium",
        "panel": "panel",
    }.get(category, "")


def program_entry_summary(entry: dict[str, Any]) -> str:
    """Format one program-at-a-glance entry for answers and retrieval chunks."""
    details = f" ({entry['details']})" if entry.get("details") else ""
    room = f" — Room: {entry['room']}" if entry.get("room") else ""
    return f"{entry.get('time', '')}: {entry.get('title', '')}{details}{room}".strip()


def program_entry_matches_track(entry: dict[str, Any], track_id: str) -> bool:
    """Match a program entry to the track whose room information it carries."""
    category = entry.get("category", "")
    searchable = normalize(f"{entry.get('title', '')} {entry.get('details', '')}")
    if track_id == "main":
        return category == "main" and "main track session" in searchable
    if track_id == "doctoral":
        return category == "phd" or "doctoral symposium" in searchable
    if track_id == "posters":
        return category == "poster" or "poster" in searchable
    if track_id == "tutorials":
        return category == "tutorial"
    if track_id == "workshops":
        return category == "workshop"
    return False


def program_rooms_for_track(room_text: str, track_id: str) -> list[str]:
    """Select a track's room from compound labels such as ``DS: 2.3 / 2.4``."""
    if not room_text:
        return []
    aliases = {
        "doctoral": {"doctoral symposium", "ds"},
        "posters": {"poster", "posters"},
    }.get(track_id, set())
    unlabeled = []
    matched = []
    for part in re.split(r"\s*·\s*", room_text):
        label, separator, value = part.partition(":")
        if not separator:
            unlabeled.append(part.strip())
        elif normalize(label) in aliases:
            matched.append(value.strip())
    return matched or unlabeled or [room_text.strip()]
