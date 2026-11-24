"""The one reader of the CE Interview sidecar set, and its validator.

A sidecar set is three kinds of YAML file in one folder: the vocabulary
(`vocabulary.yaml`), the master sidecar (`ce-interview-master.odcs.yaml`) and
one table sidecar per table (`<sidecar id>.odcs.yaml`). `load_set` reads them
into plain objects, so a caller can ask for a column's facts without knowing how
ODCS stores custom properties. `validate` runs the set checks of the Sidecar
Plan and returns a report in which every failure has a scope: `set` (no sidecar
fact can be trusted) or `table` (one table is affected, the others stay usable).

Every package that needs a sidecar fact reads it here. The module depends only
on `pyyaml`; `jsonschema` is used for the schema checks when it is installed,
and those checks are reported as skipped when it is not.
"""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

VOCABULARY_FILE = "vocabulary.yaml"
VOCABULARY_SCHEMA_FILE = "vocabulary.schema.json"
MASTER_FILE = "ce-interview-master.odcs.yaml"
TABLE_SUFFIX = ".odcs.yaml"
TABLE_ID_PREFIX = "ce-interview-"

VERIFY = "VERIFY"  # a value still under review: not a failure, reported apart
NEEDS_REVIEW = "needs-review"

Scope = Literal["set", "table"]
Status = Literal["passed", "failed", "skipped"]

_Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def custom_properties(node: dict | None) -> dict[str, Any]:
    """An ODCS `customProperties` list as a lookup, keyed by property name."""
    return {
        cp["property"]: cp["value"] for cp in (node or {}).get("customProperties") or []
    }


# ── the set ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Vocabulary:
    id: str
    version: str
    properties: dict[str, dict]
    doc: dict = field(repr=False)

    def entry(self, prop: str) -> dict | None:
        return self.properties.get(prop)

    def implies(self, prop: str) -> str | None:
        entry = self.properties.get(prop)
        return " ".join(entry["implies"].split()) if entry else None


@dataclass(frozen=True)
class Column:
    name: str
    business_name: str | None
    logical_type: str | None
    description: str | None
    facts: dict[str, Any]
    tags: tuple[str, ...]
    codes: tuple[tuple[str, str | None], ...]  # (value, label), from `enum`

    @property
    def needs_review(self) -> bool:
        return NEEDS_REVIEW in self.tags

    @property
    def unverified(self) -> tuple[str, ...]:
        """The properties of this column whose value is still `VERIFY`."""
        return tuple(p for p, v in self.facts.items() if has_verify(v))

    def get(self, prop: str, default: Any = None) -> Any:
        return self.facts.get(prop, default)


@dataclass(frozen=True)
class Table:
    id: str  # the sidecar id, e.g. ce-interview-fmli
    name: str  # the stored table, e.g. fmli
    version: str
    facts: dict[str, Any]  # ce:master, ce:rowRepresents, ce:rowKey
    columns: dict[str, Column]
    doc: dict = field(repr=False)

    @property
    def row_key(self) -> list[str]:
        return list(self.facts.get("ce:rowKey") or [])

    def column(self, name: str) -> Column | None:
        """A column by its stored name or its BLS spelling, in any casing."""
        found = self.columns.get(name) or self.columns.get(name.lower())
        if found:
            return found
        wanted = name.upper()
        for col in self.columns.values():
            if str(col.facts.get("ce:sourceName", "")).upper() == wanted:
                return col
        return None


@dataclass(frozen=True)
class Master:
    id: str
    version: str
    facts: dict[str, Any]
    doc: dict = field(repr=False)

    @property
    def tables(self) -> list[dict]:
        return list(self.facts.get("ce:tables") or [])

    @property
    def relationships(self) -> list[dict]:
        return list(self.facts.get("ce:relationships") or [])


@dataclass(frozen=True)
class Problem:
    """A file that could not be read into the set."""

    file: str
    detail: str


@dataclass(frozen=True)
class SidecarSet:
    folder: Path
    vocabulary: Vocabulary | None
    master: Master | None
    tables: dict[str, Table]  # keyed by sidecar id
    problems: tuple[Problem, ...] = ()

    def table(self, name: str) -> Table | None:
        """A table by its sidecar id (`ce-interview-fmli`) or stored name (`fmli`)."""
        return self.tables.get(name) or self.tables.get(TABLE_ID_PREFIX + name)

    def column(self, table: str, name: str) -> Column | None:
        found = self.table(table)
        return found.column(name) if found else None

    def facts_with_implies(
        self, table: str, name: str
    ) -> list[tuple[str, Any, str | None]]:
        """A column's `ce:` facts, each with what the vocabulary says follows."""
        col = self.column(table, name)
        if col is None:
            return []
        vocab = self.vocabulary
        return [
            (p, v, vocab.implies(p) if vocab else None) for p, v in col.facts.items()
        ]


