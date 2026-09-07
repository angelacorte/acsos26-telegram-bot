#!/usr/bin/env python3
"""Refresh ACSOS 2026 conference data from the public website."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

BASE_URL = "https://2026.acsos.org/"
DEFAULT_DATA_PATH = Path("src/main/resources/acsos26/conference.json")
TRACKS = [
    {
        "id": "artifacts",
        "command": "artifacts",
        "name": "Artifacts",
        "url": "https://2026.acsos.org/track/acsos-2026-artifacts",
        "summary": "Artifact evaluation information for ACSOS 2026.",
    },
    {
        "id": "doctoral",
        "command": "doctoral",
        "name": "Doctoral Symposium",
        "url": "https://2026.acsos.org/track/acsos-2026-doctoral-symposium",
        "summary": "Doctoral Symposium information for ACSOS 2026.",
    },
    {
        "id": "main",
        "command": "maintrack",
        "name": "Main Track",
        "url": "https://2026.acsos.org/track/acsos-2026-papers",
        "summary": "Research, experience, short, and vision papers for ACSOS 2026.",
    },
    {
        "id": "posters",
        "command": "posters",
        "name": "Posters and Demos",
        "url": "https://2026.acsos.org/track/acsos-2026-posters-and-demos",
        "summary": "Posters and demos information for ACSOS 2026.",
    },
    {
        "id": "tutorials",
        "command": "tutorials",
        "name": "Tutorials",
        "url": "https://2026.acsos.org/track/acsos-2026-tutorials",
        "summary": "Tutorial information for ACSOS 2026.",
    },
    {
        "id": "workshops",
        "command": "workshops",
        "name": "Workshops",
        "url": "https://2026.acsos.org/track/acsos-2026-workshops",
        "summary": "Workshop information for ACSOS 2026.",
    },
    {
        "id": "inpractice",
        "command": "inpractice",
        "name": "ACSOS In Practice",
        "url": "https://2026.acsos.org/track/acsos-2026-acsos-in-practice",
        "summary": "Industry practitioners and applied researchers on autonomic systems in production.",
    },
    {
        "id": "social",
        "command": "socialprogram",
        "name": "Social Program",
        "url": "https://2026.acsos.org/track/acsos-2026-social-program",
        "summary": "Receptions, banquet, and excursions scheduled during ACSOS 2026.",
    },
]
INFO_PAGES = [
    {
        "id": "venue",
        "title": "Venue: University of Bologna, Cesena Campus",
        "url": "https://2026.acsos.org/venue/acsos-2026-venue",
    },
    {
        "id": "registration",
        "title": "Registration",
        "url": "https://2026.acsos.org/attending/Registration",
    },
    {
        "id": "mainSocialEvent",
        "title": "Main Social Event",
        "url": "https://2026.acsos.org/attending/main-social-event",
    },
    {
        "id": "travel",
        "title": "Travel Information",
        "url": "https://2026.acsos.org/attending/travel-information",
    },
    {
        "id": "accommodation",
        "title": "Accommodation",
        "url": "https://2026.acsos.org/attending/accommodation",
    },
    {
        "id": "visa",
        "title": "Visa Information",
        "url": "https://2026.acsos.org/attending/visa-information",
    },
    {
        "id": "codeOfConduct",
        "title": "Code of Conduct",
        "url": "https://2026.acsos.org/attending/code-of-conduct",
    },
    {
        "id": "visitCesena",
        "title": "Visit Cesena",
        "url": "https://2026.acsos.org/attending/visit-cesena",
    },
    {
        "id": "welcomeReception",
        "title": "Welcome Reception",
        "url": "https://2026.acsos.org/attending/welcome-reception",
    },
]
PROGRAM_URL = "https://2026.acsos.org/info/program-at-a-glance"
DETAILED_PROGRAM_URL = "https://2026.acsos.org/program/program-acsos-2026/Detailed-Table"
SOCIAL_URL = "https://2026.acsos.org/attending/social-events"
KEYNOTES_URL = "https://2026.acsos.org/info/keynotes"
ORGANIZING_COMMITTEE_URL = "https://2026.acsos.org/committee/acsos-2026-organizing-committee"
DATES_URL = "https://2026.acsos.org/dates"
NEWS_URL = "https://2026.acsos.org/news"
SEMINAR_SERIES_URL = "https://2026.acsos.org/info/seminar-series"
VENUE_URL = "https://2026.acsos.org/venue/acsos-2026-venue"
DATE_LINE_PATTERN = re.compile(r"^[A-Z][a-z]{2} \d{1,2} [A-Z][a-z]{2} \d{4}$")
WORKSHOP_TITLE_PATTERN = re.compile(r"^(?P<name>.+?)\s+\((?P<acronym>[A-Za-z0-9][A-Za-z0-9\-]*)\)$")
NAVIGATION_MARKERS = (
    "Sign in Sign up",
    "Main Track Workshops Tutorials",
    "Malatestiana Library Visit Sponsoring",
)
NON_PAPER_TITLES = {
    "Q&A and Panel Discussion",
    "Q&A",
    "Panel Discussion",
    "Discussion",
    "Closing",
    "Opening",
}
USER_AGENT = "acsos26-telegram-bot-data-refresh/1.0"
PAGE_LINKS: dict[str, list[tuple[str, str]]] = {}
SESSION_TABLE_PATTERN = re.compile(
    r'<table(?P<attrs>[^>]*class="[^"]*session-table[^"]*"[^>]*)>(?P<body>.*?)</table>',
    re.DOTALL,
)
TRACK_IDS_BY_LABEL = {
    "main track": "main",
    "workshops": "workshops",
    "doctoral symposium": "doctoral",
    "posters and demos": "posters",
    "tutorials": "tutorials",
    "artifacts": "artifacts",
    "in practice": "inpractice",
    "social program": "social",
    "catering": "catering",
}
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


class VisibleTextParser(HTMLParser):
    """Extract visible text and links from simple conference pages."""

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._current_href: str | None = None
        self.lines: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
        if tag == "a":
            self._current_href = dict(attrs).get("href")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "a":
            self._current_href = None

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = clean_text(data)
        if not text:
            return
        self.lines.append(text)
        if self._current_href:
            self.links.append((text, self._current_href))


class ProgramTableParser(HTMLParser):
    """Extract rows, cells, classes, and row spans from the program table."""

    def __init__(self) -> None:
        super().__init__()
        self.in_program_table = False
        self.current_row: list[dict[str, Any]] | None = None
        self.current_cell: dict[str, Any] | None = None
        self.current_part_kind: str | None = None
        self.rows: list[list[dict[str, Any]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "table" and "program" in classes:
            self.in_program_table = True
        elif self.in_program_table and tag == "tr":
            self.current_row = []
        elif self.in_program_table and tag in {"td", "th"} and self.current_row is not None:
            rowspan = attributes.get("rowspan") or "1"
            self.current_cell = {
                "parts": [],
                "detail_parts": [],
                "room_parts": [],
                "classes": sorted(classes),
                "rowspan": int(rowspan) if rowspan.isdigit() else 1,
            }
        elif self.current_cell is not None:
            if "room" in classes:
                self.current_part_kind = "room"
            elif "minor" in classes:
                self.current_part_kind = "detail"

    def handle_endtag(self, tag: str) -> None:
        if not self.in_program_table:
            return
        if tag in {"td", "th"} and self.current_cell is not None and self.current_row is not None:
            self.current_row.append(self.current_cell)
            self.current_cell = None
            self.current_part_kind = None
        elif tag == "span" and self.current_cell is not None:
            self.current_part_kind = None
        elif tag == "tr" and self.current_row is not None:
            if self.current_row:
                self.rows.append(self.current_row)
            self.current_row = None
        elif tag == "table":
            self.in_program_table = False

    def handle_data(self, data: str) -> None:
        if self.current_cell is None:
            return
        text = clean_text(data)
        parts = self.current_cell["parts"]
        if text and (not parts or text != parts[-1]):
            parts.append(text)
        if self.current_part_kind is not None:
            classified_parts = self.current_cell[f"{self.current_part_kind}_parts"]
            if text and (not classified_parts or text != classified_parts[-1]):
                classified_parts.append(text)


def main() -> int:
    """Refresh the conference data file."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA_PATH)
    args = parser.parse_args()

    data = read_json(args.data)
    conference = data["conference"]
    pages, raw_pages = fetch_pages(
        [
            BASE_URL,
            PROGRAM_URL,
            DETAILED_PROGRAM_URL,
            SOCIAL_URL,
            KEYNOTES_URL,
            ORGANIZING_COMMITTEE_URL,
            DATES_URL,
            NEWS_URL,
            SEMINAR_SERIES_URL,
            *[track["url"] for track in TRACKS],
            *[page["url"] for page in INFO_PAGES],
        ],
    )
    if not any(pages.values()):
        raise RuntimeError("No conference pages could be fetched; refusing to rewrite conference data.")
    home_lines = pages[BASE_URL]

    conference["name"] = first_line_matching(home_lines, r"IEEE International Conference") or conference["name"]
    conference["dates"] = first_line_matching(home_lines, r"Mon 7 - Fri 11 September 2026") or conference["dates"]
    conference["location"] = first_line_matching(home_lines, r"Cesena, Italy") or conference["location"]
    conference["description"] = extract_description(home_lines) or conference["description"]
    program = extract_program(raw_pages.get(PROGRAM_URL, ""), pages.get(PROGRAM_URL, []))
    if program.get("days"):
        conference["program"] = program
    detailed_html = raw_pages.get(DETAILED_PROGRAM_URL, "")
    if detailed_html:
        extracted_sessions = extract_detailed_sessions(detailed_html)
        if extracted_sessions:
            conference["sessions"] = extracted_sessions
    conference["tracks"] = refresh_tracks(conference["tracks"], pages, conference.get("sessions", []))
    conference["infoPages"] = refresh_info_pages(conference["infoPages"], pages)
    social_lines = pages.get(SOCIAL_URL, [])
    conference["socialEvents"] = extract_social_events(social_lines) if social_lines else conference["socialEvents"]
    keynote_lines = pages.get(KEYNOTES_URL, [])
    conference["keynotes"] = extract_keynotes(keynote_lines) if keynote_lines else conference.get("keynotes", [])
    committee_lines = pages.get(ORGANIZING_COMMITTEE_URL, [])
    conference["committees"] = (
        extract_organizing_committee(committee_lines)
        if committee_lines
        else conference.get("committees", [])
    )
    workshop_lines = pages.get(next(track["url"] for track in TRACKS if track["id"] == "workshops"), [])
    replace_if_found(conference, "workshops", extract_workshops(workshop_lines) if workshop_lines else [])
    replace_if_found(conference, "importantDates", extract_important_dates(pages.get(DATES_URL, [])))
    replace_if_found(conference, "news", extract_news(pages.get(NEWS_URL, [])))
    replace_if_found(conference, "seminarSeries", extract_seminar_series(pages.get(SEMINAR_SERIES_URL, [])))
    replace_if_found(conference, "communityLinks", extract_community_links(raw_pages.get(BASE_URL, "")))
    venue_lines = pages.get(VENUE_URL, [])
    if venue_lines:
        replace_if_found(conference, "venue", extract_venue(venue_lines, conference.get("sessions", [])))
    conference["programStatus"] = program_status(conference)

    write_json(args.data, data)
    return 0


