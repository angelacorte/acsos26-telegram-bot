#!/usr/bin/env python3
"""Refresh ACSOS 2026 conference data from the public website."""

from __future__ import annotations

import argparse
import json
import re
import sys
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
]
SOCIAL_URL = "https://2026.acsos.org/attending/social-events"
KEYNOTES_URL = "https://2026.acsos.org/info/keynotes"
ORGANIZING_COMMITTEE_URL = "https://2026.acsos.org/committee/acsos-2026-organizing-committee"
USER_AGENT = "acsos26-telegram-bot-data-refresh/1.0"


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


def main() -> int:
    """Refresh the conference data file."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA_PATH)
    args = parser.parse_args()

    data = read_json(args.data)
    conference = data["conference"]
    pages = fetch_pages(
        [
            BASE_URL,
            SOCIAL_URL,
            KEYNOTES_URL,
            ORGANIZING_COMMITTEE_URL,
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
    conference["tracks"] = refresh_tracks(conference["tracks"], pages)
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
    conference["programStatus"] = program_status(conference)

    write_json(args.data, data)
    return 0


def fetch_pages(urls: list[str]) -> dict[str, list[str]]:
    """Fetch pages and return visible text lines keyed by URL."""
    pages = {}
    for url in urls:
        try:
            html = fetch(url)
        except OSError as error:
            print(f"warning: could not fetch {url}: {error}", file=sys.stderr)
            pages[url] = []
            continue
        parser = VisibleTextParser()
        parser.feed(html)
        pages[url] = collapse_lines(parser.lines)
    return pages


def fetch(url: str) -> str:
    """Fetch a URL as UTF-8 text."""
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def refresh_tracks(existing_tracks: list[dict[str, Any]], pages: dict[str, list[str]]) -> list[dict[str, Any]]:
    """Refresh track statuses and accepted papers while preserving known commands."""
    existing_by_id = {track["id"]: track for track in existing_tracks}
    refreshed = []
    for definition in TRACKS:
        old = existing_by_id.get(definition["id"], {})
        lines = pages.get(definition["url"], [])
        accepted_papers = extract_accepted_papers(lines, definition["name"]) if lines else []
        if not accepted_papers and old.get("acceptedPapers"):
            accepted_papers = old["acceptedPapers"]
        status = track_status(definition["name"], accepted_papers) if lines else old.get("status", "")
        refreshed.append(
            {
                **old,
                **definition,
                "status": status,
                "acceptedPapers": accepted_papers,
            },
        )
    return refreshed


def extract_accepted_papers(lines: list[str], track_name: str) -> list[dict[str, Any]]:
    """Extract accepted paper titles and authors from a track page."""
    section = section_between(
        lines,
        start_patterns=[r"^Accepted Papers$", r"^Accepted Contributions$"],
        end_patterns=[
            r"^Camera Ready",
            r"^Call for",
            r"^Important Dates",
            r"^Submission",
            r"^Program Chairs$",
            r"^Track Chairs$",
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
    return deduplicate_papers(papers)


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
    title_patterns = [rf"^{re.escape(title)}$", rf"^{re.escape(title.replace('Venue: ', ''))}$"]
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
    return " ".join(content[:14]).strip()


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
            keynotes.append(
                {
                    "speaker": speaker,
                    "affiliation": affiliation,
                    "title": title,
                    "kind": current_kind,
                    "abstract": abstract,
                    "url": KEYNOTES_URL,
                },
            )
            index = biosketch_index + 1
            continue
        index += 1
    return keynotes


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
    sessions = len(conference["sessions"])
    if sessions:
        return f"The conference data includes {papers} accepted papers and {sessions} timed sessions."
    if papers:
        return (
            f"The conference data includes {papers} accepted papers. Timed sessions, rooms, "
            "and paper-to-session assignments are not available in this data file yet."
        )
    return "The conference data includes dates, tracks, and venue information. Timed sessions are not available yet."


def track_status(track_name: str, accepted_papers: list[dict[str, Any]]) -> str:
    """Build a track-specific status line."""
    if accepted_papers:
        return (
            f"{len(accepted_papers)} accepted papers are published for {track_name}. "
            "Timed sessions and rooms are not published in this data file yet."
        )
    return "Track page is available. Program timing is not published in this data file yet."


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
