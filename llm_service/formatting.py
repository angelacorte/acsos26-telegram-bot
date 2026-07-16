"""Presentation and extraction helpers over raw conference-data dictionaries.

These functions turn the JSON records (social events, keynotes, tracks) into the
concise, Telegram-friendly strings the deterministic answers return, and provide
the searchable-text projection used when matching those records.
"""

from __future__ import annotations

from typing import Any

from llm_service.text import normalize, split_sentences


def social_event_search_text(event: dict[str, str]) -> str:
    """Build the full searchable text for a social event, including body and details.

    Distinctive words like "kart", "karting", or "race" only appear in the body / fee /
    includes fields, so matching must look beyond the title, date, and location.
    """
    fields = ("title", "whenText", "whereText", "body", "fee", "includes", "capacity")
    return " ".join(str(event.get(field, "")) for field in fields)


def social_event_summary(event: dict[str, str]) -> str:
    """Format a concise social event answer."""
    fields = [
        event["title"],
        event["whenText"],
        event["whereText"],
        event["body"],
    ]
    return " - ".join(field for field in fields if field)


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
    location_sentence = next(
        (sentence for sentence in sentences if "Teatro Verdi" in sentence),
        sentences[0] if sentences else body,
    )
    walking_sentence = next(
        (sentence for sentence in sentences if "walking" in sentence or "bus" in sentence),
        "",
    )
    details = " ".join(sentence for sentence in [location_sentence, walking_sentence] if sentence)
    return details or "The ACSOS 2026 main social event details are available on the conference website."


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