def fetch_pages(urls: list[str]) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Fetch pages and return visible lines plus raw HTML keyed by URL."""
    pages: dict[str, list[str]] = {}
    raw_pages: dict[str, str] = {}
    for url in urls:
        try:
            html = fetch(url)
        except OSError as error:
            print(f"warning: could not fetch {url}: {error}", file=sys.stderr)
            pages[url] = []
            raw_pages[url] = ""
            PAGE_LINKS[url] = []
            continue
        parser = VisibleTextParser()
        parser.feed(html)
        pages[url] = collapse_lines(parser.lines)
        raw_pages[url] = html
        PAGE_LINKS[url] = parser.links
    return pages, raw_pages


def replace_if_found(conference: dict[str, Any], key: str, value: Any) -> None:
    """Store freshly scraped data, keeping the previous value when nothing was found."""
    if value:
        conference[key] = value
    else:
        conference.setdefault(key, [] if isinstance(value, list) else {})


def fetch(url: str) -> str:
    """Fetch a URL as UTF-8 text."""
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def extract_program(html: str, lines: list[str]) -> dict[str, Any]:
    """Parse the five-day program-at-a-glance table into searchable day entries."""
    parser = ProgramTableParser()
    parser.feed(html)
    header_index = next(
        (
            index
            for index, row in enumerate(parser.rows)
            if len(row) > 1 and any(cell_text(cell).casefold() == "monday 7 september" for cell in row)
        ),
        None,
    )
    if header_index is None:
        return {}

    day_cells = parser.rows[header_index][1:]
    days = [program_day(cell) for cell in day_cells]
    time_rows = [
        row
        for row in parser.rows[header_index + 1 :]
        if row and "time" in row[0]["classes"]
    ]
    occupied_until = [0] * len(days)
    for row_index, row in enumerate(time_rows):
        start_label = cell_text(row[0])
        for cell in row[1:]:
            day_index = next(
                (index for index, occupied in enumerate(occupied_until) if occupied <= row_index),
                None,
            )
            if day_index is None:
                continue
            rowspan = max(1, cell["rowspan"])
            end_row_index = min(row_index + rowspan - 1, len(time_rows) - 1)
            end_label = cell_text(time_rows[end_row_index][0])
            parts = cell["parts"]
            days[day_index]["entries"].append(
                {
                    "time": program_time_range(start_label, end_label),
                    "title": parts[0] if parts else "",
                    "details": " · ".join(cell["detail_parts"]),
                    "room": " · ".join(cell["room_parts"]),
                    "category": program_category(cell["classes"]),
                },
            )
            occupied_until[day_index] = row_index + rowspan

    status = "Five-day overview of the ACSOS 2026 program."
    notes = [
        line
        for line in lines
        if line.startswith("NOTE: Additional social events")
    ]
    return {
        "title": "Program at a Glance",
        "url": PROGRAM_URL,
        "status": status,
        "notes": notes,
        "days": days,
    }


def extract_detailed_sessions(html_text: str) -> list[dict[str, Any]]:
    """Extract timed sessions and their talks from the detailed program table."""
    sessions: list[dict[str, Any]] = []
    for match in SESSION_TABLE_PATTERN.finditer(html_text):
        attrs, body = match.group("attrs"), match.group("body")
        title = session_title(body)
        if not title:
            continue
        iso_date = session_iso_date(attribute(attrs, "data-facet-date-order"))
        talks = session_talks(body)
        sessions.append(
            {
                "title": title,
                "trackId": session_track_id(attrs, body),
                "day": session_day_label(iso_date, attribute(attrs, "data-facet-date")),
                "date": iso_date,
                "time": session_time_range(body),
                "room": unescape_text(attribute(attrs, "data-facet-room")),
                "papers": [talk["title"] for talk in talks],
                "talks": talks,
            },
        )
    return sessions


def attribute(attrs: str, name: str) -> str:
    """Read one attribute value out of a raw HTML start tag."""
    match = re.search(rf'{re.escape(name)}="([^"]*)"', attrs)
    return match.group(1) if match else ""


def unescape_text(value: str) -> str:
    """Decode HTML entities and collapse whitespace."""
    return clean_text(unescape(value))


def strip_tags(fragment: str) -> str:
    """Drop markup and decode entities from an HTML fragment."""
    return clean_text(unescape(re.sub(r"<[^>]+>", " ", fragment)))


def session_title(body: str) -> str:
    """Read the session name, excluding the track label, the room link, and any abstract."""
    match = re.search(r'<div class="session-info-in-table">(.*?)</div>', body, re.DOTALL)
    if not match:
        return ""
    fragment = re.split(r'<span class="pull-right">', match.group(1))[0]
    fragment = re.sub(r"\s+at\s+<a[^>]*room-link.*$", "", fragment, flags=re.DOTALL)
    return strip_tags(fragment)


def session_time_range(body: str) -> str:
    """Read a session slot label such as 09:30-11:00."""
    match = re.search(r'<div class="slot-label">(.*?)</div>', body, re.DOTALL)
    if not match:
        return ""
    return re.sub(r"\s*[-–—]\s*", "-", strip_tags(match.group(1)))


def session_track_id(attrs: str, body: str) -> str:
    """Resolve a track id from the table facet, an inner facet, or the track link."""
    labels = [
        attribute(attrs, "data-facet-track"),
        *re.findall(r'data-facet-track="([^"]+)"', body),
        *re.findall(r'<span class="pull-right"><a[^>]*>(.*?)</a>', body, re.DOTALL),
    ]
    for label in labels:
        key = clean_text(strip_tags(label).casefold().replace("acsos", ""))
        if key in TRACK_IDS_BY_LABEL:
            return TRACK_IDS_BY_LABEL[key]
    return "main"


def session_iso_date(date_order: str) -> str:
    """Convert a researchr date facet such as 260907 into 2026-09-07."""
    if not re.fullmatch(r"\d{6}", date_order):
        return ""
    return f"20{date_order[:2]}-{date_order[2:4]}-{date_order[4:]}"


def session_day_label(iso_date: str, date_facet: str) -> str:
    """Build a 'Monday, 7 September' label, falling back to the raw date facet."""
    try:
        day = date.fromisoformat(iso_date)
    except ValueError:
        return unescape_text(date_facet)
    return f"{WEEKDAY_NAMES[day.weekday()]}, {day.day} {MONTH_NAMES[day.month - 1]}"


def session_talks(body: str) -> list[dict[str, Any]]:
    """Extract the individual talks scheduled inside one session."""
    talks: list[dict[str, Any]] = []
    for slot in re.findall(r'<tr[^>]*data-slot-id="[^"]*"[^>]*>(.*?)</tr>', body, re.DOTALL):
        title_match = re.search(r'data-event-modal="[^"]*">(.*?)</a>', slot, re.DOTALL)
        if not title_match:
            continue
        performers = re.search(r'<div class="performers">(.*?)</div>', slot, re.DOTALL)
        speakers = (
            [strip_tags(anchor) for anchor in re.findall(r"<a[^>]*>(.*?)</a>", performers.group(1), re.DOTALL)]
            if performers
            else []
        )
        start = re.search(r'<div class="start-time">(.*?)</div>', slot, re.DOTALL)
        kind = re.search(r'<div class="event-type">(.*?)</div>', slot, re.DOTALL)
        duration = re.search(r"<strong>(\d+m)</strong>", slot)
        talks.append(
            {
                "title": strip_tags(title_match.group(1)),
                "time": strip_tags(start.group(1)) if start else "",
                "duration": duration.group(1) if duration else "",
                "kind": strip_tags(kind.group(1)) if kind else "",
                "speakers": deduplicate([speaker for speaker in speakers if speaker]),
            },
        )
    return talks


def cell_text(cell: dict[str, Any]) -> str:
    """Join the visible text fragments of a parsed table cell."""
    return " ".join(cell["parts"]).strip()


def program_day(cell: dict[str, Any]) -> dict[str, Any]:
    """Build one program day from a header cell such as Tuesday / 8 September."""
    parts = cell["parts"]
    return {
        "day": parts[0] if parts else "",
        "date": " ".join(parts[1:]),
        "entries": [],
    }


def program_time_range(start_label: str, end_label: str) -> str:
    """Combine the first and final half-hour labels covered by a row-spanned event."""
    if start_label.casefold() == "evening":
        return "Evening"
    start = re.split(r"[\-–—]", start_label, maxsplit=1)[0].strip()
    end = re.split(r"[\-–—]", end_label)[-1].strip()
    return f"{start}–{end}"


def program_category(classes: list[str]) -> str:
    """Return the semantic category encoded in a program table cell's CSS classes."""
    categories = ("main", "keynote", "workshop", "tutorial", "phd", "poster", "panel", "special", "break", "social")
    return next((category for category in categories if category in classes), "other")


