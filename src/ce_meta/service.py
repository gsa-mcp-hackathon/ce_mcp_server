"""The read-only metadata service: what the lookup tools answer from.

Sidecars first. Every answer is built from the sidecar set, which describes
all the loaded tables, and from the loader's own tables (`dataset_registry`,
`column_profile`). The dictionary tables, `unmatched_codes` and the geographic
crosswalk add to an answer once they are built; until then the fields they
feed are null and a notice names the command that builds them. No method fails
because one of them is missing, and none returns rows of a data table.
"""

import functools
import math
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC
from typing import Any, TypeVar

from ce_meta import names
from ce_meta.catalog import BUILD_COMMANDS, SURVEY, Area, Catalog
from ce_meta.errors import (
    CeMetaError,
    UnknownTable,
    UnknownVariable,
    YearNotCovered,
)
from ce_meta.history import History, VariableRow, covers, load_history
from ce_meta.models import (
    CodeEntry,
    DatasetInfo,
    DatasetList,
    Fact,
    GeographyEntry,
    GeographyList,
    GeoLevel,
    ImputedSet,
    Notice,
    ProfileStats,
    SidecarFacts,
    SurveyFacts,
    TableDetail,
    Usage,
    VariableDetail,
    VariableMatch,
    VariableSearch,
    WeightingMethod,
    WeightRoute,
)
from ce_meta.sidecars import TABLE_ID_PREFIX, Column, Report, SidecarSet, Table
from ce_meta.usage import review_notes, usable_as
from ce_store import survey_year_filter, survey_year_set

MAX_LIMIT = 50
MAX_CODE_LIMIT = 200
LABEL_CHARS = 100  # a code label longer than this is cut in an answer
TEXT_CHARS = 160  # a one-line description longer than this is cut
CODE_BUDGET = 3500  # characters of codes one answer may carry
CACHE_SIZE = 512

# The master rates everything below state together; `psu` is the one level
# below state the data names.
GEOGRAPHY_VALUE: dict[str, str] = {
    "region": "region",
    "division": "division",
    "state": "state",
    "psu": "belowState",
}
NO_ROLE = "not assigned"
NOT_LOADED = "not in the loaded data"
# Every detailed expenditure table's business name starts with this.
EXPN_TITLE = "CE Interview detailed expenditures: "
ID_ROLES = frozenset({"identifier"})  # their min and max are record-level values
# What the generator writes when a table has no description of its own.
_PLACEHOLDER = re.compile(r"^The \w+ table of the BLS Consumer Expenditure")
_IMPUTED_FROM = re.compile(r"Imputation Iteration #[1-5]\s*-\s*(\w+)", re.I)
_VERSIONS = ("1", "2", "3", "4", "5")

T = TypeVar("T")


def _cached(method: Callable[..., T]) -> Callable[..., T]:
    """Keep a method's answers per argument set. Errors are not kept."""

    @functools.wraps(method)
    def wrapper(self: "MetadataService", *args: Any, **kwargs: Any) -> T:
        key = (method.__name__, args, tuple(sorted(kwargs.items())))
        if key not in self._cache:
            if len(self._cache) >= CACHE_SIZE:
                self._cache.clear()
            self._cache[key] = method(self, *args, **kwargs)
        return self._cache[key]

    return wrapper


def _one_line(text: str | None) -> str | None:
    return " ".join(text.split()) if text else None


def _clip(text: str | None, limit: int) -> str | None:
    text = _one_line(text)
    if text and len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def _first_sentence(text: str | None) -> str | None:
    text = _one_line(text)
    if not text:
        return None
    return re.split(r"(?<=[a-z0-9)])\. ", text, maxsplit=1)[0].rstrip(".") + "."


def _words(query: str) -> list[re.Pattern[str]]:
    """One pattern per query word, matching at the start of a word."""
    return [
        re.compile(r"(?<![a-z0-9])" + re.escape(w))
        for w in re.findall(r"[a-z0-9_]+", query.lower())
    ]


# Words that carry no meaning in a search of several words.
_STOP_WORDS = frozenset(
    "a an and any are as at be been by did do does each every for from had has "
    "have how i in is it its many much of on or our per some than that the "
    "their then there they this to was we were what when where which who whose "
    "with you your".split()
)
_ENDINGS = ("ments", "ment", "ings", "ing", "ed", "es", "s")
# What a user may ask for that this server does not hold (describe_survey).
NOT_HELD = (
    "The Diary Survey: weekly purchases of small items bought often, such as "
    "single foods and drinks, housekeeping supplies and personal care "
    "products. The Interview Survey holds food only as broad totals.",
    "BLS published tables: they combine the Interview and Diary Surveys, use "
    "data that is not top-coded, and carry the 2025 adjustment, so a figure "
    "computed here will not match them.",
    "Survey years that are not loaded, and anything after the latest release: "
    "the data holds what was collected and makes no forecast.",
)
PARTIAL_SHARE = 0.5  # the share of a query's words a close match must hold


@dataclass(frozen=True)
class _Hits:
    """What one column holds of a query."""

    rank: float | None  # settled when it holds every word; None for a close match
    held: frozenset[str] = frozenset()  # the query words found, for a close match
    codes: list[CodeEntry] = field(default_factory=list)


def _stem(word: str) -> str:
    """A word less a common ending, when five letters or more are left, so
    that "enrollment" finds "enrolled" and "students" finds "student"."""
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 5:
            return word[: -len(ending)]
    return word


