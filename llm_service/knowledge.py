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
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

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
CATERING_TRACK_ID = "catering"
DATES_URL = "https://2026.acsos.org/dates"
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
INFO_PAGE_SEARCH_ALIASES = {
    "registration": "register registering fee fees cost costs price prices payment pay how much",
    "venue": "where address location directions campus building rooms map",
    "travel": "travel airport airports train bus car getting there transport arrive",
    "accommodation": "hotel hotels stay sleep booking lodging where to stay",
    "visa": "visa visas invitation letter passport embassy",
    "codeOfConduct": "code conduct harassment",
    "visitCesena": "visit tourism sightseeing attractions",
    "welcomeReception": "welcome reception drinks opening evening",
    "mainSocialEvent": "banquet dinner gala main social event evening",
}
SEARCH_STOPWORDS = {
    "a", "an", "the", "in", "on", "at", "to", "for", "of", "and", "or", "is", "are",
    "was", "were", "be", "been", "will", "would", "can", "could", "should", "do",
    "does", "did", "what", "which", "who", "whom", "when", "where", "why", "how",
    "this", "that", "these", "those", "it", "its", "by", "from", "with", "about", "as",
}


class ConferenceKnowledge:
    """Small deterministic retrieval layer over the shared conference JSON file."""

    def __init__(self, data_path: Path, today: Callable[[], date] | None = None) -> None:
        self.data_path = data_path
        self.data = json.loads(data_path.read_text(encoding="utf-8"))["conference"]
        self._today = today or self._conference_today
        self.chunks = self._build_chunks()
        # Terms that appear anywhere OTHER than social events. A social-event term is only
        # "distinctive" (able to trigger a social answer without a social keyword) if it is
        # NOT in here -- otherwise common words like "track" or "papers" would false-match.
        self.non_social_terms = self._build_non_social_terms()

    def search(self, query: str, limit: int = MAX_CONTEXT_CHUNKS) -> list[Chunk]:
        """Return the most relevant conference facts for a user query.

        Days named in the question are answered from the schedule itself rather than left to
        lexical scoring, which otherwise ranks accepted papers that merely mention the weekday
        above the timetable for that day.
        """
        day_chunks = self.day_schedule_chunks(query)
        limit = max(limit - len(day_chunks), 1)
        return day_chunks + self._lexical_search(query, limit)

    def day_schedule_chunks(self, query: str) -> list[Chunk]:
        """Build one schedule chunk per conference day named in the query."""
        chunks = []
        for date in self.find_program_days(query):
            sessions = self.scheduled_sessions(date)
            if not sessions:
                continue
            timetable = "; ".join(
                f"{session['time']} {session['title']}"
                + (f" in {session['room']}" if session.get("room") else "")
                for session in sessions
            )
            chunks.append(
                Chunk(
                    f"Schedule for {sessions[0].get('day', date)}",
                    f"Sessions on {sessions[0].get('day', date)}: {timetable}.",
                    self.data.get("program", {}).get("url") or self.data["website"],
                ),
            )
        return chunks

    def _lexical_search(self, query: str, limit: int) -> list[Chunk]:
        """Rank chunks by token overlap with the query."""
        all_terms = set(tokenize(query))
        if not all_terms:
            return self.chunks[:limit]
        substantive_terms = all_terms - SEARCH_STOPWORDS
        query_terms = substantive_terms or all_terms
        query_stems = stems(query_terms)
        scored = []
        for chunk in self.chunks:
            title_terms = set(tokenize(chunk.title))
            text_terms = set(tokenize(chunk.text))
            sub_title = len(query_terms & title_terms)
            sub_text = len(query_terms & text_terms)
            all_title = len(all_terms & title_terms)
            all_text = len(all_terms & text_terms)
            # Singular/plural only, scored below exact matches: "swarm robotics" must still reach
            # a keynote titled "... Robot Swarms", which exact tokens never match.
            stem_title = len(query_stems & stems(title_terms))
            stem_text = len(query_stems & stems(text_terms))
            score = (
                (5 * sub_title)
                + (3 * sub_text)
                + all_title
                + all_text
                + (2 * stem_title)
                + stem_text
            )
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

    def _conference_today(self) -> date:
        """Today's date in the conference time zone."""
        return datetime.now(ZoneInfo(self.data.get("timezone", "Europe/Rome"))).date()

    def find_program_days(self, question: str) -> list[str]:
        """Find the dates of the schedule days named in a question, including today/tomorrow."""
        query_terms = set(tokenize(question))
        scheduled_dates = {session.get("date", "") for session in self.data.get("sessions", [])}
        matched: set[str] = set()
        if query_terms & {"today", "tonight"}:
            matched.add(self._today().isoformat())
        if "tomorrow" in query_terms:
            matched.add((self._today() + timedelta(days=1)).isoformat())
        for session in self.data.get("sessions", []):
            weekday = normalize(session.get("day", "").partition(",")[0])
            if weekday and weekday in query_terms:
                matched.add(session.get("date", ""))
        return sorted(matched & scheduled_dates)

    def scheduled_sessions(self, date: str = "", track_id: str = "") -> list[dict[str, Any]]:
        """Return schedule sessions, excluding catering, optionally filtered by day and track."""
        return [
            session
            for session in self.data.get("sessions", [])
            if session.get("trackId") != CATERING_TRACK_ID
            and (not date or session.get("date") == date)
            and (not track_id or session.get("trackId") == track_id)
        ]

    def track_name(self, track_id: str) -> str:
        """Return the display name of a track id."""
        return next(
            (track["name"] for track in self.data.get("tracks", []) if track.get("id") == track_id),
            "",
        )

    def program_answer(self, question: str) -> AskResponse | None:
        """Answer timetable questions from the published session schedule."""
        query_terms = set(tokenize(question))
        requested_dates = self.find_program_days(question)
        # Naming a conference day is itself a schedule question ("what is on wednesday?"),
        # unless the question is clearly about the social programme.
        if not requested_dates or query_terms & EXPLICIT_SOCIAL_TERMS:
            return None
        track_id = program_track_for_query(query_terms)
        wants_everything = bool(query_terms & FULL_PROGRAM_TERMS)
        blocks = []
        for date in requested_dates:
            sessions = self.scheduled_sessions(date, "" if wants_everything else (track_id or ""))
            if not sessions:
                continue
            lines = "\n".join(f"- {session_schedule_line(session)}" for session in sessions)
            blocks.append(f"{sessions[0].get('day', date)}:\n{lines}")
        if not blocks:
            return None
        track_name = "" if wants_everything else self.track_name(track_id or "")
        heading = f"{track_name} schedule" if track_name else "ACSOS 2026 schedule"
        return AskResponse(
            answer=f"{heading}\n\n" + "\n\n".join(blocks),
            sources=[self.data.get("program", {}).get("url") or self.data["website"]],
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
                f"Register for ACSOS 2026 here: {registration_url}. "
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
        venue = self.data.get("venue", {})
        lines = [f"ACSOS 2026 takes place at the {venue.get('name') or self.data['location']}."]
        if venue.get("address"):
            lines.append(f"- **Address:** {venue['address']}")
        if venue.get("mainRoom"):
            lines.append(f"- **Main room:** {venue['mainRoom']}")
        if venue.get("rooms"):
            lines.append(f"- **Rooms in use:** {', '.join(venue['rooms'])}")
        return AskResponse(
            answer="\n".join(lines),
            sources=[venue.get("url") or page["url"]],
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
                answer="I do not have the keynote details. See https://2026.acsos.org/info/keynotes.",
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

    def find_session_for_keynote(self, keynote: dict[str, Any]) -> dict[str, Any] | None:
        """Find the timed session hosting a keynote.

        Keynote sessions carry no talk rows on the programme page, so the speaker is never
        attached to a time and room by the scrape. The session name holds either the talk
        title ("Keynote: Bridging Centralized ...") or the speaker ("Keynote: Valeria
        Cardellini"), so match on both.
        """
        title = normalize(keynote.get("title", ""))
        speaker = normalize(keynote.get("speaker", ""))
        for session in self.data.get("sessions", []):
            session_title = normalize(session.get("title", ""))
            if "keynote" not in session_title:
                continue
            if (title and title in session_title) or (speaker and speaker in session_title):
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
                lines.append(
                    "• **Schedule:** not listed in a timed session; "
                    f"see {track['url']} for the track programme.",
                )
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
                answer += "\n\nThese contributions are not listed in a timed session."
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
                    "I could not find that in the ACSOS 2026 data. "
                    f"Please check {self.data['website']}."
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
                # Names ACSOS so it wins "what is ACSOS", and reads as content if the model
                # echoes it back as a heading.
                title="ACSOS 2026 conference overview",
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
            # Document expansion: the page titles use the site's nouns ("Registration", "Venue")
            # while questions use verbs and synonyms ("where do I register", "how much does it
            # cost"). Tokens are not stemmed, so "register" never matches "Registration" on its
            # own; these aliases give each page the vocabulary people actually type.
            aliases = INFO_PAGE_SEARCH_ALIASES.get(page["id"], "")
            title = f"{page['title']} ({aliases})" if aliases else page["title"]
            chunks.append(Chunk(title, page["body"], page["url"]))
        # The Program at a Glance grid is deliberately NOT indexed: it names blocks
        # ("Main-track session", "Coffee break") that the real sessions below name properly,
        # and retrieving the vaguer copy made answers worse.
        for track in self.data["tracks"]:
            chunks.append(Chunk(track["name"], f"{track['summary']} {track['status']}", track["url"]))
            for paper in track["acceptedPapers"]:
                session = self.find_session_for_paper(paper["title"])
                if session:
                    time_info = f" {session['day']} at {session['time']}" if session.get("time") else f" {session['day']}"
                    room_info = f" (Room: {session['room']})" if session.get("room") else ""
                    session_info = f" Scheduled:{time_info} in session '{session['title']}'{room_info}."
                else:
                    session_info = ""
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
        keynote_speakers: dict[str, list[str]] = {}
        for keynote in self.data.get("keynotes", []):
            session = self.find_session_for_keynote(keynote)
            schedule = ""
            if session is not None:
                room = f" in room {session['room']}" if session.get("room") else ""
                schedule = f" Scheduled: {session.get('day', '')} at {session.get('time', '')}{room}."
                keynote_speakers.setdefault(session_key(session), []).append(keynote["speaker"])
            chunks.append(
                Chunk(
                    f"Keynote: {keynote['speaker']}",
                    f"{keynote_summary(keynote)}{schedule}",
                    keynote["url"],
                ),
            )
        for person in self.data.get("committees", []):
            chunks.append(
                Chunk(
                    f"{person['role']}: {person['name']}",
                    f"{person['name']} is {person['role']}. Affiliation: {person['affiliation']}",
                    person["url"],
                ),
            )
        for session in self.data["sessions"]:
            talks = "; ".join(
                f"{talk.get('time', '')} {talk['title']}"
                + (f" by {', '.join(talk['speakers'])}" if talk.get("speakers") else "")
                for talk in session.get("talks", [])
            )
            papers = f" Papers: {', '.join(session['papers'])}." if session.get("papers") else ""
            speakers = keynote_speakers.get(session_key(session), [])
            speaker_text = f" Speaker: {', '.join(speakers)}." if speakers else ""
            chunks.append(
                Chunk(
                    session["title"],
                    (
                        f"{session['day']} {session['time']} in room {session['room']}."
                        f"{speaker_text}{papers}" + (f" Talks: {talks}" if talks else "")
                    ),
                    self.data["website"],
                ),
            )
        chunks.extend(self._build_extra_chunks())
        return chunks

    def _build_extra_chunks(self) -> list[Chunk]:
        """Build chunks for the logistics data the deterministic answers do not always cover."""
        chunks: list[Chunk] = []
        venue = self.data.get("venue", {})
        if venue:
            chunks.append(
                Chunk(
                    "Venue address and rooms",
                    (
                        f"{venue.get('name', '')}, {venue.get('address', '')}. "
                        f"Main room: {venue.get('mainRoom', '')}. "
                        f"Rooms in use: {', '.join(venue.get('rooms', []))}."
                    ),
                    venue.get("url", self.data["website"]),
                ),
            )
        deadlines = self.data.get("importantDates", [])
        if deadlines:
            rows = "; ".join(
                f"{row.get('date', '')} - {row.get('track', '')}: {row.get('what', '')}"
                for row in deadlines
            )
            chunks.append(
                Chunk(
                    "ACSOS 2026 important dates, deadlines and notifications",
                    f"Submission and camera-ready deadlines per track: {rows}.",
                    DATES_URL,
                ),
            )
        for workshop in self.data.get("workshops", []):
            chunks.append(
                Chunk(
                    (
                        f"ACSOS 2026 Workshops - {workshop.get('acronym', '')}: "
                        f"{workshop.get('name', '')} (workshop)"
                    ),
                    (
                        f"{workshop.get('summary', '')} "
                        f"Organizers: {', '.join(workshop.get('organizers', []))}. "
                        f"Website: {workshop.get('site', '')}."
                    ),
                    workshop.get("url", self.data["website"]),
                ),
            )
        for seminar in self.data.get("seminarSeries", []):
            chunks.append(
                Chunk(
                    f"Seminar: {seminar.get('title', '')}",
                    (
                        f"{seminar.get('speaker', '')} ({seminar.get('affiliation', '')}) "
                        f"on {seminar.get('whenText', '')}."
                    ),
                    seminar.get("url", self.data["website"]),
                ),
            )
        for item in self.data.get("news", []):
            chunks.append(
                Chunk(
                    f"News: {item.get('title', '')}",
                    f"{item.get('date', '')}. {item.get('summary', '')}",
                    item.get("url", self.data["website"]),
                ),
            )
        if self.data.get("communityLinks"):
            chunks.append(
                Chunk(
                    "ACSOS community links",
                    "; ".join(f"{link['name']}: {link['url']}" for link in self.data["communityLinks"]),
                    self.data["website"],
                ),
            )
        return chunks


def stems(terms: set[str]) -> set[str]:
    """Reduce terms to a crude singular stem, so "swarms" and "swarm" compare equal."""
    reduced = set()
    for term in terms:
        if len(term) > 4 and term.endswith(("ies",)):
            reduced.add(f"{term[:-3]}y")
        elif len(term) > 4 and term.endswith(("es", "s")) and not term.endswith("ss"):
            reduced.add(term.rstrip("s").removesuffix("e"))
        else:
            reduced.add(term)
    return reduced


def session_key(session: dict[str, Any]) -> str:
    """Stable identity for one scheduled session."""
    return f"{session.get('date', '')}|{session.get('time', '')}|{session.get('title', '')}"


def session_schedule_line(session: dict[str, Any]) -> str:
    """Format one scheduled session for answers and retrieval chunks."""
    room = f" — {session['room']}" if session.get("room") else ""
    return f"**{session.get('time', '')}** {session.get('title', '')}{room}".strip()


def program_track_for_query(query_terms: set[str]) -> str | None:
    """Map explicit program vocabulary to the corresponding track id."""
    track_terms = (
        ("main", {"main", "plenary"}),
        ("workshops", {"workshop", "workshops"}),
        ("tutorials", {"tutorial", "tutorials"}),
        ("posters", {"demo", "demos", "poster", "posters"}),
        ("doctoral", {"doctoral", "phd"}),
        ("artifacts", {"artifact", "artifacts"}),
        ("inpractice", {"practice", "industry"}),
    )
    return next((track_id for track_id, terms in track_terms if query_terms & terms), None)


