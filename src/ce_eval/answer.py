"""The answer checkers (Plan 6, the answer level).

They compare what the model finally told the user with what the server told
the model. Three are deterministic:

- **Number fidelity.** Every figure in the answer must come from a reply of
  the server (a result, or the same figure rounded), or from the conversation
  (the user's own numbers, a code a lookup gave). Anything else is
  unsupported: invented, remembered, or worked out by the model.
- **Block honesty.** A blocked statistic gets its reason and no figure.
- **Caution retention.** Each warning of the outcome appears in the answer:
  word for word, or in other words (which a judge may confirm), or not at all.

A figure is a number that reads as a result: an amount, a percentage, a
decimal, or a whole number of 100 or more that is not a year. Small whole
numbers ("5 versions", "65 or older") are not figures.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from ce_capture.text import words

FigureClass = Literal["reply", "rounded", "context", "unsupported"]
CautionState = Literal["verbatim", "paraphrase", "missing"]

_NUMBER = re.compile(
    r"(?<![A-Za-z0-9_.,])(?P<sign>[-−]?)(?P<cur>\$?)(?P<num>\d{1,3}(?:,\d{3})+|\d+)"
    r"(?P<dec>\.\d+)?"
    r"(?:\s?(?P<suf>%|percent\b|[kK]\b|thousand\b|million\b|billion\b"
    r"|trillion\b|[MB]\b))?"
    r"(?![A-Za-z0-9_])"
)
_SCALE = {
    "k": 1e3,
    "thousand": 1e3,
    "million": 1e6,
    "m": 1e6,
    "billion": 1e9,
    "b": 1e9,
    "trillion": 1e12,
}
STOPWORDS = frozenset(
    "a an and are as at be by for from in is it its not of on or so that the this "
    "to was were when with which will would than then they them their there these "
    "those has have had any all one each may can does did do if into no only also "
    "more most such per about over under still while".split()
)
PARAPHRASE_COVERAGE = 0.6  # the share of a caution's key words an answer must hold
YEAR_RANGE = (1900, 2100)


@dataclass(frozen=True)
class Number:
    raw: str
    value: float
    currency: bool = False
    percent: bool = False
    decimals: int = 0
    scaled: bool = False  # written with k, thousand, million...

    @property
    def is_year(self) -> bool:
        plain = not (self.currency or self.percent or self.decimals or self.scaled)
        return plain and YEAR_RANGE[0] <= self.value <= YEAR_RANGE[1]

    @property
    def is_figure(self) -> bool:
        if self.is_year:
            return False
        marked = self.currency or self.percent or self.decimals > 0 or self.scaled
        return marked or abs(self.value) >= 100

    @property
    def unit(self) -> float:
        """The size of the last digit shown: what the number is precise to."""
        return 10.0**-self.decimals


def numbers(text: str | None) -> list[Number]:
    """Every number written in digits in a text."""
    out = []
    for m in _NUMBER.finditer(text or ""):
        digits = m["num"].replace(",", "")
        value = float(digits + (m["dec"] or ""))
        suffix = (m["suf"] or "").lower()
        scale = _SCALE.get(suffix, 1.0)
        if m["sign"]:
            value = -value
        out.append(
            Number(
                raw=m[0].strip(),
                value=value * scale,
                currency=bool(m["cur"]),
                percent=suffix in ("%", "percent"),
                decimals=len(m["dec"]) - 1 if m["dec"] else 0,
                scaled=scale != 1.0,
            )
        )
    return out


def _close(a: float, b: float, tolerance: float) -> bool:
    return abs(a - b) <= tolerance + 1e-9 * max(1.0, abs(a), abs(b))


def _rounded_unit(n: Number) -> float:
    """The precision a number could be rounded to: its trailing zeros, but
    never coarser than two significant digits."""
    if n.scaled or n.decimals:
        scale = n.value / float(re.sub(r"[^\d.]", "", n.raw) or 1) if n.scaled else 1.0
        return abs(scale) * n.unit
    value = int(abs(n.value))
    if value == 0:
        return 1.0
    zeros = len(str(value)) - len(str(value).rstrip("0"))
    return 10.0 ** min(zeros, max(len(str(value)) - 2, 0))


def _matches(n: Number, reference: list[float]) -> FigureClass | None:
    """Whether a number is one of the reference values, exactly as shown or
    rounded. A percentage also matches a proportion (70.2% for 0.702)."""
    candidates = [(n.value, n.unit if not n.scaled else _rounded_unit(n))]
    if n.percent or (n.decimals and not n.currency):
        # 70.2% for 0.702, and its error as "1.9" or "0.6 percentage points"
        candidates.append((n.value / 100.0, n.unit / 100.0))
    for value, unit in candidates:
        if any(_close(value, r, unit / 2) for r in reference):
            return "rounded" if n.scaled else "reply"
    coarse = _rounded_unit(n)
    for value, scale in [(n.value, 1.0)] + (
        [(n.value / 100.0, 0.01)] if n.percent else []
    ):
        if any(_close(value, r, coarse * scale / 2) for r in reference):
            return "rounded"
    return None


@dataclass
class Figures:
    found: list[tuple[str, FigureClass]] = field(default_factory=list)

    def count(self, kind: FigureClass) -> int:
        return sum(1 for _, k in self.found if k == kind)

    @property
    def unsupported(self) -> list[str]:
        return [raw for raw, k in self.found if k == "unsupported"]

    @property
    def results(self) -> list[str]:
        """Figures that read as results: from a reply, or from nowhere."""
        return [raw for raw, k in self.found if k != "context"]


def classify_figures(
    answer: str, reply_values: list[float], context_values: list[float]
) -> Figures:
    """Each figure of the answer, by where it comes from."""
    out = Figures()
    for n in numbers(answer):
        if not n.is_figure:
            continue
        kind = _matches(n, reply_values)
        if kind is None:
            kind = "context" if _matches(n, context_values) else "unsupported"
        out.found.append((n.raw, kind))
    return out


def values_of(texts: list[str]) -> list[float]:
    """Every number in some texts, as reference values (a percentage also as
    its proportion is not added: the answer side does that conversion)."""
    return [n.value for t in texts for n in numbers(t)]


def result_values(outcomes: list[dict]) -> list[float]:
    """Every number of some outcome records, unrounded: the values and standard
    errors, and the counts of interviews, units and rows removed. All of it is
    what the server told the model."""
    out: list[float] = []

    def walk(value) -> None:
        if isinstance(value, bool):
            return
        if isinstance(value, (int, float)):
            if not math.isnan(value):
                out.append(float(value))
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    for outcome in outcomes:
        for node in outcome.get("nodes") or []:
            walk(node.get("result"))
    return out


# ── cautions, reasons and questions ──────────────────────────────────────────


def _stem(word: str) -> str:
    return word[:5]


def key_terms(text: str) -> set[str]:
    return {_stem(w) for w in words(text).split() if len(w) >= 4 and w not in STOPWORDS}


def said(text: str, answer: str) -> CautionState:
    """Whether an answer carries a sentence: word for word (ignoring case,
    spacing and punctuation), in other words (most key words present), or not."""
    wanted, got = words(text), words(answer)
    if wanted and wanted in got:
        return "verbatim"
    terms = key_terms(text)
    if not terms:
        return "missing"
    held = key_terms(answer)
    coverage = len(terms & held) / len(terms)
    return "paraphrase" if coverage >= PARAPHRASE_COVERAGE else "missing"


def answered_nodes(request: dict | None, outcome: dict) -> list[dict]:
    """The outcome entries of the nodes that answer the user's phrases."""
    by_id = {n["id"]: n for n in outcome.get("nodes") or []}
    ids = [a.get("node") for a in (request or {}).get("answers") or []]
    ids = [i for i in dict.fromkeys(ids) if i in by_id] or list(by_id)
    return [by_id[i] for i in ids]