def _terms(query: str) -> list[tuple[str, re.Pattern[str]]]:
    """The words of a query that carry meaning, each with a pattern matching
    it (or its stem) at the start of a word. Filler words are dropped from a
    query of several words."""
    found = list(dict.fromkeys(re.findall(r"[a-z0-9_]+", query.lower())))
    kept = [w for w in found if w not in _STOP_WORDS] or found
    return [(w, re.compile(r"(?<![a-z0-9])" + re.escape(_stem(w)))) for w in kept]


STEM_ONLY = 0.25  # added to the rank of a match made through a stem alone


def _same_code(value: str, known: set[str]) -> bool:
    return value in known or (value.endswith(".0") and value[:-2] in known)


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class MetadataService:
    """Answers about the CE Interview tables, columns, codes and geographies."""

    def __init__(
        self, catalog: Catalog, sidecars: SidecarSet, report: Report, release: int
    ) -> None:
        self.catalog = catalog
        self.sidecars = sidecars
        self.report = report
        self.release = release
        self._cache: dict[tuple, Any] = {}
        self._loaded_history: History | None = None
        self._tables = names.ordered_tables(sidecars)

    # ── shared pieces ────────────────────────────────────────────────────────

    def notices(self, table: str | None = None) -> list[Notice]:
        """What the validator's report says a reader must know.

        A set-level failure goes on every answer; a table-level failure on
        answers about that table.
        """
        out = [
            Notice(scope="set", text=f"Sidecar set check failed: {f.detail}")
            for f in self.report.set_failures
        ]
        if table:
            out += [
                Notice(
                    scope="table", table=table, text=f"Sidecar check failed: {f.detail}"
                )
                for f in self.report.table_failures(table)
            ]
        return out

    def _not_built(self, area: Area, name: str, adds: str) -> list[Notice]:
        """A notice when a table that would add to the answer is not built."""
        if self.catalog.has(area, name):
            return []
        command = BUILD_COMMANDS[area]
        text = f"{adds}: not available until `{command}` has built {area}/{name}."
        return [Notice(scope="build", text=text)]

    def _fact(self, prop: str) -> Fact | None:
        master = self.sidecars.master
        if master is None or prop not in master.facts:
            return None
        vocab = self.sidecars.vocabulary
        return Fact(
            property=prop,
            value=master.facts[prop],
            implies=vocab.implies(prop) if vocab else None,
        )

    def _table(self, name: str) -> Table:
        return names.resolve_table(self.sidecars, name)

    def _year(self, year: int | None) -> int:
        return self.release if year is None else year

    def _history(self) -> History | None:
        """The dictionary tables, read once; None until `ce-meta build` has run."""
        if self._loaded_history is None:
            self._loaded_history = load_history(self.catalog)
        return self._loaded_history

    def _check_year(self, year: int | None) -> int:
        """The year an answer is for. With the dictionary tables built, a year
        they do not reach is refused, with the nearest year they do."""
        history = self._history()
        if year is None or history is None or history.first_year is None:
            return self._year(year)
        if year < history.first_year:
            raise YearNotCovered(year, [history.first_year])
        if year > self.release:
            raise YearNotCovered(year, [self.release])
        return year

    # ── describe_survey ──────────────────────────────────────────────────────

    @_cached
    def describe_survey(self) -> SurveyFacts:
        """The survey facts every request depends on."""
        master = self.sidecars.master
        facts = master.facts if master else {}
        context = (master.doc.get("context") or {}) if master else {}
        constraints = [
            _one_line(c.get("constraint")) or ""
            for c in context.get("constraints") or []
        ]
        weight = facts.get("ce:weight") or {}
        levels = facts.get("ce:geographyLevels") or {}
        weight_table = str(weight.get("table", "")).removeprefix(TABLE_ID_PREFIX)
        summary = (
            f"CE Interview Survey, {facts.get('ce:sourceRelease')} release. "
            f"The weight is {weight.get('column')} on {weight_table}; every other "
            "table reaches it through its relationship to that table. "
            "Geography: "
            + ", ".join(f"{k} {v}" for k, v in levels.items())
            + ". Read the constraints before estimating. This server holds the "
            "Interview Survey only: see not_held."
        )
        return SurveyFacts(
            release=facts.get("ce:sourceRelease"),
            target_population=facts.get("ce:targetPopulation"),
            design=self._fact("ce:design"),
            weight=self._fact("ce:weight"),
            reference_period=self._fact("ce:referencePeriod"),
            geography_levels=self._fact("ce:geographyLevels"),
            breaks=self._fact("ce:breaks"),
            constraints=[c for c in constraints if c],
            not_held=list(NOT_HELD),
            notices=self.notices(),
            summary=summary,
        )

    @_cached
    def weighting_method(self) -> WeightingMethod:
        """The weight, the replicate weights, the variance method and the period."""
        weight = self._fact("ce:weight")
        variance = self._fact("ce:variance")
        column = (weight.value or {}).get("column") if weight else None
        summary = (
            f"Population estimates use {column}. Standard errors re-estimate the "
            "statistic with each replicate weight and apply the variance formula "
            "for the release."
        )
        return WeightingMethod(
            weight=weight,
            replicate_weights=self._fact("ce:replicateWeights"),
            variance=variance,
            reference_period=self._fact("ce:referencePeriod"),
            notices=self.notices(),
            summary=summary,
        )

    # ── list_datasets ────────────────────────────────────────────────────────

    def _loads(self) -> dict[str, dict[tuple[int, str], dict]]:
        """The latest successful load of each partition, by table."""
        if not self.catalog.has("meta", "dataset_registry"):
            return {}
        source = self.catalog.source("meta", "dataset_registry")
        rows = self.catalog.rows(
            f"""
            select "table", survey_year, quarter_code, row_count,
                   (loaded_at at time zone 'UTC') as loaded_at
            from (
                select *, row_number() over (
                    partition by "table", survey_year, quarter_code
                    order by loaded_at desc
                ) as attempt
                from {source}
                where survey = ? and status in ('loaded', 'loaded_with_warnings')
            )
            where attempt = 1
            """,
            [SURVEY],
        )
        out: dict[str, dict[tuple[int, str], dict]] = {}
        for r in rows:
            out.setdefault(r["table"], {})[(r["survey_year"], r["quarter_code"])] = r
        return out

    def _row_represents(self, table: Table | None, sidecar_id: str) -> str | None:
        if table is not None and table.facts.get("ce:rowRepresents"):
            return table.facts["ce:rowRepresents"]
        master = self.sidecars.master
        entry = next(
            (
                t
                for t in (master.tables if master else [])
                if t.get("sidecar") == sidecar_id
            ),
            None,
        )
        return entry.get("rowRepresents") if entry else None

    def _description(self, table: Table) -> tuple[str | None, str | None, bool]:
        """Purpose, limitations, and whether a person or template wrote them."""
        text = table.doc.get("description") or {}
        purpose = _one_line(text.get("purpose"))
        written = bool(purpose) and not _PLACEHOLDER.match(purpose)
        return purpose, _one_line(text.get("limitations")), written

    @_cached
    def list_datasets(self, year: int | None = None) -> DatasetList:
        """The Interview tables, what a row of each is, and what is loaded."""
        year = self._year(year)
        loads = self._loads()
        year_set = set(survey_year_set(SURVEY, year))
        master = self.sidecars.master
        listed = [
            str(t["sidecar"]).removeprefix(TABLE_ID_PREFIX)
            for t in (master.tables if master else [])
        ]
        stored = set(listed) | {t.name for t in self._tables} | set(loads)
        all_parts = Counter(
            tuple(sorted(f"{y}/{q}" for y, q in parts)) for parts in loads.values()
        )
        common = list(all_parts.most_common(1)[0][0]) if all_parts else []

        datasets: list[DatasetInfo] = []
        for name in sorted(stored, key=names.table_rank):
            table = self.sidecars.table(name)
            parts = loads.get(name, {})
            in_year = [r["row_count"] for key, r in parts.items() if key in year_set]
            held = sorted(f"{y}/{q}" for y, q in parts)
            schema = (table.doc.get("schema") or [{}])[0] if table else {}
            purpose = None
            if table is not None and name in names.CORE_ORDER:
                written_purpose, _, written = self._description(table)
                purpose = (
                    _clip(_first_sentence(written_purpose), TEXT_CHARS)
                    if written
                    else _one_line(schema.get("description"))
                )
            datasets.append(
                DatasetInfo(
                    table=name,
                    title=_clip(
                        str(schema.get("businessName") or "").removeprefix(EXPN_TITLE)
                        or None,
                        90,
                    ),
                    purpose=purpose,
                    row_represents=self._row_represents(table, TABLE_ID_PREFIX + name),
                    rows=sum(in_year) if in_year else None,
                    partitions=None if held == common else held,
                    has_sidecar=table is not None,
                )
            )

        loaded = sum(1 for d in datasets if d.rows is not None)
        notices = self.notices()
        notices += self._not_built("meta", "dataset_registry", "What is loaded")
        summary = (
            f"{len(datasets)} Interview tables; {loaded} have rows loaded for survey "
            f"year {year}. A dataset ID is intrvw_<table>. Rows are interviews in fmli "
            "only; see row_represents for the others."
        )
        latest = max(
            (r["loaded_at"] for parts in loads.values() for r in parts.values()),
            default=None,
        )
        if latest is not None:
            latest = latest.replace(tzinfo=UTC)  # read as a naive UTC timestamp
        return DatasetList(
            year=year,
            partitions=common,
            loaded_at=latest,
            datasets=datasets,
            notices=notices,
            summary=summary,
        )

    # ── describe_table ───────────────────────────────────────────────────────

    def _weight_route(self, table: Table) -> WeightRoute | None:
        master = self.sidecars.master
        for rel in master.relationships if master else []:
            if rel.get("child") != table.id or rel.get("weightFrom") != "parent":
                continue
            keys = rel.get("keys") or {}
            return WeightRoute(
                parent=str(rel.get("parent", "")).removeprefix(TABLE_ID_PREFIX),
                parent_keys=list(keys.get("parent") or []),
                child_keys=list(keys.get("child") or []),
                child_rows_optional=bool(rel.get("childRowsOptional")),
                note=_one_line(rel.get("note")),
            )
        return None

    def _same_unit_keys(self, table: Table) -> list[str]:
        master = self.sidecars.master
        for rel in master.relationships if master else []:
            same = rel.get("parent") == table.id == rel.get("child")
            if same and rel.get("cardinality") == "same_unit":
                return list((rel.get("keys") or {}).get("parent") or [])
        return []

    @_cached
    def describe_table(self, table: str) -> TableDetail:
        """What one row is, the row key, and how the table reaches the weight."""
        found = self._table(table)
        master = self.sidecars.master
        entry = next(
            (
                t
                for t in (master.tables if master else [])
                if t.get("sidecar") == found.id
            ),
            {},
        )
        schema = (found.doc.get("schema") or [{}])[0]
        purpose, limitations, written = self._description(found)
        columns = list(found.columns.values())
        by_role = Counter(c.get("ce:role") or NO_ROLE for c in columns)
        route = self._weight_route(found)
        carries = bool(entry.get("carriesWeight"))

        if carries:
            reach = "It carries the weight."
        elif route:
            zero = (
                "an interview with no rows here counts as zero"
                if route.child_rows_optional
                else "every interview has rows here"
            )
            reach = (
                f"It carries no weight: sum its rows to {', '.join(route.child_keys)} "
                f"and use the weight of the matching {route.parent} row ({zero})."
            )
        else:
            reach = "It has no route to the weight."
        row_sentence = _one_line(schema.get("description"))
        summary = (
            f"{found.name}: {row_sentence or 'row meaning not described.'} "
            f"Row key: {', '.join(found.row_key) or 'not stated'}. {reach}"
        )
        notices = self.notices(found.name)
        if not written:
            notices.append(
                Notice(
                    scope="table",
                    table=found.name,
                    text="The table's description is not written yet.",
                )
            )
        return TableDetail(
            table=found.name,
            sidecar=found.id,
            row_represents=found.facts.get("ce:rowRepresents"),
            row_sentence=row_sentence,
            row_key=found.row_key,
            purpose=purpose if written else None,
            limitations=limitations if written else None,
            description_written=written,
            carries_weight=carries,
            weight_route=route,
            same_unit_keys=self._same_unit_keys(found),
            period_columns=[c.name for c in columns if c.get("ce:role") == "period"],
            columns_by_role=dict(sorted(by_role.items())),
            columns_needing_review=sum(1 for c in columns if c.needs_review),
            notices=notices,
            summary=summary,
        )

    # ── search_variables ─────────────────────────────────────────────────────

    def _match(
        self, table: Table, column: Column, codes: list[CodeEntry]
    ) -> VariableMatch:
        source = column.get("ce:sourceName")
        return VariableMatch(
            name=column.name,
            # the BLS spelling, when it is more than the stored name in capitals
            source_name=source if source and source != column.name.upper() else None,
            table=table.name,
            description=_clip(column.business_name or column.description, TEXT_CHARS),
            role=column.get("ce:role"),
            unit=column.get("ce:unit"),
            usable_as=usable_as(column),
            review=review_notes(column),
            matched_codes=codes,
        )

    @staticmethod
    def _matching_codes(pairs: Any, words: list[re.Pattern[str]]) -> list[CodeEntry]:
        """Up to three codes whose label holds every word of the query."""
        found = [
            CodeEntry(value=value, label=_clip(label, LABEL_CHARS))
            for value, label in pairs
            if all(w.search((label or "").lower()) for w in words)
        ]
        return found[:3]

    @staticmethod
    def _hits(
        q: str,
        terms: list[tuple[str, re.Pattern[str]]],
        name: str,
        source: str,
        title: str,
        text: str,
        pairs: Any,
    ) -> _Hits | None:
        """What of a query one column holds, or None when it holds nothing.

        A name match is settled here. Otherwise the words are looked for in
        the title, in the description, and in the code labels: the words the
        description lacks may come from one code label ("Toyota vehicle make"
        finds the make column through its code), never from several labels
        pieced together, which a long code list would always satisfy.
        """
        if q in (name, source):
            return _Hits(rank=0.0)
        if name.startswith(q) or source.startswith(q):
            return _Hits(rank=1.0)
        if " " not in q and (q in name or q in source):
            return _Hits(rank=2.0)
        if not terms:
            return None
        patterns = [p for _, p in terms]
        # the words as written rank before a match through their stems
        # ("classified" finds "classified" before "classification")
        as_written = all(
            re.search(r"(?<![a-z0-9])" + re.escape(w), text) for w, _ in terms
        )
        loose = 0.0 if as_written else STEM_ONLY
        if all(p.search(title) for p in patterns):
            return _Hits(rank=3.0 + loose)
        if all(p.search(text) for p in patterns):
            return _Hits(rank=3.5 + loose)
        labels = [(value, (label or "").lower(), label) for value, label in pairs]
        whole = [
            CodeEntry(value=value, label=_clip(label, LABEL_CHARS))
            for value, low, label in labels
            if all(p.search(low) for p in patterns)
        ]
        if whole:
            return _Hits(rank=4.0, codes=whole[:3])
        in_text = {w for w, p in terms if p.search(text)}
        rest = [(w, p) for w, p in terms if w not in in_text]
        by_code = [
            (value, label, frozenset(w for w, p in rest if p.search(low)))
            for value, low, label in labels
        ]
        most = max((len(found) for _, _, found in by_code), default=0)
        best = [c for c in by_code if most and len(c[2]) == most]
        in_code = best[0][2] if best else frozenset()
        held = in_text | in_code
        if not held:
            return None
        codes = [
            CodeEntry(value=value, label=_clip(label, LABEL_CHARS))
            for value, label, found in best
            if found == in_code
        ]
        return _Hits(rank=None, held=frozenset(held), codes=codes[:3])

    @staticmethod
    def _close(
        hits: _Hits, terms: list[tuple[str, re.Pattern[str]]], weight: dict[str, float]
    ) -> tuple[float, list[str]] | None:
        """The rank of a column that holds some of the words, with the words
        it lacks; None when it holds too little. A word few columns hold
        counts for more than a common one, and a word no column holds counts
        for nothing, though it is still listed as lacking."""
        lacking = [w for w, _ in terms if w not in hits.held]
        if not lacking:
            return 4.5, []
        total = sum(weight[w] for w, _ in terms)
        if len(terms) < 2 or not total:
            return None
        share = sum(weight[w] for w in hits.held) / total
        if share < PARTIAL_SHARE:
            return None
        return 6.0 - share, lacking

    def search_variables(
        self,
        query: str,
        table: str | None = None,
        year: int | None = None,
        usable_as: Usage | None = None,
        limit: int = 20,
    ) -> VariableSearch:
        """Columns matching a phrase, best first."""
        return self._search(query.strip(), table, year, usable_as, limit)

    @staticmethod
    def _dictionary_match(row: VariableRow, codes: list[CodeEntry]) -> VariableMatch:
        """A variable the dictionary has and the loaded data does not."""
        plain = row.source_name == row.name.upper()
        return VariableMatch(
            name=row.name,
            source_name=None if plain else row.source_name,
            table=row.table,
            description=_clip(row.description, TEXT_CHARS),
            first_year=row.first_year,
            last_year=row.last_year,
            in_loaded_data=False,
            role=None,
            unit=None,
            usable_as=[],
            review=[NOT_LOADED],
            matched_codes=codes,
        )

    def _search_tables(
        self, table: str | None, history: History | None
    ) -> tuple[list[Table], str | None]:
        """The sidecar tables to search, and a dictionary-only table if named."""
        if table is None:
            return self._tables, None
        try:
            return [self._table(table)], None
        except UnknownTable:
            name = table.strip().lower()
            if history is not None and name in history.tables:
                return [], name
            raise

    @_cached
    def _search(
        self,
        query: str,
        table: str | None,
        year: int | None,
        usage: Usage | None,
        limit: int,
    ) -> VariableSearch:
        if not query:
            raise CeMetaError("The query is empty.", ["Give a word or a column name."])
        limit = max(1, min(limit, MAX_LIMIT))
        history = self._history()
        at = self._check_year(year)
        past = history is not None and at != self.release
        q = query.lower()
        terms = _terms(query)
        tables, only_table = self._search_tables(table, history)

        # First what every column holds of the query; then, knowing how common
        # each word is, the rank of the columns that hold only some of it.
        found: list[tuple[_Hits, tuple, Any]] = []
        for t in tables:
            for col in t.columns.values():
                if usage and usage not in usable_as(col):
                    continue
                row = history.covering(t.name, col.name, at) if history else None
                if past and row is None:
                    continue  # the variable did not exist that year
                name = col.name
                source = str(col.get("ce:sourceName") or "").lower()
                title = (col.business_name or "").lower()
                text = f"{title} {(col.description or '').lower()}"
                hits = self._hits(q, terms, name, source, title, text, col.codes)
                if hits is None:
                    continue
                # Within a rank: columns a request can use before flags and
                # identifiers, core tables before the others, then the shorter
                # (more specific) title.
                usable = 0 if usable_as(col) else 1
                order = (usable, names.table_rank(t.name)[0], len(title), name)
                found.append((hits, order, (t, col, row)))

        # Variables the dictionary has for that year and the loaded data does
        # not. They cannot fill a slot of a request, so a search by use skips
        # them; otherwise they rank below every loaded column.
        if history is not None and usage is None:
            wanted = {only_table} if only_table else {t.name for t in tables}
            for tbl, name in history.keys():
                if table is not None and tbl not in wanted:
                    continue
                if self.sidecars.column(tbl, name) is not None:
                    continue
                row = history.covering(tbl, name, at)
                if row is None:
                    continue
                text = (row.description or "").lower()
                pairs = [(c.value, c.label) for c in history.codes(tbl, name, at)]
                hits = self._hits(
                    q, terms, name, row.source_name.lower(), text, text, pairs
                )
                if hits is None:
                    continue
                order = (2, names.table_rank(tbl)[0], len(text), name)
                found.append((hits, order, row))

        held_by = Counter(w for hits, _, _ in found for w in hits.held)
        weight = {
            w: 1 / math.log(2 + held_by[w]) if held_by[w] else 0.0 for w, _ in terms
        }
        ranked: list[tuple[tuple, VariableMatch]] = []
        for hits, order, what in found:
            lacking: list[str] = []
            rank = hits.rank
            if rank is None:
                close = self._close(hits, terms, weight)
                if close is None:
                    continue
                rank, lacking = close
            update: dict[str, Any] = {"lacks": lacking} if lacking else {}
            if isinstance(what, tuple):
                t, col, row = what
                match = self._match(t, col, hits.codes)
                if row is not None:
                    update |= {"first_year": row.first_year, "last_year": row.last_year}
            else:
                match = self._dictionary_match(what, hits.codes)
            if update:
                match = match.model_copy(update=update)
            ranked.append(((rank, *order), match))

        ranked.sort(key=lambda item: item[0])
        matches = [m for _, m in ranked[:limit]]

        notices = self.notices(table)
        if year is not None and year != self.release:
            notices += self._not_built(
                "ref", "variables", f"Variables as they stood in {year}"
            )
        where = f" in {table}" if table else ""
        as_what = f" usable as a {usage}" if usage else ""
        whole = sum(1 for key, _ in ranked if key[0] < 5.0)
        close = len(ranked) - whole
        found = "1 column matches" if whole == 1 else f"{whole} columns match"
        if whole and close:
            summary = (
                f"{found} {query!r}{where}{as_what}; {close} more hold some of "
                f"its words (`lacks` lists the rest). Showing {len(matches)}."
            )
        elif whole:
            summary = f"{found} {query!r}{where}{as_what}; showing {len(matches)}."
        elif close:
            summary = (
                f"No column holds every word of {query!r}{where}{as_what}. "
                f"Showing the {len(matches)} closest of {close}; `lacks` lists "
                "the words each one is missing."
            )
        else:
            summary = (
                f"No column matches {query!r}{where}{as_what}. Try another word "
                "for the same thing, or fewer words; list_datasets shows the "
                "tables, and describe_table what a table holds. A small item "
                "bought often (a single food, a cleaning or personal care "
                "product) is collected in the Diary Survey, which is not held."
            )
        return VariableSearch(
            query=query,
            matches=matches,
            total=len(ranked),
            notices=notices,
            summary=summary,
        )

    # ── describe_variable ────────────────────────────────────────────────────

    def _profile(
        self, table: str, column: Column
    ) -> tuple[ProfileStats | None, str | None]:
        """The loader's profile of a column, and its stored type."""
        if not self.catalog.has("meta", "column_profile"):
            return None, None
        source = self.catalog.source("meta", "column_profile")
        rows = self.catalog.rows(
            f"""
            select data_type, row_count, null_share, "min", "max", distinct_count
            from {source}
            where survey = ? and "table" = ? and column_name = ?
            """,
            [SURVEY, table, column.name],
        )
        if not rows:
            return None, None
        r = rows[0]
        hide = column.get("ce:role") in ID_ROLES
        undocumented = self._undocumented(table, column.name)
        stats = ProfileStats(
            rows=r["row_count"],
            null_share=r["null_share"],
            distinct_count=r["distinct_count"],
            min=None if hide else r["min"],
            max=None if hide else r["max"],
            undocumented_values=undocumented,
        )
        return stats, r["data_type"]

    def _undocumented(self, table: str, column: str) -> list[str] | None:
        if not self.catalog.has("meta", "unmatched_codes"):
            return None
        source = self.catalog.source("meta", "unmatched_codes")
        rows = self.catalog.rows(
            f"""
            select "value" from {source}
            where survey = ? and "table" = ? and column_name = ?
            order by row_count desc limit 20
            """,
            [SURVEY, table, column],
        )
        return [str(r["value"]) for r in rows]

    @staticmethod
    def _flag_column(table: Table, name: str) -> str | None:
        for col in table.columns.values():
            if col.get("ce:flagFor") == name and col.get("ce:role") == "flag":
                return col.name
        return None

    @staticmethod
    def _imputed_set(table: Table, column: Column) -> ImputedSet | None:
        """The imputation set a column belongs to, as base, versions and mean."""
        cols = table.columns

        def members(stem: str) -> tuple[list[str], str | None]:
            versions = [stem + v for v in _VERSIONS if stem + v in cols]
            mean = stem + "m" if stem + "m" in cols else None
            return versions, mean

        def base_of(stem: str, versions: list[str]) -> str | None:
            for v in versions:
                m = _IMPUTED_FROM.search(cols[v].description or "")
                if m and m[1].lower() in cols:
                    return m[1].lower()
            return next((c for c in (stem, stem + "x") if c in cols), None)

        name = column.name
        if column.get("ce:imputed") and name[-1:] in (*_VERSIONS, "m"):
            versions, mean = members(name[:-1])
            if versions:
                return ImputedSet(
                    base=base_of(name[:-1], versions), versions=versions, mean=mean
                )
        # the base column of a set: its versions name it in their descriptions
        for other in cols.values():
            if not other.get("ce:imputed") or other.name[-1:] not in _VERSIONS:
                continue
            versions, mean = members(other.name[:-1])
            if versions and base_of(other.name[:-1], versions) == name:
                return ImputedSet(base=name, versions=versions, mean=mean)
        if column.get("ce:imputed") and "impnum" in table.row_key:
            return ImputedSet(
                base=None, versions=[], mean=None, version_column="impnum"
            )
        return None

    @staticmethod
    def _codes(
        pairs: list[tuple[str, str | None]],
        code_query: str | None,
        code_limit: int,
        years: dict[str, tuple[int | None, int | None]] | None = None,
    ) -> tuple[list[CodeEntry], int | None]:
        """Up to `code_limit` codes, within the answer's size budget.

        `years` gives a code its first and last year, where they say something
        the variable's own years do not.
        """
        matched = None
        if code_query:
            q = code_query.strip().lower()
            words = _words(code_query)
            pairs = [
                (v, lab)
                for v, lab in pairs
                if v.lower().startswith(q)
                or (words and all(w.search((lab or "").lower()) for w in words))
            ]
            matched = len(pairs)
        years = years or {}
        out: list[CodeEntry] = []
        used = 0
        for value, label in pairs[:code_limit]:
            first, last = years.get(value, (None, None))
            entry = CodeEntry(
                value=value,
                label=_clip(label, LABEL_CHARS),
                first_year=first,
                last_year=last,
            )
            used += len(value) + len(entry.label or "") + 24
            if out and used > CODE_BUDGET:
                break
            out.append(entry)
        return out, matched

    @staticmethod
    def _code_years(
        history: History | None, table: str, name: str, at: int
    ) -> dict[str, tuple[int | None, int | None]]:
        """Years worth showing on a code: a first year later than the oldest
        code's (a code added since), and a last year when the code has ended."""
        if history is None:
            return {}
        rows = history.codes(table, name, at)
        firsts = [c.first_year for c in rows if c.first_year is not None]
        oldest = min(firsts, default=None)
        out = {}
        for c in rows:
            added = c.first_year if c.first_year != oldest else None
            if added is not None or c.last_year is not None:
                out[c.value] = (added, c.last_year)
        return out

    def _caveats(self, column: Column) -> list[str]:
        vocab = self.sidecars.vocabulary
        if vocab is None:
            return []
        out = []
        for prop in column.facts:
            if prop == "ce:sourceName":
                continue  # a naming fact, not a caveat
            implies = vocab.implies(prop)
            if implies:
                out.append(f"{prop}: {implies}")
        return out

    def describe_variable(
        self,
        name: str,
        table: str | None = None,
        year: int | None = None,
        code_query: str | None = None,
        code_limit: int = 50,
    ) -> VariableDetail:
        """Everything known about one column."""
        return self._describe(name.strip(), table, year, code_query, code_limit)

    def _describe_from_dictionary(
        self,
        keys: list[tuple[str, str]],
        at: int,
        code_query: str | None,
        code_limit: int,
        history: History,
    ) -> VariableDetail:
        """A variable the dictionary has and the loaded data does not."""
        ordered = sorted(keys, key=lambda k: names.table_rank(k[0]))
        table, name = ordered[0]
        row = history.covering(table, name, at) or history.latest(table, name)
        assert row is not None  # `keys` come from the history itself
        in_force = covers(row.first_year, row.last_year, at)
        pairs = [
            (c.value, c.label)
            for c in history.codes(table, name, at if in_force else row.first_year)
        ]
        codes, matched = self._codes(pairs, code_query, code_limit)
        match = self._dictionary_match(row, [])
        years = f"{row.first_year} to {row.last_year or 'now'}"
        text = f"{name} ({table}) is not in the loaded data. It is described from "
        text += f"the BLS dictionary only, as it stood from {years}."
        return VariableDetail(
            **match.model_dump(exclude={"description"}),
            description=_clip(row.description, 600),
            business_name=None,
            data_type=row.data_type,
            codes=codes,
            codes_total=len(pairs),
            codes_matched=matched,
            calendar_pair=None,
            flag_column=row.flag_name,
            imputed_set=None,
            profile=None,
            sidecar=None,
            caveats=[],
            also_in=[tbl for tbl, _ in ordered[1:]],
            sources=["BLS CE data dictionary (ref/variables, ref/codes)"],
            notices=[Notice(scope="table", table=table, text=text)],
            summary=(
                f"{name} is a variable of the {table} file, {years}: "
                f"{_clip(row.description, 120) or 'no description'}. "
                f"{NOT_LOADED.capitalize()}."
            ),
        )

    @_cached
    def _describe(
        self,
        name: str,
        table: str | None,
        year: int | None,
        code_query: str | None,
        code_limit: int,
    ) -> VariableDetail:
        code_limit = max(1, min(code_limit, MAX_CODE_LIMIT))
        history = self._history()
        at = self._check_year(year)
        try:
            found = names.resolve_name(self.sidecars, self.catalog, name, table, year)
        except (UnknownVariable, UnknownTable):
            wanted = table.strip().lower() if table else None
            only = history.find(name, wanted) if history else []
            if not only:
                raise
            return self._describe_from_dictionary(
                only, at, code_query, code_limit, history
            )
        table_name, stored = found[0]
        t = self._table(table_name)
        column = t.columns[stored]
        row = history.covering(t.name, stored, at) if history else None
        past = history is not None and at != self.release
        if past and row is None and history.ranges(t.name, stored):
            raise YearNotCovered(
                at, history.years(t.name, stored), what=f"{t.name}.{stored}"
            )

        match = self._match(t, column, [])
        if row is not None:
            match = match.model_copy(
                update={"first_year": row.first_year, "last_year": row.last_year}
            )
        # another year's codes come from the dictionary; the release's from the
        # sidecar, which also holds what a person corrected
        pairs = (
            [(c.value, c.label) for c in history.codes(t.name, stored, at)]
            if past
            else list(column.codes)
        )
        codes, matched = self._codes(
            pairs,
            code_query,
            code_limit,
            self._code_years(history, t.name, stored, at),
        )
        profile, data_type = self._profile(t.name, column)

        notices = self.notices(t.name)
        notices += self._not_built(
            "ref", "variables", "Coverage years and the history of the codes"
        )
        if column.codes:
            notices += self._not_built(
                "meta",
                "unmatched_codes",
                "Values in the data that are not listed codes",
            )
        if profile is None:
            notices += self._not_built("meta", "column_profile", "Profile statistics")

        role = str(column.get("ce:role") or "column with no role assigned")
        role = role.replace("_", " ")
        article = "an" if role[0] in "aeiou" else "a"
        unit = f", unit {column.get('ce:unit')}" if column.get("ce:unit") else ""
        review = match.review
        about = _clip(column.business_name or column.description, 120)
        summary = (
            f"{stored} is {article} {role} on the {t.name} table{unit}: "
            f"{about or 'no description'}."
            + (f" Under review: {', '.join(review)}." if review else "")
        )
        release = (
            self.sidecars.master.facts.get("ce:sourceRelease")
            if self.sidecars.master
            else self.release
        )
        sources = [
            f"{t.id} {t.version}",
            f"BLS CE data dictionary, {release} release (through the sidecar)",
        ]
        if history is not None:
            sources.append("ref/variables and ref/codes, for the years")
        return VariableDetail(
            **match.model_dump(exclude={"description"}),
            description=_clip(column.description, 600),
            business_name=column.business_name,
            data_type=data_type or column.logical_type,
            codes=codes,
            codes_total=len(pairs),
            codes_matched=matched,
            calendar_pair=column.get("ce:calendarPair"),
            flag_column=self._flag_column(t, stored),
            imputed_set=self._imputed_set(t, column),
            profile=profile,
            sidecar=SidecarFacts(
                id=t.id,
                facts=dict(column.facts),
                needs_review=column.needs_review,
                unverified=list(column.unverified),
            ),
            caveats=self._caveats(column),
            also_in=[tbl for tbl, _ in found[1:]],
            sources=sources,
            notices=notices,
            summary=summary,
        )

    # ── list_geographies ─────────────────────────────────────────────────────

    def _geography_column(self, value: str) -> tuple[Table, Column] | None:
        for t in self._tables:
            for col in t.columns.values():
                if col.get("ce:geographyLevel") == value:
                    return t, col
        return None

    def _counts(self, table: str, column: str, year: int) -> dict[str, int] | None:
        """Unweighted rows per value over the year's set; None with no data."""
        if not self.catalog.has("raw", table):
            return None
        source = self.catalog.source("raw", table)
        rows = self.catalog.rows(
            f"""
            select coalesce({_quote(column)}::varchar, '') as code, count(*) as n
            from {source}
            where {survey_year_filter(SURVEY, year)}
            group by 1
            """
        )
        return {r["code"]: r["n"] for r in rows} or None

    def _crosswalk(self, level: str, year: int) -> list[dict] | None:
        """The crosswalk's rows for one level and year; None until it is built."""
        if not self.catalog.has("ref", "geo_crosswalk"):
            return None
        source = self.catalog.source("ref", "geo_crosswalk")
        rows = self.catalog.rows(
            f"""
            select code, label, parent_state, parent_division, parent_region,
                   first_year, last_year, note
            from {source} where level = ? order by code
            """,
            [level],
        )
        return [r for r in rows if covers(r["first_year"], r["last_year"], year)]

    @_cached
    def list_geographies(
        self, level: GeoLevel, year: int | None = None
    ) -> GeographyList:
        """The codes of one geography level, and how far the design supports it."""
        if level not in GEOGRAPHY_VALUE:
            raise CeMetaError(
                f"Unknown geography level {level!r}.", list(GEOGRAPHY_VALUE)
            )
        year = self._check_year(year)
        value = GEOGRAPHY_VALUE[level]
        levels = (self._fact("ce:geographyLevels") or Fact(property="", value={})).value
        support = (levels or {}).get(value)
        found = self._geography_column(value)
        crosswalk = self._crosswalk(level, year)
        table, column = found if found else (None, None)

        notices = self.notices(table.name if table else None)
        if found is None:
            notices.append(
                Notice(
                    scope="table",
                    text=(
                        f"No sidecar column is marked with the {level} level "
                        "(ce:geographyLevel), so the column to group by is not "
                        "given and interviews are not counted."
                    ),
                )
            )
        if crosswalk is None:
            notices += self._not_built("ref", "geo_crosswalk", "Parent geographies")

        counts = None
        if found is not None:
            counts = self._counts(table.name, column.name, year)
            if counts is None:
                notices.append(
                    Notice(
                        scope="build",
                        text=f"No {table.name} rows are loaded for survey year "
                        f"{year}, so interview counts are not given.",
                    )
                )

        def interviews(code: str) -> int | None:
            return None if counts is None else counts.get(code, 0)

        if crosswalk:
            entries = [
                GeographyEntry(
                    code=r["code"],
                    label=_clip(r["label"], LABEL_CHARS),
                    parent_state=r["parent_state"],
                    parent_division=r["parent_division"],
                    parent_region=r["parent_region"],
                    interviews=interviews(r["code"]),
                    note=r["note"],
                )
                for r in crosswalk
            ]
        elif found is not None:
            entries = [
                GeographyEntry(
                    code=v, label=_clip(label, LABEL_CHARS), interviews=interviews(v)
                )
                for v, label in column.codes
            ]
        else:
            entries = []

        known = {e.code for e in entries}
        unlisted = (
            None
            if counts is None
            else sum(n for code, n in counts.items() if not _same_code(code, known))
        )
        if not entries:
            summary = f"The {level} level is rated {support}, but no column carries it."
        else:
            where = f", in {table.name}.{column.name}" if found else ""
            summary = (
                f"{len(entries)} {level} codes{where}. "
                f"The design's support for this level is {support}."
            )
        if unlisted:
            summary += f" {unlisted} interviews carry no listed code."
        return GeographyList(
            level=level,
            year=year,
            table=table.name if table else None,
            column=column.name if column else None,
            support=support,
            entries=entries,
            unlisted_interviews=unlisted,
            notices=notices,
            summary=summary,
        )

    # ── dictionary ───────────────────────────────────────────────────────────

    @_cached
    def dictionary(self, year: int | None = None) -> dict[str, list[VariableMatch]]:
        """The variables of one year, by table.

        The release year comes from the sidecars. Any other year the
        dictionary reaches comes from the dictionary tables, once built.
        """
        at = self._check_year(year)
        history = self._history()
        if at == self.release:
            out: dict[str, list[VariableMatch]] = {}
            for t in self._tables:
                out[t.name] = []
                for c in t.columns.values():
                    match = self._match(t, c, [])
                    row = history.covering(t.name, c.name, at) if history else None
                    if row is not None:
                        match = match.model_copy(
                            update={
                                "first_year": row.first_year,
                                "last_year": row.last_year,
                            }
                        )
                    out[t.name].append(match)
            return out
        if history is None:
            raise CeMetaError(
                f"Only the {self.release} release can be listed until the "
                "dictionary tables are built (`ce-meta build`).",
                [str(self.release)],
            )
        listing: dict[str, list[VariableMatch]] = {}
        for table, name in sorted(
            history.keys(), key=lambda k: (names.table_rank(k[0]), k[1])
        ):
            row = history.covering(table, name, at)
            if row is None:
                continue
            column = self.sidecars.column(table, name)
            sidecar_table = self.sidecars.table(table)
            match = (
                self._match(sidecar_table, column, []).model_copy(
                    update={"first_year": row.first_year, "last_year": row.last_year}
                )
                if column is not None and sidecar_table is not None
                else self._dictionary_match(row, [])
            )
            listing.setdefault(table, []).append(match)
        return listing


def imputed_set(table: Table, column: Column) -> ImputedSet | None:
    """The imputation set a column belongs to: its base, versions and mean.

    Public for request capture (Plan 4), whose derivation resolves a named
    imputed version to the averaged column.
    """
    return MetadataService._imputed_set(table, column)