def refresh_tracks(
    existing_tracks: list[dict[str, Any]],
    pages: dict[str, list[str]],
    sessions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Refresh track statuses and accepted contributions while preserving known commands."""
    existing_by_id = {track["id"]: track for track in existing_tracks}
    refreshed = []
    for definition in TRACKS:
        old = existing_by_id.get(definition["id"], {})
        lines = pages.get(definition["url"], [])
        accepted_papers = extract_accepted_papers(lines, definition["name"]) if lines else []
        if not accepted_papers and old.get("acceptedPapers"):
            accepted_papers = old["acceptedPapers"]
        track_sessions = [session for session in sessions if session.get("trackId") == definition["id"]]
        refreshed.append(
            {
                **old,
                **definition,
                "status": track_status(definition["name"], accepted_papers, track_sessions),
                "acceptedPapers": accepted_papers,
            },
        )
    return refreshed


def extract_accepted_papers(lines: list[str], track_name: str) -> list[dict[str, Any]]:
    """Extract accepted paper titles and authors from a track page."""
    section = section_between(
        lines,
        start_patterns=[
            r"^Accepted Papers$",
            r"^Accepted Contributions$",
            r"^Accepted Workshop Papers$",
            r"^Accepted Tutorials$",
            r"^Accepted Artifacts$",
            r"^Accepted Posters/Demos$",
        ],
        end_patterns=[
            r"^Camera Ready",
            r"^Call for",
            r"^Important Dates",
            r"^Submission",
            r"^Program Chairs$",
            r"^Track Chairs$",
            r"^Accepted Workshops$",
        ],
    )
    if not section:
        return []

    papers: list[dict[str, Any]] = []
    pending_title: str | None = None
    pending_authors: list[str] = []
    index = 0
    while index < len(section):
        line = section[index]
        if line in {"Title", track_name, "Authors", ","}:
            index += 1
            continue
        if index + 1 < len(section) and section[index + 1] == track_name:
            if pending_title:
                papers.append({"title": pending_title, "authors": deduplicate(pending_authors)})
            pending_title = line
            pending_authors = []
            index += 2
            continue
        elif pending_title and looks_like_person(line):
            pending_authors.append(line)
        index += 1
    if pending_title:
        papers.append({"title": pending_title, "authors": deduplicate(pending_authors)})
    papers = [paper for paper in papers if paper["title"] not in NON_PAPER_TITLES]
    return deduplicate_papers(papers)


def extract_important_dates(lines: list[str]) -> list[dict[str, str]]:
    """Extract the deadline table as (date, track, what) rows."""
    start = content_heading_index(lines, [r"^Important Dates$"])
    if start is None:
        return []
    section = lines[start + 1 : content_end_index(lines, start + 1)]
    header = next_index(section, "What", 0)
    rows: list[dict[str, str]] = []
    index = header + 1 if header < len(section) else 0
    while index + 2 < len(section) + 1:
        chunk = section[index : index + 3]
        if len(chunk) < 3 or not DATE_LINE_PATTERN.match(chunk[0]):
            break
        rows.append({"date": chunk[0], "track": chunk[1], "what": chunk[2]})
        index += 3
    return rows


def extract_venue(lines: list[str], sessions: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract the venue name, postal address, and the rooms actually in use."""
    start = content_heading_index(lines, [r"^Venue: ", r"^Location$"])
    address: list[str] = []
    if start is not None:
        location = next_index(lines, "Location", start)
        if location < len(lines):
            end = min(next_index(lines, "Rooms", location), content_end_index(lines, location))
            address = lines[location + 1 : end]
    rooms = sorted({session["room"] for session in sessions if session.get("room")})
    return {
        "name": "University of Bologna, Cesena Campus",
        "address": ", ".join(address),
        "mainRoom": 'Aula Magna "Carmen Tura" (Room 3.4), first floor',
        "rooms": rooms,
        "url": VENUE_URL,
    }


def extract_workshops(lines: list[str]) -> list[dict[str, Any]]:
    """Extract the accepted workshops with their organizers and websites."""
    start = content_heading_index(lines, [r"^Accepted Workshops$"])
    if start is None:
        return []
    section = lines[start + 1 : content_end_index(lines, start + 1)]
    repeated = {line for line in section if section.count(line) > 1}
    block_starts = [
        index
        for index, line in enumerate(section)
        if line in repeated and WORKSHOP_TITLE_PATTERN.match(line)
    ]
    latest: dict[str, int] = {}
    for index in block_starts:
        latest[section[index]] = index
    ordered = sorted(latest.items(), key=lambda item: item[1])
    workshops = []
    for position, (line, block_start) in enumerate(ordered):
        block_end = ordered[position + 1][1] if position + 1 < len(ordered) else len(section)
        block = section[block_start + 1 : block_end]
        organizers_at = next_index(block, "Workshop organizers:", 0)
        if organizers_at >= len(block):
            continue
        website_at = next(
            (index for index, item in enumerate(block) if item.startswith("More information")),
            len(block),
        )
        site = block[website_at + 1] if website_at + 1 < len(block) else ""
        match = WORKSHOP_TITLE_PATTERN.match(line)
        workshops.append(
            {
                "name": match.group("name").strip(),
                "acronym": match.group("acronym"),
                "summary": clean_text(" ".join(block[:organizers_at]))[:700],
                "organizers": deduplicate(block[organizers_at + 1 : website_at]),
                "site": site if site.startswith("http") else "",
                "url": next(track["url"] for track in TRACKS if track["id"] == "workshops"),
            },
        )
    return workshops


def extract_news(lines: list[str]) -> list[dict[str, str]]:
    """Extract news items as title, date, and summary."""
    start = content_heading_index(lines, [r"^News Items$"])
    if start is None:
        return []
    section = lines[start + 1 : content_end_index(lines, start + 1)]
    news = []
    for index, line in enumerate(section):
        if index + 1 >= len(section):
            break
        date_match = re.fullmatch(r"\((?P<date>[A-Z][a-z]{2} \d{1,2} [A-Z][a-z]{2} \d{4})\)", section[index + 1])
        if not date_match:
            continue
        body = []
        for follower in section[index + 2 :]:
            if follower.startswith("Submitted ") or re.fullmatch(r"\(.*\)", follower):
                break
            body.append(follower)
        news.append(
            {
                "title": line,
                "date": date_match.group("date"),
                "summary": clean_text(" ".join(body))[:500],
                "url": NEWS_URL,
            },
        )
    return news


def extract_seminar_series(lines: list[str]) -> list[dict[str, str]]:
    """Extract the seminar series talks listed in the summary table."""
    start = content_heading_index(lines, [r"^Seminar series$"])
    if start is None:
        return []
    section = lines[start + 1 : content_end_index(lines, start + 1)]
    header = next_index(section, "Date", 0)
    seminars = []
    index = header + 1 if header < len(section) else 0
    while index + 2 < len(section) + 1:
        chunk = section[index : index + 3]
        if len(chunk) < 3 or not re.search(r"\(.+\)$", chunk[0]):
            break
        speaker, _, affiliation = chunk[0].rpartition("(")
        seminars.append(
            {
                "speaker": speaker.strip(),
                "affiliation": affiliation.rstrip(")").strip(),
                "title": chunk[1],
                "whenText": chunk[2],
                "url": SEMINAR_SERIES_URL,
            },
        )
        index += 3
    return seminars


def extract_community_links(html_text: str) -> list[dict[str, str]]:
    """Pick the official ACSOS community and social media links from the home page."""
    wanted = [
        ("linktr.ee", "Linktree"),
        ("github.com/acsos", "GitHub"),
        ("twitter.com/acsosconf", "X (Twitter)"),
        ("linkedin.com/company/acsos", "LinkedIn"),
        ("instagram.com/acsos", "Instagram"),
        ("youtube.com/@acsosconf", "YouTube"),
        ("facebook.com/profile", "Facebook"),
    ]
    hrefs = re.findall(r'href="(https?://[^"]+)"', html_text)
    found: dict[str, str] = {}
    for needle, name in wanted:
        for href in hrefs:
            if needle in href.casefold() and name not in found:
                found[name] = href
                break
    return [{"name": name, "url": url} for name, url in found.items()]


def refresh_info_pages(existing_pages: list[dict[str, Any]], pages: dict[str, list[str]]) -> list[dict[str, str]]:
    """Refresh concise conference info pages."""
    existing_by_id = {page["id"]: page for page in existing_pages}
    refreshed = []
    for definition in INFO_PAGES:
        lines = pages.get(definition["url"], [])
        body = extract_page_body(lines, definition["title"])
        old = existing_by_id.get(definition["id"], {})
        refreshed.append({**old, **definition, "body": body or old.get("body", "")})
    return refreshed


def extract_page_body(lines: list[str], title: str) -> str:
    """Extract a compact body from a generic conference page."""
    bare_title = title.replace("Venue: ", "")
    # researchr renders some headings as "ACSOS 2026 <title>", so accept that prefix too.
    title_patterns = [
        rf"^{re.escape(title)}$",
        rf"^{re.escape(bare_title)}$",
        rf"^ACSOS 2026 {re.escape(bare_title)}$",
    ]
    start = content_heading_index(lines, title_patterns)
    if start is None:
        return ""
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if any(re.search(pattern, lines[index], flags=re.IGNORECASE) for pattern in [r"^x$", r"^using$"]):
            end = index
            break
    section = lines[start + 1 : end]
    content = [line for line in section if not line.startswith("Image:") and not line.startswith("Photo ")]
    body = " ".join(content[:20]).strip()
    # A heading matched inside the navigation or footer yields the menu, not the page: reject it.
    return "" if any(marker in body for marker in NAVIGATION_MARKERS) else body


def content_heading_index(lines: list[str], title_patterns: list[str]) -> int | None:
    """Find a heading in the page content, skipping top navigation and footer repeats."""
    candidates = [
        index
        for index, line in enumerate(lines)
        if any(re.search(pattern, line, flags=re.IGNORECASE) for pattern in title_patterns)
    ]
    content_candidates = [index for index in candidates if index > 50]
    return content_candidates[0] if content_candidates else (candidates[-1] if candidates else None)


def extract_social_events(lines: list[str]) -> list[dict[str, str]]:
    """Extract social events when the page has concrete entries."""
    start = content_heading_index(lines, [r"^Additional Social Events$", r"^Social Events$"])
    if start is None:
        return []
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index] == "x" or lines[index] == "using":
            end = index
            break
    section = lines[start + 1 : end]
    title_indexes = [
        index
        for index, line in enumerate(section)
        if index + 1 < len(section) and section[index + 1] == "Date:"
    ]
    events = []
    for position, title_index in enumerate(title_indexes):
        next_title_index = title_indexes[position + 1] if position + 1 < len(title_indexes) else len(section)
        event_lines = section[title_index:next_title_index]
        events.append(parse_social_event(event_lines))
    return events


