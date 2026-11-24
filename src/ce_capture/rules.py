"""The phrase lists and the default set, read from `data/`."""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path

import yaml

from ce_capture.text import has_phrase, words

DATA = Path(__file__).parent / "data"


@dataclass(frozen=True)
class StatisticTerm:
    phrase: str
    statistics: frozenset[str]


@dataclass(frozen=True)
class ChoicePhrase:
    field: str
    phrase: str
    required: bool


@dataclass(frozen=True)
class Phrases:
    version: str
    statistic_terms: tuple[StatisticTerm, ...]
    choices: tuple[ChoicePhrase, ...]

    def statistics_named(self, text: str) -> tuple[list[str], set[str]]:
        """The statistic phrases in `text`, and the statistics they allow."""
        found = [t for t in self.statistic_terms if has_phrase(words(text), t.phrase)]
        allowed: set[str] = set()
        for term in found:
            allowed |= term.statistics
        return [t.phrase for t in found], allowed

    def required_choices(self, text: str) -> dict[str, list[str]]:
        """Choice field -> the required phrases of it that `text` holds."""
        text_words = words(text)
        out: dict[str, list[str]] = {}
        for c in self.choices:
            if c.required and has_phrase(text_words, c.phrase):
                out.setdefault(c.field, []).append(c.phrase)
        return out


@dataclass(frozen=True)
class Default:
    value: str
    source: str


@dataclass(frozen=True)
class Defaults:
    version: str
    values: dict[str, Default]

    def __getitem__(self, name: str) -> Default:
        return self.values[name]


@functools.cache
def load_phrases(path: Path = DATA / "phrases.yaml") -> Phrases:
    doc = yaml.safe_load(path.read_text())
    terms = tuple(
        StatisticTerm(str(t["phrase"]), frozenset(t["statistics"]))
        for t in doc["statistic_terms"]
    )
    choices = tuple(
        ChoicePhrase(field, str(p["phrase"]), bool(p["required"]))
        for field, phrases in doc["stated_choices"].items()
        for p in phrases
    )
    return Phrases(str(doc["version"]), terms, choices)


@functools.cache
def load_defaults(path: Path = DATA / "defaults.yaml") -> Defaults:
    doc = yaml.safe_load(path.read_text())
    values = {
        name: Default(str(d["value"]), str(d["source"]))
        for name, d in doc["defaults"].items()
    }
    return Defaults(str(doc["version"]), values)
