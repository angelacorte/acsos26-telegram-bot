"""Tests for the structured Program at a Glance scraper."""

from __future__ import annotations

from scripts.refresh_conference_data import PROGRAM_URL, extract_program


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
          <td class="event main" rowspan="4">Main-track session</td>
        </tr>
        <tr><td class="time">11:30 - 12:00</td></tr>
        <tr><td class="time">12:00 - 12:30</td></tr>
        <tr><td class="time">12:30 - 13:00</td></tr>
      </tbody>
    </table>
    """
    lines = [
        "Tentative schedule, SUBJECT TO CHANGE!",
        "Rough overview based on the tentative schedule; names and timings may change.",
    ]

    program = extract_program(html, lines)

    assert program["url"] == PROGRAM_URL
    assert program["status"] == "Tentative schedule, SUBJECT TO CHANGE!"
    assert program["notes"] == [lines[1]]
    assert [day["day"] for day in program["days"]] == ["Monday", "Tuesday"]
    assert program["days"][1]["entries"] == [
        {
            "time": "09:00–10:30",
            "title": "Opening & Keynote",
            "details": "Valeria Cardellini",
            "category": "keynote",
        },
        {
            "time": "10:30–11:00",
            "title": "Coffee break",
            "details": "",
            "category": "break",
        },
        {
            "time": "11:00–13:00",
            "title": "Main-track session",
            "details": "",
            "category": "main",
        },
    ]
