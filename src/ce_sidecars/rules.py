"""General rules that give a role, scale, unit and level to columns the dictionary's
codes and the generator's name patterns leave open.

A rule matches on the BLS data dictionary's text or on naming conventions BLS
uses throughout the Interview files, never on a list of column names: judgments
about single columns go in the table's overrides file. A rule only fills
properties that are not set yet, so the generator's own roles and a person's
overrides always win.

Rules run in two passes:

- `assign`, before the overrides, in this order (first match wins):
  imputation versions and means are only recognised here (their properties come
  later, from their base); imputation indicators; bracket medians; quarter pairs
  (`<stem>pq`/`<stem>cq`, then `<stem>p`/`<stem>c` and `…px4`/`…cx4`); counts;
  dollar amounts.
- `inherit`, after the overrides: each imputation version (`<stem>1` to
  `<stem>5`) and mean (`<stem>m`) takes its base column's role, scale, unit,
  level, top-coding and additivity, so a decision made on the base carries over
  to the whole set.

A rule's default level is the level of the table's rows, passed in by the
caller: `consumer_unit` for the household table, `member` for the member table.
A dollar amount always states its additivity: a quarter pair over consumer
units and months in scope, an annual amount over consumer units only, because
it repeats on every interview of a unit.

Where the vocabulary has no value for a column's unit, or a column's level is a
decision still open, the rule writes VERIFY, which the review list picks up.
"""

import re
from dataclasses import dataclass, field

from ce_ingest.normalize import normalize_name
from ce_sidecars.dictionary import FileDictionary

VERIFY = "VERIFY"
IMPUTATIONS = 5
_VERSION = re.compile(r"Imputation Iteration #([1-5])\s*-\s*(\w+)", re.I)
_INDICATOR = re.compile(r"Indicator/descriptor variable for income imputation", re.I)
_BRACKET = re.compile(r"median of bracket", re.I)
_COUNT = re.compile(r"^(?:(?:number|count|total number)\b|#)", re.I)
_COUNT_NO_UNIT = re.compile(r"\b(hours|weeks)\b", re.I)  # no vocabulary unit yet
_DOLLARS = re.compile(r"\b(amount|value|paid|owed|taxes)\b", re.I)
_ANNUAL = re.compile(r"12 months|annual|past year|last year", re.I)
_PERSON = re.compile(r"reference person|spouse", re.I)  # ce:level still open

DEFAULT_LEVEL = "consumer_unit"
QUARTERLY = {
    "ce:role": "measure",
    "ce:scale": "ratio",
    "ce:unit": "usd_quarterly",
    "ce:additiveOver": ["consumer_units", "months_in_scope"],
}
ANNUAL_ADDITIVE = ["consumer_units"]  # never summed across a unit's interviews
INHERITED = (
    "ce:role",
    "ce:scale",
    "ce:unit",
    "ce:level",
    "ce:topcoded",
    "ce:topcodeMethod",
    "ce:additiveOver",
)


@dataclass
class RuleResult:
    """Which rule set each column, and the review items the rules raise."""

    rule: dict[str, str] = field(default_factory=dict)
    review: list[tuple[str, str, str]] = field(
        default_factory=list
    )  # reason, column, tag
    sets: dict[str, list[str]] = field(default_factory=dict)  # base -> its members


def _text(entry: dict) -> str:
    return " ".join(
        f"{entry.get('businessName', '')} {entry.get('description', '')}".split()
    )


def _fill(custom: dict, values: dict) -> None:
    # Each column gets its own copy of a list: a list shared between columns
    # is written to YAML once and then as a reference (*id001) to it.
    for key, value in values.items():
        custom.setdefault(key, list(value) if isinstance(value, list) else value)


def _level(text: str, default: str) -> str:
    """The table's level, unless a consumer-unit table's column describes one
    person (the reference person or spouse): that level is still open."""
    if default == DEFAULT_LEVEL and _PERSON.search(text):
        return VERIFY
    return default


def _imputation_sets(entries: dict[str, dict]) -> tuple[dict[str, str], set[str]]:
    """Member column -> its stem, for `<stem>1`-`<stem>5` the dictionary describes
    as imputation iterations, and for `<stem>m`; plus the stems found."""
    stems = {
        name[:-1]
        for name, e in entries.items()
        if name[-1:].isdigit() and _VERSION.search(_text(e))
    }
    member: dict[str, str] = {}
    for stem in stems:
        for suffix in [str(i) for i in range(1, IMPUTATIONS + 1)] + ["m"]:
            if stem + suffix in entries:
                member[stem + suffix] = stem
    return member, stems


