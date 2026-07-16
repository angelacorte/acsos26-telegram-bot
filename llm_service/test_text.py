"""Tests for language-aware tokenisation and normalisation."""

from __future__ import annotations

from llm_service.text import normalize, split_sentences, tokenize


def test_normalize_strips_accents_case_and_punctuation() -> None:
    """Normalization should make matching accent- and case-insensitive."""
    assert normalize("Università") == "universita"
    assert normalize("Cesena-Campus:Room") == "cesena campus room"


def test_tokenize_drops_stopwords() -> None:
    """Stopwords carry no retrieval signal and must be removed."""
    assert tokenize("what is the venue") == ["venue"]


def test_tokenize_keeps_only_english_terms_without_expansion() -> None:
    """Tokenisation is plain English matching with no synonym expansion."""
    assert tokenize("accepted papers") == ["accepted", "papers"]


def test_split_sentences_trims_and_splits_on_boundaries() -> None:
    """Sentence splitting should yield clean, non-empty sentences."""
    assert split_sentences("First one.  Second one! Third?") == ["First one.", "Second one!", "Third?"]