def _read_yaml(path: Path) -> dict:
    doc = yaml.load(path.read_text(), Loader=_Loader)  # noqa: S506 - a safe loader
    if not isinstance(doc, dict):
        raise ValueError("not a YAML mapping")
    return doc


def _column(entry: dict) -> Column:
    return Column(
        name=entry["name"],
        business_name=entry.get("businessName"),
        logical_type=entry.get("logicalType"),
        description=entry.get("description"),
        facts=custom_properties(entry),
        tags=tuple(entry.get("tags") or ()),
        codes=tuple((str(e["value"]), e.get("label")) for e in entry.get("enum") or ()),
    )


def _table(doc: dict) -> Table:
    schema = (doc.get("schema") or [{}])[0]
    facts = {**custom_properties(doc), **custom_properties(schema)}
    columns = {c["name"]: _column(c) for c in schema.get("properties") or []}
    name = schema.get("name") or str(doc["id"]).removeprefix(TABLE_ID_PREFIX)
    return Table(str(doc["id"]), name, str(doc.get("version")), facts, columns, doc)


def load_set(folder: Path) -> SidecarSet:
    """Read every sidecar file in `folder`. A file that cannot be read is listed
    in `problems` and left out, so the rest of the set is still usable."""
    folder = Path(folder)
    problems: list[Problem] = []

    def read(name: str) -> dict | None:
        path = folder / name
        if not path.exists():
            problems.append(Problem(name, "file not found"))
            return None
        try:
            return _read_yaml(path)
        except (yaml.YAMLError, ValueError) as exc:
            problems.append(Problem(name, f"cannot be read: {exc}"))
            return None

    vocab_doc = read(VOCABULARY_FILE)
    vocabulary = None
    if vocab_doc is not None:
        vocabulary = Vocabulary(
            str(vocab_doc.get("id")),
            str(vocab_doc.get("version")),
            dict(vocab_doc.get("properties") or {}),
            vocab_doc,
        )

    master_doc = read(MASTER_FILE)
    master = None
    if master_doc is not None:
        master = Master(
            str(master_doc.get("id")),
            str(master_doc.get("version")),
            custom_properties(master_doc),
            master_doc,
        )

    tables: dict[str, Table] = {}
    for path in sorted(folder.glob("*" + TABLE_SUFFIX)):
        if path.name == MASTER_FILE:
            continue
        doc = read(path.name)
        if doc is None:
            continue
        try:
            table = _table(doc)
        except (KeyError, TypeError, IndexError) as exc:
            problems.append(Problem(path.name, f"not a table sidecar: {exc!r}"))
            continue
        tables[table.id] = table
    return SidecarSet(folder, vocabulary, master, tables, tuple(problems))


# ── the report ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Failure:
    scope: Scope
    table: str | None  # the sidecar id, for a table-level failure
    detail: str


@dataclass(frozen=True)
class CheckResult:
    check: str
    description: str
    status: Status
    failures: tuple[Failure, ...] = ()
    note: str | None = None


@dataclass(frozen=True)
class Unverified:
    table: str | None  # None for the master
    column: str | None
    property: str


@dataclass(frozen=True)
class Report:
    checks: tuple[CheckResult, ...]
    unverified: tuple[Unverified, ...] = ()

    @property
    def failures(self) -> list[Failure]:
        return [f for c in self.checks for f in c.failures]

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def set_failures(self) -> list[Failure]:
        return [f for f in self.failures if f.scope == "set"]

    def table_failures(self, table: str) -> list[Failure]:
        ids = {table, TABLE_ID_PREFIX + table}
        return [f for f in self.failures if f.scope == "table" and f.table in ids]

    def usable(self, table: str) -> bool:
        """Whether a request may rely on this table's sidecar facts."""
        return not self.set_failures and not self.table_failures(table)

    def lines(self) -> Iterator[str]:
        for c in self.checks:
            suffix = f" ({c.note})" if c.note else ""
            yield f"{c.status:<8}{c.check}: {c.description}{suffix}"
            for f in c.failures:
                where = "set" if f.scope == "set" else f"table {f.table}"
                yield f"          [{where}] {f.detail}"
        if self.unverified:
            n = len(self.unverified)
            yield f"{n} values are still VERIFY (under review, not failures)"


