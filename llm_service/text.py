"""Text normalisation and tokenisation for the deterministic retrieval layer.

The retrieval and gating logic is dependency-free English matching. Keeping this
vocabulary in one module means every consumer tokenises identically.
"""

from __future__ import annotations

import re
import unicodedata

# Words carrying no retrieval signal, dropped before scoring.
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
    "me",
    "of",
    "on",
    "please",
    "tell",
    "the",
    "this",
    "to",
    "what",
    "which",
    "will",
    "who",
    "with",
}


def normalize(text: str) -> str:
    """Normalize text for robust, dependency-free matching (accent- and case-insensitive)."""
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return without_accents.casefold().replace("-", " ").replace(":", " ")


def tokenize(text: str) -> list[str]:
    """Split text into lowercase searchable terms, dropping stopwords."""
    return [term for term in re.findall(r"[a-z0-9]+", normalize(text)) if term not in STOPWORDS]


def split_sentences(text: str) -> list[str]:
    """Split compact page text into readable sentences."""
    return [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", text) if sentence.strip()]