def parse_social_event(lines: list[str]) -> dict[str, str]:
    """Parse one social event detail section."""
    title = lines[0]
    details = {
        "Date:": "",
        "Location:": "",
        "Participation fee:": "",
        "Capacity:": "",
        "Includes:": "",
        "Restaurant:": "",
    }
    consumed_indexes = {0}
    for index, line in enumerate(lines[:-1]):
        if line in details:
            details[line] = lines[index + 1]
            consumed_indexes.update({index, index + 1})
    body_lines = [
        line
        for index, line in enumerate(lines)
        if index not in consumed_indexes
        and not line.startswith("http")
        and line not in {"General Information", "Transportation", "Cancellation and Refunds", "Dietary Requirements"}
    ]
    facts = [
        f"Fee: {details['Participation fee:']}" if details["Participation fee:"] else "",
        f"Capacity: {details['Capacity:']}" if details["Capacity:"] else "",
        f"Includes: {details['Includes:']}" if details["Includes:"] else "",
        f"Restaurant: {details['Restaurant:']}" if details["Restaurant:"] else "",
    ]
    body = " ".join([fact for fact in facts if fact] + body_lines[:8])
    return {
        "title": title,
        "whenText": details["Date:"],
        "whereText": details["Location:"],
        "fee": details["Participation fee:"],
        "capacity": details["Capacity:"],
        "includes": details["Includes:"],
        "restaurant": details["Restaurant:"],
        "body": body.strip(),
    }


