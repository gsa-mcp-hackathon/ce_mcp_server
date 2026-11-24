"""Finding the user's words in the user's text.

A model quotes the user ("as released", "skip the weights for that count"); code
finds the quote in the text, ignoring case, spacing and punctuation, and keeps
the exact span (D24). Phrase lists are matched the same forgiving way, on whole
words.
"""

from __future__ import annotations

import re
import unicodedata

_APOSTROPHES = "'’‘`´"


def _fold(ch: str) -> str:
    return unicodedata.normalize("NFKD", ch).casefold()


def _letters(text: str) -> tuple[str, list[int]]:
    """The letters and digits of `text`, folded, with each one's position."""
    chars: list[str] = []
    where: list[int] = []
    for i, ch in enumerate(text):
        for c in _fold(ch):
            if c.isalnum():
                chars.append(c)
                where.append(i)
    return "".join(chars), where


def locate(needle: str, text: str) -> tuple[int, int] | None:
    """Where `needle` occurs in `text`, ignoring case, spacing and punctuation.

    Returns the start and end of the exact span, or None. The first occurrence
    wins.
    """
    folded_needle, _ = _letters(needle)
    if not folded_needle:
        return None
    folded, where = _letters(text)
    at = folded.find(folded_needle)
    if at < 0:
        return None
    return where[at], where[at + len(folded_needle) - 1] + 1


def words(text: str) -> str:
    """Lowercase words separated by single spaces; apostrophes dropped."""
    folded = "".join(_fold(ch) for ch in text)
    folded = "".join("" if ch in _APOSTROPHES else ch for ch in folded)
    return " ".join(re.findall(r"[0-9a-z]+", folded))


def has_phrase(text_words: str, phrase: str) -> bool:
    """Whether `phrase` occurs in `text_words` (from `words`) as whole words."""
    target = words(phrase)
    if not target:
        return False
    return (
        re.search(rf"(?<![0-9a-z]){re.escape(target)}(?![0-9a-z])", text_words)
        is not None
    )
