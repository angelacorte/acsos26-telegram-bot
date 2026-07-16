"""Data models exchanged across the service boundary.

`AskRequest`/`AskResponse` are the HTTP contract with the Telegram bot; `Chunk`
is the internal unit of retrievable conference knowledge.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field


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