def extract_keynotes(lines: list[str]) -> list[dict[str, str]]:
    """Extract keynote speakers and talk metadata."""
    start = content_heading_index(lines, [r"^Keynotes$"])
    if start is None:
        return []
    end = content_end_index(lines, start + 1)
    section = lines[start + 1 : end]
    keynotes = []
    index = 0
    current_kind = "Main keynote"
    while index < len(section):
        line = section[index]
        if line == "Doctoral Symposium keynote:":
            current_kind = "Doctoral Symposium keynote"
            index += 1
            continue
        if index + 1 < len(section) and section[index + 1] == "Abstract:":
            speaker, affiliation, title = parse_keynote_heading(line)
            abstract_start = index + 2
            biosketch_index = next_index(section, "Biosketch:", abstract_start)
            abstract = " ".join(section[abstract_start:biosketch_index]).strip()
            biosketch_end = next_biosketch_end(section, biosketch_index + 1)
            keynotes.append(
                {
                    "speaker": speaker,
                    "affiliation": affiliation,
                    "title": title,
                    "kind": current_kind,
                    "abstract": abstract,
                    "biosketch": clean_text(" ".join(section[biosketch_index + 1 : biosketch_end]))[:1200],
                    "url": KEYNOTES_URL,
                },
            )
            index = biosketch_index + 1
            continue
        index += 1
    return keynotes