# ── value checks against the vocabulary ──────────────────────────────────────

_FIELD_LIST = re.compile(r"(\w+):\s*(?:one of, or a list of,\s*)?\[([^\]]+)\]")


def has_verify(value: Any) -> bool:
    if isinstance(value, dict):
        return any(has_verify(v) for v in value.values())
    if isinstance(value, list):
        return any(has_verify(v) for v in value)
    return value == VERIFY


def _members(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _not_in(allowed: list, value: Any) -> list[Any]:
    return [v for v in _members(value) if v != VERIFY and v not in allowed]


def _described_lists(text: str) -> dict[str, list[str]]:
    """Closed lists named inside a value description, e.g. `kind: [a, b]`."""
    found: dict[str, list[str]] = {}
    for name, body in _FIELD_LIST.findall(text):
        items = [v.strip() for v in body.split(",")]
        if items != [".."]:
            found[name] = items
    return found


def _dicts(value: Any) -> list[dict]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


def value_problems(spec: Any, value: Any) -> list[str]:
    """Why `value` is not an allowed value of a property whose vocabulary
    entry gives `spec`. An empty list means it is allowed, or cannot be told."""
    if isinstance(spec, list):  # a closed list
        bad = _not_in(spec, value)
        return [f"{bad} not in {spec}"] if bad else []
    if isinstance(spec, dict):  # a field map
        if not isinstance(value, dict):
            return [f"expected fields {sorted(spec)}, got {value!r}"]
        problems = [f"unknown field {k!r}" for k in value if k not in spec]
        for name, allowed in spec.items():
            if isinstance(allowed, list) and name in value:
                bad = _not_in(allowed, value[name])
                if bad:
                    problems.append(f"{name}: {bad} not in {allowed}")
        return problems
    lists = _described_lists(str(spec))
    problems = []
    rows = _dicts(value)
    named = {name for name in lists if any(name in row for row in rows)}
    for name in sorted(named):
        for row in rows:
            if name in row:
                bad = _not_in(lists[name], row[name])
                if bad:
                    problems.append(f"{name}: {bad} not in {lists[name]}")
    if not named and len(lists) == 1 and isinstance(value, dict):
        # `{code: [..]}`: any key, each value from the one list
        (allowed,) = lists.values()
        for key, item in value.items():
            bad = _not_in(allowed, item)
            if bad:
                problems.append(f"{key}: {bad} not in {allowed}")
    return problems


# ── the checks ───────────────────────────────────────────────────────────────


def _jsonschema():
    try:
        import jsonschema
    except ImportError:  # the server image does not carry it
        return None
    return jsonschema


def _schema_errors(schema_path: Path, doc: dict) -> list[str] | None:
    """Schema errors, or None when the schema or `jsonschema` is not available."""
    js = _jsonschema()
    if js is None or not schema_path.exists():
        return None
    validator = js.Draft202012Validator(json.loads(schema_path.read_text()))
    return [
        f"{'/'.join(str(p) for p in e.absolute_path) or '(top)'}: {e.message}"
        for e in validator.iter_errors(doc)
    ]


def _result(check: str, description: str, failures: list[Failure]) -> CheckResult:
    return CheckResult(
        check, description, "failed" if failures else "passed", tuple(failures)
    )


def _skipped(check: str, description: str, note: str) -> CheckResult:
    return CheckResult(check, description, "skipped", note=note)


def _check_odcs(s: SidecarSet, odcs_schema: Path | None) -> CheckResult:
    name, text = "odcs_schema", "each sidecar conforms to the ODCS v3.2.0 schema"
    if odcs_schema is None or _jsonschema() is None:
        return _skipped(name, text, "no ODCS schema file given")
    failures = []
    if s.master is not None:
        for err in _schema_errors(odcs_schema, s.master.doc) or []:
            failures.append(Failure("set", None, f"{MASTER_FILE}: {err}"))
    for table in s.tables.values():
        for err in _schema_errors(odcs_schema, table.doc) or []:
            failures.append(Failure("table", table.id, err))
    return _result(name, text, failures)


def _check_vocabulary_schema(s: SidecarSet) -> CheckResult:
    name, text = "vocabulary_schema", "the vocabulary conforms to its JSON Schema"
    if s.vocabulary is None:
        return _result(name, text, [Failure("set", None, "no vocabulary to check")])
    errors = _schema_errors(s.folder / VOCABULARY_SCHEMA_FILE, s.vocabulary.doc)
    if errors is None:
        return _skipped(name, text, "schema file or jsonschema not available")
    return _result(name, text, [Failure("set", None, e) for e in errors])


def _property_failures(
    s: SidecarSet,
    facts: dict[str, Any],
    level: str,
    scope: Scope,
    table: str | None,
    where: str,
    column: str | None,
    unverified: list[Unverified],
) -> list[Failure]:
    failures = []
    properties = s.vocabulary.properties if s.vocabulary else {}
    for prop, value in facts.items():
        entry = properties.get(prop)
        if entry is None:
            failures.append(
                Failure(scope, table, f"{where}: {prop} is not in the vocabulary")
            )
            continue
        if entry.get("level") != level:
            failures.append(
                Failure(
                    scope,
                    table,
                    f"{where}: {prop} is a {entry.get('level')}-level property",
                )
            )
        if has_verify(value):
            unverified.append(Unverified(table, column, prop))
        for problem in value_problems(entry.get("values"), value):
            failures.append(Failure(scope, table, f"{where}: {prop}: {problem}"))
    return failures


def _check_properties(s: SidecarSet, unverified: list[Unverified]) -> CheckResult:
    name = "properties_defined"
    text = "every ce: property is defined in the vocabulary, with an allowed value"
    if s.vocabulary is None:
        return _result(
            name, text, [Failure("set", None, "no vocabulary to check against")]
        )
    failures = []
    if s.master is not None:
        failures += _property_failures(
            s, s.master.facts, "survey", "set", None, "master", None, unverified
        )
    for table in s.tables.values():
        failures += _property_failures(
            s, table.facts, "table", "table", table.id, "table", None, unverified
        )
        for col in table.columns.values():
            failures += _property_failures(
                s,
                col.facts,
                "column",
                "table",
                table.id,
                col.name,
                col.name,
                unverified,
            )
    return _result(name, text, failures)


def _check_tables_listed(s: SidecarSet) -> CheckResult:
    name = "tables_listed"
    text = "every table the master lists has a sidecar that points back to it"
    if s.master is None:
        return _result(name, text, [Failure("set", None, "no master sidecar")])
    failures = []
    listed = {t.get("sidecar") for t in s.master.tables}
    for table_id in sorted(t for t in listed if t):
        table = s.tables.get(table_id)
        if table is None:
            failures.append(
                Failure("table", table_id, "listed in the master, no sidecar file")
            )
        elif (table.facts.get("ce:master") or {}).get("id") != s.master.id:
            failures.append(
                Failure("table", table_id, f"ce:master does not name {s.master.id}")
            )
    for table_id in sorted(set(s.tables) - listed):
        failures.append(
            Failure("table", table_id, "has a sidecar, not listed in the master")
        )
    return _result(name, text, failures)


def _check_versions(s: SidecarSet) -> CheckResult:
    name, text = "versions_agree", "the versions named across the files agree"
    failures = []
    if s.master is None or s.vocabulary is None:
        return _result(
            name, text, [Failure("set", None, "master or vocabulary missing")]
        )
    pointer = s.master.facts.get("ce:vocabulary") or {}
    actual = {"id": s.vocabulary.id, "version": s.vocabulary.version}
    if {k: str(v) for k, v in pointer.items()} != actual:
        failures.append(
            Failure(
                "set", None, f"master names vocabulary {pointer}, the file is {actual}"
            )
        )
    listed = {t.get("sidecar"): str(t.get("version")) for t in s.master.tables}
    for table in s.tables.values():
        named = (table.facts.get("ce:master") or {}).get("version")
        if named is not None and str(named) != s.master.version:
            failures.append(
                Failure(
                    "table",
                    table.id,
                    f"written against master {named}, the master is {s.master.version}",
                )
            )
        if table.id in listed and listed[table.id] != table.version:
            failures.append(
                Failure(
                    "table",
                    table.id,
                    f"the master lists version {listed[table.id]}, "
                    f"the sidecar is {table.version}",
                )
            )
    return _result(name, text, failures)


def _check_relationship_keys(s: SidecarSet) -> CheckResult:
    name = "relationship_keys"
    text = "every relationship key exists in its table and is an identifier"
    failures = []
    for rel in s.master.relationships if s.master else []:
        for side in ("parent", "child"):
            table = s.tables.get(rel.get(side))
            if table is None:  # reported by tables_listed
                continue
            for key in (rel.get("keys") or {}).get(side) or []:
                col = table.columns.get(key)
                if col is None:
                    detail = f"relationship key {key} is not a column"
                elif col.facts.get("ce:role") != "identifier":
                    detail = f"relationship key {key} is not marked as an identifier"
                else:
                    continue
                if not any(
                    f.table == table.id and f.detail == detail for f in failures
                ):
                    failures.append(Failure("table", table.id, detail))
    return _result(name, text, failures)


def _check_weight_route(s: SidecarSet) -> CheckResult:
    name = "weight_route"
    text = "every table reaches a weight through weightFrom: parent links"
    if s.master is None:
        return _result(name, text, [Failure("set", None, "no master sidecar")])
    reach = {t.get("sidecar") for t in s.master.tables if t.get("carriesWeight")}
    routes = [r for r in s.master.relationships if r.get("weightFrom") == "parent"]
    changed = True
    while changed:
        changed = False
        for r in routes:
            if r.get("parent") in reach and r.get("child") not in reach:
                reach.add(r.get("child"))
                changed = True
    listed = [t.get("sidecar") for t in s.master.tables]
    failures = [
        Failure("table", t, "no route to a weight") for t in listed if t not in reach
    ]
    return _result(name, text, failures)


def _check_flags(s: SidecarSet) -> CheckResult:
    name, text = "flag_columns", "every flag names a column in the same table"
    failures = []
    for table in s.tables.values():
        for col in table.columns.values():
            target = col.facts.get("ce:flagFor")
            if target is not None and target != VERIFY and target not in table.columns:
                failures.append(
                    Failure(
                        "table",
                        table.id,
                        f"{col.name}: ce:flagFor names {target}, not a column",
                    )
                )
    return _result(name, text, failures)


def _check_calendar_pairs(s: SidecarSet) -> CheckResult:
    name = "calendar_pairs"
    text = "every ce:calendarPair names a column that names it back"
    failures = []
    for table in s.tables.values():
        for col in table.columns.values():
            target = col.facts.get("ce:calendarPair")
            if target is None or target == VERIFY:
                continue
            partner = table.columns.get(target)
            if partner is None:
                detail = f"{col.name}: ce:calendarPair names {target}, not a column"
            elif partner.facts.get("ce:calendarPair") != col.name:
                detail = (
                    f"{col.name}: ce:calendarPair names {target}, "
                    "which does not name it back"
                )
            else:
                continue
            failures.append(Failure("table", table.id, detail))
    return _result(name, text, failures)


def _check_row_keys(s: SidecarSet) -> CheckResult:
    name, text = "row_key", "every ce:rowKey names columns that exist in its sidecar"
    failures = []
    for table in s.tables.values():
        if not table.row_key:
            failures.append(Failure("table", table.id, "no ce:rowKey"))
        for key in table.row_key:
            if key not in table.columns:
                failures.append(
                    Failure("table", table.id, f"ce:rowKey names {key}, not a column")
                )
    return CheckResult(
        name,
        text,
        "failed" if failures else "passed",
        tuple(failures),
        note="uniqueness in the data is checked when a sidecar is generated",
    )


def validate(sidecar_set: SidecarSet, odcs_schema: Path | None = None) -> Report:
    """Run the set checks. Nothing is raised: every finding is in the report."""
    s = sidecar_set
    unverified: list[Unverified] = []
    load = [
        Failure(
            "set" if p.file in (VOCABULARY_FILE, MASTER_FILE) else "table",
            None
            if p.file in (VOCABULARY_FILE, MASTER_FILE)
            else p.file.removesuffix(TABLE_SUFFIX),
            f"{p.file}: {p.detail}",
        )
        for p in s.problems
    ]
    checks = [
        _result("files_readable", "every sidecar file can be read", load),
        _check_odcs(s, odcs_schema),
        _check_vocabulary_schema(s),
        _check_properties(s, unverified),
        _check_tables_listed(s),
        _check_versions(s),
        _check_relationship_keys(s),
        _check_weight_route(s),
        _check_flags(s),
        _check_calendar_pairs(s),
        _check_row_keys(s),
    ]
    return Report(tuple(checks), tuple(unverified))