def _base_of(stem: str, entries: dict[str, dict]) -> str | None:
    """The reported column an imputation set belongs to: the one the dictionary
    names on its versions, else `<stem>` or `<stem>x` if stored."""
    for i in range(1, IMPUTATIONS + 1):
        e = entries.get(f"{stem}{i}")
        m = _VERSION.search(_text(e)) if e else None
        if m and normalize_name(m[2]) in entries:
            return normalize_name(m[2])
    return next((c for c in (stem, stem + "x") if c in entries), None)


def _partner(name: str, entries: dict[str, dict]) -> str | None:
    """The other quarter of a previous/current quarter pair, if both are stored."""
    for prev, cur in (("pq", "cq"), ("px4", "cx4"), ("p", "c")):
        for mine, other in ((prev, cur), (cur, prev)):
            if name.endswith(mine):
                stem = name[: -len(mine)]
                if stem and stem + prev in entries and stem + cur in entries:
                    return stem + other
    return None


def assign(
    entries: dict[str, dict], dictionary: FileDictionary, level: str = DEFAULT_LEVEL
) -> RuleResult:
    """Pass 1, before the overrides. `entries` maps stored name -> entry, with
    customProperties as a dict; entries are changed in place. `level` is the
    level of the table's rows, the default for every rule."""
    result = RuleResult()
    member, stems = _imputation_sets(entries)
    for stem in sorted(stems):
        base = _base_of(stem, entries)
        if base is None:
            for m in sorted(n for n, s in member.items() if s == stem):
                result.review.append(
                    ("Imputation set: base column not found", m, "review:verify")
                )
            continue
        result.sets[base] = sorted(n for n, s in member.items() if s == stem)

    for name, entry in entries.items():
        custom = entry["customProperties"]
        if "ce:role" in custom or entry.get("logicalType") != "number":
            continue
        text = _text(entry)
        if name in member:
            result.rule[name] = "imputation_set"
        elif _INDICATOR.search(text):
            stem = name[:-1]
            base = _base_of(stem, entries) if stem in stems else None
            _fill(custom, {"ce:role": "flag"})
            if base is not None:
                custom.setdefault("ce:flagFor", base)
            else:
                result.review.append(
                    ("Imputation indicator: column it describes not found", name,
                     "review:verify")
                )  # fmt: skip
            result.review.append(
                ("Imputation indicator: code meanings not described", name,
                 "review:verify")
            )  # fmt: skip
            result.rule[name] = "imputation_indicator"
        elif _BRACKET.search(text):
            _fill(
                custom,
                {"ce:role": "measure", "ce:scale": "ratio", "ce:unit": VERIFY,
                 "ce:level": level},
            )  # fmt: skip
            result.rule[name] = "bracket_median"
        elif (partner := _partner(name, entries)) is not None:
            _fill(custom, QUARTERLY | {"ce:level": level})
            custom.setdefault("ce:calendarPair", partner)
            result.rule[name] = "quarter_pair"
        elif _COUNT.search(entry.get("businessName", "")):
            unit = VERIFY if _COUNT_NO_UNIT.search(text) else "count"
            _fill(
                custom,
                {"ce:role": "measure", "ce:scale": "ratio", "ce:unit": unit,
                 "ce:level": _level(text, level)},
            )  # fmt: skip
            result.rule[name] = "count"
        elif _DOLLARS.search(text) or name.endswith("x"):
            unit = "usd_annual" if _ANNUAL.search(text) else VERIFY
            _fill(
                custom,
                {"ce:role": "measure", "ce:scale": "ratio", "ce:unit": unit,
                 "ce:level": _level(text, level)},
            )  # fmt: skip
            if custom.get("ce:unit") == "usd_annual":
                custom.setdefault("ce:additiveOver", list(ANNUAL_ADDITIVE))
            result.rule[name] = "dollar_amount"
    return result


def inherit(entries: dict[str, dict], result: RuleResult) -> None:
    """Pass 2, after the overrides: imputation versions and means take their
    base column's role, scale, unit, level, top-coding and additivity, and are
    marked as imputed."""
    for base, members in result.sets.items():
        source = entries[base]["customProperties"]
        for m in members:
            if m == base:
                continue
            custom = entries[m]["customProperties"]
            _fill(
                custom,
                {
                    k: list(source[k]) if isinstance(source[k], list) else source[k]
                    for k in INHERITED
                    if k in source
                },
            )
            custom.setdefault(
                "ce:imputed", {"multiple": True, "iterations": IMPUTATIONS}
            )