def next_biosketch_end(section: list[str], start: int) -> int:
    """Find where a keynote biosketch stops: at the next keynote heading or section end."""
    for index in range(start, len(section)):
        if index + 1 < len(section) and section[index + 1] == "Abstract:":
            return index
        if section[index] == "Doctoral Symposium keynote:":
            return index
    return len(section)


def parse_keynote_heading(line: str) -> tuple[str, str, str]:
    """Parse 'Speaker (Affiliation): Title' keynote headings."""
    match = re.match(r"^(?P<speaker>.+?)\s+\((?P<affiliation>.+?)\)(?::\s*(?P<title>.+))?$", line)
    if not match:
        return line, "", ""
    return (
        match.group("speaker").strip(),
        match.group("affiliation").strip(),
        (match.group("title") or "").strip(),
    )


def extract_organizing_committee(lines: list[str]) -> list[dict[str, str]]:
    """Extract organizing committee people and roles."""
    start = content_heading_index(lines, [r"^Organizing Committee$"])
    if start is None:
        return []
    end = content_end_index(lines, start + 1)
    section = lines[start + 2 : end] if start + 1 < end and lines[start + 1] == "ACSOS 2026" else lines[start + 1 : end]
    role_indexes = [index for index, line in enumerate(section) if is_committee_role(line)]
    people = []
    for position, role_index in enumerate(role_indexes):
        name_start = committee_name_start(section, role_index)
        next_name_start = (
            committee_name_start(section, role_indexes[position + 1])
            if position + 1 < len(role_indexes)
            else len(section)
        )
        name = clean_person_name(" ".join(section[name_start:role_index]))
        role = section[role_index]
        affiliation = " ".join(section[role_index + 1 : next_name_start]).strip()
        people.append(
            {
                "name": name,
                "role": role,
                "affiliation": affiliation,
                "url": ORGANIZING_COMMITTEE_URL,
            },
        )
    return people


