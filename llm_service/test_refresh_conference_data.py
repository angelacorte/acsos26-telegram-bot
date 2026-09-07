"""Tests for the conference data scraper."""

from __future__ import annotations

from scripts.refresh_conference_data import (
    PROGRAM_URL,
    extract_detailed_sessions,
    extract_page_body,
    extract_program,
    program_status,
    track_status,
)


def test_extract_program_reconstructs_row_spanned_day_entries() -> None:
    """HTML row spans should become accurate per-day time ranges and categories."""
    html = """
    <table class="program">
      <thead>
        <tr>
          <th></th>
          <th>Monday <span class="date">7 September</span></th>
          <th>Tuesday <span class="date">8 September</span></th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td class="time">09:00 - 09:30</td>
          <td class="event workshop" rowspan="3">Workshops</td>
          <td class="event keynote" rowspan="3">
            Opening &amp; Keynote <span class="minor">Valeria Cardellini</span>
            <span class="room">Aula Magna</span>
          </td>
        </tr>
        <tr><td class="time">09:30 - 10:00</td></tr>
        <tr><td class="time">10:00 - 10:30</td></tr>
        <tr>
          <td class="time">10:30 - 11:00</td>
          <td class="event break">Coffee break</td>
          <td class="event break">Coffee break</td>
        </tr>
        <tr>
          <td class="time">11:00 - 11:30</td>
          <td class="event workshop" rowspan="4">Workshops</td>
          <td class="event main" rowspan="4">
            Main-track session <span class="room">Aula Magna</span>
          </td>
        </tr>
        <tr><td class="time">11:30 - 12:00</td></tr>
        <tr><td class="time">12:00 - 12:30</td></tr>
        <tr><td class="time">12:30 - 13:00</td></tr>
      </tbody>
    </table>
    """
    lines = ["NOTE: Additional social events (Tuesday, Thursday, and Friday evening) are extra"]

    program = extract_program(html, lines)

    assert program["url"] == PROGRAM_URL
    assert program["status"] == "Five-day overview of the ACSOS 2026 program."
    assert program["notes"] == [lines[0]]
    assert [day["day"] for day in program["days"]] == ["Monday", "Tuesday"]
    assert program["days"][1]["entries"] == [
        {
            "time": "09:00–10:30",
            "title": "Opening & Keynote",
            "details": "Valeria Cardellini",
            "room": "Aula Magna",
            "category": "keynote",
        },
        {
            "time": "10:30–11:00",
            "title": "Coffee break",
            "details": "",
            "room": "",
            "category": "break",
        },
        {
            "time": "11:00–13:00",
            "title": "Main-track session",
            "details": "",
            "room": "Aula Magna",
            "category": "main",
        },
    ]


def test_extract_detailed_sessions_reads_the_published_session_table() -> None:
    """Session names must exclude the track label, the room link, and any keynote abstract."""
    html = """
    <div class="hidable day-wrapper">
      <h4 class="day-header sticky-top"><div><div>Mon 7 Sep</div></div></h4>
      <table data-facet-date="Mon 7 Sep 2026" data-facet-date-order="260907"
             data-facet-track="ACSOS Main Track" data-facet-room="Aula Magna &quot;Carmen Tura&quot;"
             class="during-conference table session-table">
        <tr class="session-details"><td class="track-color c1"></td>
          <td><div class="slot-label">09:00 - 09:20</div></td>
          <td colspan="2"><div class="session-info-in-table">Registration<span class="pull-right">
            <a href="/track/acsos-2026-papers" class="text-muted navigate">Main Track</a></span>
            at <a href="/room/x" class="room-link navigate">Aula Magna &quot;Carmen Tura&quot;</a><br/></div></td>
        </tr>
      </table>
      <table data-facet-date="Mon 7 Sep 2026" data-facet-date-order="260907" data-facet-room="2.4"
             class="during-conference table session-table">
        <tr class="session-details"><td class="track-color c6"></td>
          <td><div class="slot-label">09:30 - 11:00</div></td>
          <td colspan="2"><div class="session-info-in-table">AI4AS - Keynote<span class="pull-right">
            <a href="/track/acsos-2026-workshops" class="text-muted navigate">Workshops</a></span>
            at <a href="/room/y" class="room-link navigate">2.4</a><br/></div></td>
        </tr>
        <tr><td><div class="abstract"><p>Long abstract text that must not become the title.&amp;nbsp;</p></div></td></tr>
        <tr data-slot-id="slot-1" class="hidable">
          <td class=" text-right"><div class="text-muted"><div class="start-time">09:30</div><strong>20m</strong></div>
            <div class="event-type">Paper</div><span data-facet-track="ACSOS Workshops"></span></td>
          <td><strong><a href="#" data-event-modal="e1">Q&amp;A Driven Adaptation</a></strong>
            <div class="performers"><a href="/profile/a" class="navigate">Ada Lovelace</a>
              <span class="prog-aff"> Politecnico</span></div></td>
        </tr>
      </table>
    </div>
    """

    sessions = extract_detailed_sessions(html)

    assert [session["title"] for session in sessions] == ["Registration", "AI4AS - Keynote"]
    assert all(session["date"] == "2026-09-07" for session in sessions)
    assert all(session["day"] == "Monday, 7 September" for session in sessions)
    assert sessions[0]["time"] == "09:00-09:20"
    assert sessions[0]["room"] == 'Aula Magna "Carmen Tura"'
    assert sessions[0]["trackId"] == "main"
    # The table carries no data-facet-track, so the track comes from the inner facet / track link.
    assert sessions[1]["trackId"] == "workshops"
    assert sessions[1]["papers"] == ["Q&A Driven Adaptation"]
    assert sessions[1]["talks"][0] == {
        "title": "Q&A Driven Adaptation",
        "time": "09:30",
        "duration": "20m",
        "kind": "Paper",
        "speakers": ["Ada Lovelace"],
    }


def test_generated_status_lines_never_hedge_about_the_programme() -> None:
    """The programme is published, so no generated status may call it tentative or unavailable."""
    sessions = [
        {"trackId": "main", "room": "Aula Magna", "papers": ["A", "B"]},
        {"trackId": "main", "room": "Aula Magna", "papers": []},
    ]
    conference = {
        "tracks": [{"acceptedPapers": [{"title": "A"}, {"title": "B"}]}],
        "sessions": sessions,
    }

    lines = [program_status(conference), track_status("Main Track", [{"title": "A"}], sessions)]

    assert "2 scheduled sessions" in lines[0]
    assert "1 accepted contribution;" in lines[1]
    for line in lines:
        for hedge in ("tentative", "not available yet", "not published", "subject to change"):
            assert hedge not in line.casefold()


def test_page_body_accepts_the_acsos_prefixed_heading() -> None:
    """researchr renders some headings as "ACSOS 2026 <title>"; the body must still be found."""
    lines = ["nav"] * 60 + [
        "ACSOS 2026 Visa Information",
        "A letter of invitation will be sent upon registration.",
        "x",
    ]

    assert extract_page_body(lines, "Visa Information").startswith("A letter of invitation")


def test_page_body_rejects_a_heading_matched_in_the_navigation() -> None:
    """Falling back to a nav or footer match must yield nothing, not the menu text."""
    lines = ["Visa Information", "Sign in Sign up", "Main Track Workshops Tutorials Artifacts"]

    assert extract_page_body(lines, "Visa Information") == ""
