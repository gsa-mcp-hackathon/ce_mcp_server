"""The capture check: form and references, never method (D2).

One function, `check_record`, called at capture and again as the first step
of rule validation (Plan 5). It returns errors, which send the record back to
the model, flags, which do not, and the questions a record needs answered
before it can be resolved. It reads only the stored record and the context,
so a stored record can be checked again under new sidecars.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import ValidationError

from ce_capture.context import MEMBER_ROWS, CaptureContext
from ce_capture.normalize import SET_COMPARISONS, code_for
from ce_capture.periods import period_years
from ce_capture.records import STATED_FIELDS, RequestRecord
from ce_capture.rules import load_phrases
from ce_capture.text import locate
from ce_meta.names import closest_names

GAP_YEAR = 2025
GAP_QUESTION = "As released, or adjusted to match the published figures?"
COMBINE_KEYS = {
    "ratio": ("numerator", "denominator"),
    "difference": ("first", "second"),
}
NEEDS_MEASURE = frozenset({"mean", "total"})


@dataclass
class CheckOutcome:
    errors: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    spans: list[dict] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    missing_gap_choice: bool = False

    @property
    def passed(self) -> bool:
        return not self.errors

    def result(self) -> dict:
        return {"passed": self.passed, "errors": self.errors, "flags": self.flags}

    def error(self, part: str, message: str, node=None, field_=None, suggestions=None):
        self.errors.append(
            {
                "part": part,
                "node": node,
                "field": field_,
                "message": message,
                "suggestions": list(suggestions or []),
            }
        )


def form_errors(exc: ValidationError, prefix: str = "") -> list[dict]:
    """A pydantic validation error as check errors of the form part."""
    out = []
    for e in exc.errors(include_url=False):
        where = ".".join(str(p) for p in e["loc"])
        out.append(
            {
                "part": "form",
                "node": None,
                "field": f"{prefix}{where}" if where else prefix or None,
                "message": e["msg"],
                "suggestions": [],
            }
        )
    return out


def base_table(node: dict, ctx: CaptureContext) -> str:
    """The table a node's rows come from: the measure's, or for a count the
    weight table (the member table for a count of members)."""
    if node.get("measure"):
        return node["measure"].get("table", "")
    unit = ((node.get("stated") or {}).get("count_unit") or {}).get("value")
    if unit == "member":
        for name in ctx.covered_tables:
            if ctx.row_represents(name) == MEMBER_ROWS:
                return name
    return ctx.weight[0]


def column_refs(node: dict) -> list[tuple[str, dict]]:
    """(field, reference) for every column a node names."""
    refs = []
    if node.get("measure"):
        refs.append(("measure", node["measure"]))
    refs += [(f"group_by.{i}", r) for i, r in enumerate(node.get("group_by") or [])]
    refs += [(f"filters.{i}", r) for i, r in enumerate(node.get("filters") or [])]
    return refs


# ── the parts ────────────────────────────────────────────────────────────────


def _references(node: dict, ctx: CaptureContext, out: CheckOutcome) -> None:
    nid = node.get("id")
    for where, ref in column_refs(node):
        name = str(ref.get("table", ""))
        table = ctx.table(name)
        if table is None or table.name not in ctx.listed:
            out.error(
                "references",
                f"No covered table named {name!r}.",
                nid,
                f"{where}.table",
                closest_names(name, ctx.covered_tables),
            )
            continue
        if not ctx.covered(table.name):
            out.error(
                "references",
                f"The {table.name} sidecar failed a check, so capture does not "
                "cover the table.",
                nid,
                f"{where}.table",
            )
            continue
        column = table.column(str(ref.get("column", "")))
        if column is None:
            out.error(
                "references",
                f"{table.name} has no column {ref.get('column')!r}.",
                nid,
                f"{where}.column",
                closest_names(str(ref.get("column", "")), table.columns),
            )
            continue
        if not where.startswith("filters"):
            continue
        _filter(ref, where, nid, column.codes, out)


def _filter(flt: dict, where: str, nid, codes, out: CheckOutcome) -> None:
    comparison, value = flt.get("comparison"), flt.get("value")
    many = comparison in ("in", "not_in", "between")
    if many != isinstance(value, list):
        shape = "a list" if many else "one value"
        out.error("references", f"{comparison} takes {shape}.", nid, f"{where}.value")
        return
    if comparison == "between" and len(value) != 2:
        out.error("references", "between takes two values.", nid, f"{where}.value")
        return
    if not codes or comparison not in SET_COMPARISONS:
        return
    for item in value if many else [value]:
        code, rule = code_for(item, codes)
        if code is not None and rule == "":
            continue
        shown = [f"{v} ({label})" if label else v for v, label in codes[:20]]
        more = (
            f" It has {len(codes)} codes; find one with describe_variable and "
            "code_query."
            if len(codes) > 20
            else ""
        )
        out.error(
            "references",
            f"{item!r} is not a code of {flt.get('table')}.{flt.get('column')}.{more}",
            nid,
            f"{where}.value",
            shown,
        )


def _reach(node: dict, ctx: CaptureContext, out: CheckOutcome) -> None:
    nid = node.get("id")
    base = base_table(node, ctx)
    weight_table = ctx.weight[0]
    tables = {base} | {str(r.get("table")) for _, r in column_refs(node)}
    for name in sorted(t for t in tables if ctx.covered(t)):
        if not ctx.reaches_weight(name):
            out.error(
                "table_reach",
                f"{name} has no route to the weight in the master's relationships.",
                nid,
            )
    for i, ref in enumerate(node.get("group_by") or []):
        if ref.get("table") not in (base, weight_table):
            out.error(
                "table_reach",
                f"A grouping column must be on {base} or {weight_table}; "
                f"{ref.get('table')}.{ref.get('column')} is not (D29).",
                nid,
                f"group_by.{i}",
            )


def _consistency(record: dict, out: CheckOutcome) -> None:
    nodes = record.get("nodes") or []
    ids = [n.get("id") for n in nodes]
    for nid in sorted({i for i in ids if ids.count(i) > 1}):
        out.error("consistency", f"Two nodes have the ID {nid!r}.", nid)
    known = set(ids)
    if not nodes and not (record.get("capture") or {}).get("reason_code"):
        if not record.get("unresolved"):
            out.error(
                "consistency",
                "The record has no nodes and no reason code. Write the nodes, or "
                "give a reason_code when the message cannot be captured.",
            )
    for node in nodes:
        nid = node.get("id")
        if node.get("kind") == "estimate":
            if node.get("statistic") in NEEDS_MEASURE and not node.get("measure"):
                out.error(
                    "consistency",
                    f"A {node.get('statistic')} needs a measure.",
                    nid,
                    "measure",
                )
            period = node.get("period") or {}
            if period.get("kind") != "not_stated" and not period.get("values"):
                out.error(
                    "consistency",
                    "The period has no values; give them, or kind not_stated.",
                    nid,
                    "period",
                )
            continue
        keys = COMBINE_KEYS.get(node.get("statistic"), ())
        inputs = node.get("inputs") or {}
        given = {k for k, v in inputs.items() if v}
        if given != set(keys):
            out.error(
                "consistency",
                f"A {node.get('statistic')} takes the inputs {' and '.join(keys)}.",
                nid,
                "inputs",
            )
        for key in keys:
            target = inputs.get(key)
            if target and (target not in known or target == nid):
                out.error(
                    "consistency",
                    f"Input {key} names {target!r}, which is not another node.",
                    nid,
                    f"inputs.{key}",
                    sorted(known - {nid}),
                )
    if _has_cycle(nodes):
        out.error("consistency", "Combine nodes refer to each other in a cycle.")
    for i, answer in enumerate(record.get("answers") or []):
        if answer.get("node") not in known:
            out.error(
                "consistency",
                f"Answer {i} names {answer.get('node')!r}, which is not a node.",
                None,
                f"answers.{i}.node",
                sorted(known),
            )


def _has_cycle(nodes: list[dict]) -> bool:
    edges = {
        n.get("id"): [v for v in (n.get("inputs") or {}).values() if v]
        for n in nodes
        if n.get("kind") == "combine"
    }
    state: dict[str, int] = {}

    def visit(nid: str) -> bool:
        if state.get(nid) == 1:
            return True
        if state.get(nid) == 2:
            return False
        state[nid] = 1
        if any(visit(t) for t in edges.get(nid, [])):
            return True
        state[nid] = 2
        return False

    return any(visit(n) for n in edges)


def quoted(record: dict) -> list[tuple[str | None, str, str]]:
    """(node, where, phrase) for every phrase the model quoted from the user."""
    out: list[tuple[str | None, str, str]] = []
    for node in record.get("nodes") or []:
        nid = node.get("id")
        text = (node.get("period") or {}).get("text")
        if text:
            out.append((nid, f"{nid}.period.text", text))
        for name, choice in (node.get("stated") or {}).items():
            if choice and choice.get("evidence") is not None:
                out.append((nid, f"{nid}.stated.{name}", choice["evidence"]))
    for i, a in enumerate(record.get("answers") or []):
        out.append((a.get("node"), f"answers.{i}.asks", a.get("asks", "")))
    for i, a in enumerate(record.get("ambiguities") or []):
        out.append((None, f"ambiguities.{i}.phrase", a.get("phrase", "")))
    for i, u in enumerate(record.get("unresolved") or []):
        out.append((None, f"unresolved.{i}.phrase", u.get("phrase", "")))
    return out


def _evidence(record: dict, out: CheckOutcome) -> None:
    source = record.get("source") or {}
    texts = [
        ("user_text", source.get("user_text") or ""),
        ("earlier_text", source.get("earlier_text") or ""),
    ]
    for nid, where, phrase in quoted(record):
        for name, text in texts:
            span = locate(phrase, text) if text else None
            if span:
                start, end = span
                out.spans.append(
                    {
                        "where": where,
                        "text": text[start:end],
                        "source": name,
                        "start": start,
                        "end": end,
                    }
                )
                break
        else:
            out.error(
                "evidence",
                f"{phrase!r} is not in the user's text. Quote the user's own words.",
                nid,
                where,
            )


def _stated_choices(record: dict, out: CheckOutcome) -> None:
    """A choice the user stated must be on some node. With no estimate node
    (nothing matched, or not a statistics request) there is nowhere to put it."""
    if not any(n.get("kind") == "estimate" for n in record.get("nodes") or []):
        return
    user_text = (record.get("source") or {}).get("user_text") or ""
    stated = {
        name
        for node in record.get("nodes") or []
        for name, choice in (node.get("stated") or {}).items()
        if choice
    }
    for name, phrases in load_phrases().required_choices(user_text).items():
        if name in STATED_FIELDS and name not in stated:
            out.error(
                "stated_choices",
                f"The text says {', '.join(repr(p) for p in phrases)}, but no node "
                f"states {name}.",
                None,
                f"stated.{name}",
            )


def _required_choices(record: dict, ctx: CaptureContext, out: CheckOutcome) -> None:
    """A record whose period includes 2025 states the 2025 choice (D23)."""
    estimates = [n for n in record.get("nodes") or [] if n.get("kind") == "estimate"]
    in_gap = [
        n for n in estimates if GAP_YEAR in period_years(n.get("period"), ctx.release)
    ]
    stated = any((n.get("stated") or {}).get("gap_2025") for n in estimates)
    if in_gap and not stated:
        out.missing_gap_choice = True
        out.questions.append(GAP_QUESTION)


def _flags(record: dict, ctx: CaptureContext, out: CheckOutcome) -> None:
    not_loaded: list[str] = []
    review: list[str] = []
    for node in record.get("nodes") or []:
        if node.get("kind") != "estimate":
            continue
        for year in period_years(node.get("period"), ctx.release):
            if not ctx.year_loaded(year):
                not_loaded.append(f"{node.get('id')} {year}")
        for _, ref in column_refs(node):
            column = ctx.column(str(ref.get("table")), str(ref.get("column")))
            name = f"{ref.get('table')}.{ref.get('column')}"
            if column is not None and column.needs_review and name not in review:
                review.append(name)
    if not_loaded:
        out.flags.append("period_not_loaded: " + ", ".join(not_loaded))
    if ctx.report.set_failures:
        detail = "; ".join(f.detail for f in ctx.report.set_failures)
        out.flags.append(f"sidecar_set_invalid: {detail}")
    out.flags += [f"needs_review: {name}" for name in review]


def check_record(
    record: dict, ctx: CaptureContext, flags: list[str] | None = None
) -> CheckOutcome:
    """Check a request record. `flags` are those normalize raised."""
    out = CheckOutcome()
    try:
        RequestRecord.model_validate(record)
    except ValidationError as exc:
        out.errors += form_errors(exc)
        return out
    for node in record.get("nodes") or []:
        if node.get("kind") == "estimate":
            _references(node, ctx, out)
            _reach(node, ctx, out)
    _consistency(record, out)
    _evidence(record, out)
    _stated_choices(record, out)
    _required_choices(record, ctx, out)
    _flags(record, ctx, out)
    out.flags = list(flags or []) + out.flags
    return out