def committee_name_start(
    lines: list[str],
    role_index: int,
) -> int:
    """Find where a committee member name starts before its role."""
    start = role_index - 1
    if start - 1 >= 0 and is_name_continuation(lines[start - 1]):
        start -= 1
    return start


def is_committee_role(line: str) -> bool:
    """Return true for committee role labels."""
    return bool(re.search(r"\b(Co-)?Chair\b", line))


def is_name_continuation(line: str) -> bool:
    """Return true when a previous line is likely part of a split person name."""
    if "," in line:
        return False
    countries = {
        "Canada",
        "Colombia",
        "Denmark",
        "France",
        "Germany",
        "Ireland",
        "Italy",
        "Japan",
        "Netherlands",
        "Sweden",
        "United Kingdom",
        "United States",
    }
    if line in countries:
        return False
    organization_terms = {"University", "College", "Institute", "Research", "Corporation", "Faculty"}
    if any(term in line for term in organization_terms):
        return False
    return len(line.split()) <= 2


def clean_person_name(name: str) -> str:
    """Clean duplicated split names from conf.researchr pages."""
    parts = name.split()
    if len(parts) >= 2 and parts[-1] == parts[-2]:
        parts.pop()
    return " ".join(parts)


def content_end_index(
    lines: list[str],
    start: int,
) -> int:
    """Find the end of the main page content."""
    for index in range(start, len(lines)):
        if lines[index] == "x" or lines[index] == "using":
            return index
    return len(lines)