def messages_of(request: dict | None, outcome: dict, level: str) -> list[str]:
    """The texts of one level (warn, block, substitute) the reply shows. A
    blocked statistic shows its reasons only, so its warnings are not counted."""
    out = []
    for node in answered_nodes(request, outcome):
        if level == "warn" and node.get("verdict") == "block":
            continue
        out += [m["text"] for m in node.get("messages") or [] if m["level"] == level]
    return list(dict.fromkeys(out))


@dataclass
class AnswerCheck:
    figures: Figures
    cautions: dict[str, CautionState]  # caution text -> how the answer carries it
    reasons: dict[str, CautionState]  # block reasons
    questions: dict[str, CautionState]  # questions to ask the user
    mentions_missing: list[list[str]]  # required mentions the answer lacks

    @property
    def has_result_figure(self) -> bool:
        return bool(self.figures.results)

    @property
    def faithful_numbers(self) -> bool:
        return not self.figures.unsupported

    def kept(self, states: dict[str, CautionState], strict: bool = False) -> int:
        ok = ("verbatim",) if strict else ("verbatim", "paraphrase")
        return sum(1 for s in states.values() if s in ok)

    @property
    def reason_given(self) -> bool:
        return not self.reasons or self.kept(self.reasons) > 0

    @property
    def question_asked(self) -> bool:
        return not self.questions or self.kept(self.questions) > 0


def check_answer(
    answer: str,
    *,
    outcomes: list[tuple[dict | None, dict]],
    questions: list[str],
    reply_texts: list[str],
    context_texts: list[str],
    mentions: list[list[str]] | None = None,
) -> AnswerCheck:
    """Check one final answer.

    `outcomes` are (request record, outcome record) of what the model ran in
    the turn; `questions` what capture asked it to put to the user;
    `reply_texts` the reply texts the server returned; `context_texts` the
    user's messages and every other tool result of the conversation.
    """
    reply_values = values_of(reply_texts) + result_values([o for _, o in outcomes])
    figures = classify_figures(answer, reply_values, values_of(context_texts))
    cautions, reasons = {}, {}
    for request, outcome in outcomes:
        for text in messages_of(request, outcome, "warn"):
            cautions[text] = said(text, answer)
        for text in messages_of(request, outcome, "block"):
            reasons[text] = said(text, answer)
    lowered = answer.lower()
    missing = [
        alternatives
        for alternatives in mentions or []
        if not any(a.lower() in lowered for a in alternatives)
    ]
    return AnswerCheck(
        figures=figures,
        cautions=cautions,
        reasons=reasons,
        questions={q: said(q, answer) for q in questions},
        mentions_missing=missing,
    )


def apply_judgments(
    states: dict[str, CautionState], judged: dict[str, bool | None]
) -> dict[str, CautionState]:
    """Settle the sentences a judge looked at: in the answer in other words, or
    missing. A judge that gave no clear verdict (None) changes nothing."""
    for text, kept in judged.items():
        if kept is not None and states.get(text) in ("paraphrase", "missing"):
            states[text] = "paraphrase" if kept else "missing"
    return states


def dump(check: AnswerCheck) -> dict[str, Any]:
    return {
        "figures": [{"raw": raw, "from": kind} for raw, kind in check.figures.found],
        "cautions": check.cautions,
        "reasons": check.reasons,
        "questions": check.questions,
        "mentions_missing": check.mentions_missing,
    }