def next_index(
    lines: list[str],
    value: str,
    start: int,
) -> int:
    """Find a value index, or return the end of the list."""
    for index in range(start, len(lines)):
        if lines[index] == value:
            return index
    return len(lines)


def extract_description(lines: list[str]) -> str | None:
    """Extract the conference description paragraph from the home page."""
    candidates = [
        line
        for line in lines
        if "leading forum" in line
        or "autonomic computing" in line and "self-organization" in line
    ]
    return candidates[0] if candidates else None


def program_status(conference: dict[str, Any]) -> str:
    """Build a status line from the currently refreshed data."""
    papers = sum(len(track["acceptedPapers"]) for track in conference["tracks"])
    sessions = conference.get("sessions", [])
    talks = sum(len(session.get("papers", [])) for session in sessions)
    if sessions:
        return (
            f"The ACSOS 2026 program is published: {count_label(len(sessions), 'scheduled session')} "
            f"with rooms, {count_label(talks, 'scheduled talk')}, and "
            f"{count_label(papers, 'accepted contribution')}."
        )
    if papers:
        return f"The conference data includes {count_label(papers, 'accepted contribution')}."
    return "The conference data includes the dates, tracks, and venue of ACSOS 2026."


def count_label(count: int, noun: str) -> str:
    """Format a count with a correctly pluralized noun."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def track_status(
    track_name: str,
    accepted_papers: list[dict[str, Any]],
    track_sessions: list[dict[str, Any]] | None = None,
    /,
) -> str:
    """Describe what is published for one track, based on the real schedule."""
    track_sessions = track_sessions or []
    talks = sum(len(session.get("papers", [])) for session in track_sessions)
    facts = []
    if accepted_papers:
        facts.append(f"{count_label(len(accepted_papers), 'accepted contribution')}")
    if track_sessions:
        rooms = sorted({session["room"] for session in track_sessions if session.get("room")})
        scheduled = count_label(len(track_sessions), "scheduled session")
        if rooms:
            scheduled += f" in {', '.join(rooms)}"
        facts.append(scheduled)
    if talks:
        facts.append(count_label(talks, "scheduled talk"))
    if not facts:
        return f"{track_name} details are published on the track page."
    return f"{track_name}: {'; '.join(facts)}."


def section_between(
    lines: list[str],
    start_patterns: list[str],
    end_patterns: list[str],
) -> list[str]:
    """Return lines between the first start pattern and the next end pattern."""
    start = None
    for index, line in enumerate(lines):
        if any(re.search(pattern, line, flags=re.IGNORECASE) for pattern in start_patterns):
            start = index + 1
    if start is None:
        return []
    end = len(lines)
    for index in range(start, len(lines)):
        if any(re.search(pattern, lines[index], flags=re.IGNORECASE) for pattern in end_patterns):
            end = index
            break
    return lines[start:end]


def looks_like_title(line: str) -> bool:
    """Heuristically detect paper titles in researchr track pages."""
    if looks_like_person(line):
        return False
    title_markers = [":", "-", "LLM", "Self", "Adaptive", "Autonomic", "Framework", "Systems"]
    return len(line.split()) >= 4 and any(marker in line for marker in title_markers)


def looks_like_person(line: str) -> bool:
    """Heuristically detect author names."""
    if len(line.split()) not in {2, 3, 4}:
        return False
    if any(char.isdigit() for char in line):
        return False
    return all(part[:1].isupper() or part[:1] in {"Á", "É", "Í", "Ó", "Ú"} for part in line.split())


def first_line_matching(lines: list[str], pattern: str) -> str | None:
    """Return the first line matching a regex."""
    for line in lines:
        if re.search(pattern, line):
            return line
    return None


def clean_text(text: str) -> str:
    """Normalize whitespace in scraped text."""
    return re.sub(r"\s+", " ", text).strip()


def collapse_lines(lines: list[str]) -> list[str]:
    """Drop duplicate adjacent lines and URL fragments."""
    collapsed = []
    for line in lines:
        if not line or (collapsed and line == collapsed[-1]):
            continue
        collapsed.append(line)
    return collapsed


def deduplicate(items: list[str]) -> list[str]:
    """Preserve order while removing duplicates."""
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def deduplicate_papers(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Preserve order while removing duplicate paper titles."""
    seen = set()
    result = []
    for paper in papers:
        if paper["title"] not in seen:
            seen.add(paper["title"])
            result.append(paper)
    return result


def read_json(path: Path) -> dict[str, Any]:
    """Read a JSON file."""
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Write stable JSON formatting for reviewable diffs."""
    path.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
