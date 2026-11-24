"""Generate the column entries of an ODCS table sidecar, from scratch.

Every sidecar is generated: nothing is read back from a previous one. Column
by column, the generator sets:

- `name` and `id`: the stored name (the `id` keeps ODCS foreign keys possible);
- `logicalType`: from the stored type; `ce:sourceName`: the BLS spelling;
- `businessName` and `description`: from the dictionary, with its formula;
- `enum`: the dictionary's code list for the release;
- flag columns: `ce:role: flag`, `ce:flagFor`, and the BLS flag codes;
- roles it can tell from the name: identifiers, weight, replicate weights,
  interview period, and coded columns as nominal dimensions;
- general rules: the loader's columns, the row key (required, and the primary
  key when it is one column), the weight (required, never null), and the
  interview year and month (required).

Columns still open after that get a role, scale, unit and level from the
general rules in `rules.py` (dictionary text and BLS naming conventions). What a
person decided comes from the table's overrides file and is merged on top.
Anything still undecided is listed in the review report, and the column itself
is tagged `needs-review` plus one `review:<reason>` tag per reason, so the
sidecar shows which of its columns are not settled yet.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from ce_ingest.profile import same_code
from ce_meta.sidecars import has_verify
from ce_sidecars import rules
from ce_sidecars.dictionary import FileDictionary

# BLS Interview Survey data-flag codes, and what each means in the vocabulary:
# one meaning, or a list when the code states several (Sidecar Plan decision 18).
# A, B, C and H are blanks, never values.
FLAG_CODES: list[tuple[str, str, str | list[str]]] = [
    ("A", "Valid blank; no response was expected", "skip_pattern"),
    (
        "B",
        "Invalid blank; nonresponse inconsistent with other data reported",
        "nonresponse",
    ),
    ("C", 'Blank from "don\'t know", refusal, or other nonresponse', "nonresponse"),
    ("D", "Valid value, unadjusted", "valid"),
    ("E", "Valid value, allocated", "allocated"),
    ("F", "Valid value, imputed or adjusted in some other way", "imputed"),
    ("G", "Valid value, allocated and imputed", ["allocated", "imputed"]),
    (
        "H",
        "Valid blank; a parent record whose amount was allocated to other records",
        "parent_record",
    ),
    ("T", "Top-coded or suppressed", "topcoded"),
    ("U", "Allocated, then top-coded or suppressed", ["allocated", "topcoded"]),
    (
        "V",
        "Imputed or adjusted, then top-coded or suppressed",
        ["imputed", "topcoded"],
    ),
    (
        "W",
        "Allocated and imputed, then top-coded or suppressed",
        ["allocated", "imputed", "topcoded"],
    ),
]
TOPCODE_FLAGS = frozenset({"T", "U", "V", "W"})

LOGICAL_TYPES = {"VARCHAR": "string", "DOUBLE": "number", "INTEGER": "integer"}
FIELD_ORDER = (
    "name",
    "id",
    "businessName",
    "logicalType",
    "primaryKey",
    "required",
    "description",
    "enum",
    "quality",
    "tags",
    "customProperties",
)
REVIEW_TAG = "needs-review"
_REPLICATE = re.compile(r"^wtrep(\d{2})$")
INTERVIEW_PERIOD = {"qintrvyr": "interval", "qintrvmo": "ordinal"}

# Columns BLS names the same way in every file that has them: what identifies a
# row, when it refers to, and where it came from. Their meaning is clear from
# the name, in whichever table they appear.
_IDENTIFIER = {"ce:role": "identifier", "ce:scale": "nominal"}
STRUCTURAL: dict[str, dict] = {
    "membno": _IDENTIFIER,  # the member within the consumer unit
    "seqno": _IDENTIFIER,  # the record within a questionnaire section
    "alcno": _IDENTIFIER,  # the allocated part of a record
    "uccseq": _IDENTIFIER,  # the item code's place in a mapping
    "impnum": _IDENTIFIER,  # which of the five imputed versions a row is
    "ref_mo": {"ce:role": "period", "ce:scale": "ordinal"},
    "refmo": {"ce:role": "period", "ce:scale": "ordinal"},
    "ref_yr": {"ce:role": "period", "ce:scale": "interval"},
    "refyr": {"ce:role": "period", "ce:scale": "interval"},
    "qyear": {"ce:role": "period", "ce:scale": "ordinal"},  # interview year, quarter
    "rec_orig": {"ce:role": "dimension", "ce:scale": "nominal"},
    "expname": {"ce:role": "dimension", "ce:scale": "nominal"},
}
# The item code. Where its hierarchy is defined is not settled yet (Sidecar
# Plan open item 13), so the source stays VERIFY.
ITEM_CODE = {
    "ce:role": "item_code",
    "ce:scale": "nominal",
    "ce:codeHierarchy": {"scheme": "ucc", "source": "VERIFY"},
}


def _not_null(description: str) -> dict:
    return {
        "metric": "nullValues",
        "mustBe": 0,
        "description": description,
        "dimension": "completeness",
        "type": "library",
        "severity": "error",
    }


# Columns the loader adds to every table (mo_scope: fmli and fmld only). They
# are described the same way everywhere, so they live here, not in overrides.
LOADER_COLUMNS: dict[str, dict] = {
    "cuid": {
        "businessName": "Consumer unit identifier",
        "required": True,
        "description": (
            "The consumer unit, shared by all its interviews: newid without its "
            "last digit, added by the loader. Where BLS supplies CUID (fmli, "
            "fmld) it is kept, padded to 7 digits, and checked against newid."
        ),
        "quality": [_not_null("Every interview belongs to a consumer unit.")],
        "customProperties": {"ce:role": "identifier", "ce:scale": "nominal"},
    },
    "mo_scope": {
        "businessName": "Months in scope",
        "description": (
            "Added by the loader: how many months of the interview's three-month "
            "reference period fall in the file's year (1x file: interview month "
            "minus 1; next-year 1 file: 4 minus interview month; other files: 3)."
        ),
        "customProperties": {
            "ce:role": "measure",
            "ce:scale": "ratio",
            "ce:unit": "months",
            "ce:level": "consumer_unit",
        },
    },
    "survey_year": {
        "businessName": "Survey year (partition)",
        "required": True,
        "description": "Added by the loader, from the file name.",
        "customProperties": {"ce:role": "period", "ce:scale": "interval"},
    },
    "quarter": {
        "businessName": "Quarter (1-4)",
        "required": True,
        "description": "Added by the loader, from the file name.",
        "customProperties": {"ce:role": "period", "ce:scale": "ordinal"},
    },
    "quarter_code": {
        "businessName": "Quarter code (partition)",
        "required": True,
        "description": (
            "Added by the loader, from the file name: 1x is quarter 1 of the "
            "release year, 1 is quarter 1 of the following year."
        ),
        "enum": [{"value": v} for v in ("1x", "1", "2", "3", "4")],
        "customProperties": {"ce:role": "period", "ce:scale": "nominal"},
    },
}


@dataclass(frozen=True)
class StoredColumn:
    name: str
    db_type: str  # DuckDB type: VARCHAR, DOUBLE, INTEGER
    source_name: str | None  # None for columns the loader added


@dataclass
class Review:
    """Columns that still need a person's decision, grouped by reason."""

    items: dict[str, list[str]] = field(default_factory=dict)
    notes: dict[tuple[str, str], str] = field(default_factory=dict)
    tags: dict[str, list[str]] = field(default_factory=dict)

    def add(
        self, reason: str, column: str, note: str | None = None, tag: str | None = None
    ) -> None:
        """List a column under a reason; `tag` (review:<x>) also marks the column."""
        self.items.setdefault(reason, []).append(column)
        if note:
            self.notes[(reason, column)] = note
        if tag:
            column_tags = self.tags.setdefault(column, [])
            if tag not in column_tags:
                column_tags.append(tag)

    def label(self, reason: str, column: str) -> str:
        note = self.notes.get((reason, column))
        return f"`{column}` ({note})" if note else f"`{column}`"


def _pattern_role(name: str) -> dict | None:
    """Role and scale for columns whose meaning is clear from the name."""
    if name == "newid":
        return {"ce:role": "identifier", "ce:scale": "nominal"}
    if name == "finlwt21":
        return {"ce:role": "weight", "ce:scale": "ratio"}
    m = _REPLICATE.match(name)
    if m:
        return {"ce:role": "replicate_weight", "ce:replicateIndex": int(m[1])}
    if name in INTERVIEW_PERIOD:
        return {"ce:role": "period", "ce:scale": INTERVIEW_PERIOD[name]}
    if name == "ucc":
        return {k: dict(v) if isinstance(v, dict) else v for k, v in ITEM_CODE.items()}
    if name in STRUCTURAL:
        return dict(STRUCTURAL[name])
    return None


def _business_name(text: str) -> str:
    first = re.split(r"(?<=[a-z])\. ", text, maxsplit=1)[0].strip().rstrip(".")
    return first[:120] if first else text[:120]


def _enum(codes, db_type: str) -> list[dict]:
    out = []
    for c in codes:
        value: object = c.value
        if db_type == "DOUBLE":
            try:
                value = float(c.value) if "." in c.value else int(c.value)
            except ValueError:
                pass
        out.append({"value": value, "label": c.label})
    return out


def _generated_entry(
    col: StoredColumn, dictionary: FileDictionary, flag_target: str | None
) -> dict:
    """What the sources alone say about one column (customProperties as a dict)."""
    entry: dict = {"name": col.name, "id": col.name}
    custom: dict = {"ce:sourceName": col.source_name} if col.source_name else {}
    entry["logicalType"] = LOGICAL_TYPES[col.db_type]

    if flag_target is not None:
        entry["businessName"] = f"Flag for {flag_target}"
        entry["description"] = (
            f"Data flag for {flag_target}: BLS's Interview Survey flag codes."
        )
        entry["enum"] = [{"value": c, "label": label} for c, label, _ in FLAG_CODES]
        custom.update(
            {
                "ce:role": "flag",
                "ce:flagFor": flag_target,
                # a fresh list per column, so the YAML has no shared references
                "ce:flagMeanings": {
                    c: list(m) if isinstance(m, list) else m for c, _, m in FLAG_CODES
                },
            }
        )
        entry["customProperties"] = custom
        return entry

    if col.name in LOADER_COLUMNS:
        block = LOADER_COLUMNS[col.name]
        entry.update({k: v for k, v in block.items() if k != "customProperties"})
        custom.update(block["customProperties"])
        entry["customProperties"] = custom
        return entry

    var = dictionary.variables.get(col.name)
    if var is None:
        entry["description"] = (
            f"Not in the {dictionary.release} BLS data dictionary for "
            f"{dictionary.file}."
        )
    else:
        entry["businessName"] = _business_name(var.description)
        text = var.description
        if var.formula:
            text = text.rstrip() + ("" if text.rstrip().endswith(".") else ".")
            text += f" Formula: {var.formula}"
        entry["description"] = text

    codes = dictionary.codes.get(col.name)
    if codes:
        entry["enum"] = _enum(codes, col.db_type)
    role = _pattern_role(col.name)
    if role is not None:
        custom.update(role)
    elif codes:
        custom.update({"ce:role": "dimension", "ce:scale": "nominal"})
    entry["customProperties"] = custom
    return entry


def _apply_rules(entry: dict, row_key: list[str], weight_column: str | None) -> None:
    """General rules that hold in every table."""
    name = entry["name"]
    if name in row_key:
        entry["required"] = True
        if len(row_key) == 1:
            entry["primaryKey"] = True
    if name == weight_column:
        entry["required"] = True
        entry["quality"] = [_not_null("Every record carries a weight.")]
    if name in INTERVIEW_PERIOD:
        entry["required"] = True


def _apply_override(entry: dict, override: dict) -> None:
    """Merge a person's decisions: fields replace, ce: properties merge by name."""
    for key, value in override.items():
        if key == "customProperties":
            entry["customProperties"].update(value)
        else:
            entry[key] = value


def _ordered(entry: dict) -> dict:
    out = {k: entry[k] for k in FIELD_ORDER if k in entry}
    out.update({k: v for k, v in entry.items() if k not in out})
    out["customProperties"] = [
        {"property": k, "value": v} for k, v in entry["customProperties"].items()
    ]
    return out


def build_properties(
    columns: Iterable[StoredColumn],
    dictionary: FileDictionary,
    overrides: dict[str, dict] | None = None,
    observed: dict[str, set[str]] | None = None,
    row_key: list[str] | None = None,
    weight_column: str | None = None,
    level: str = rules.DEFAULT_LEVEL,
) -> tuple[list[dict], Review]:
    """One ODCS property per stored column, in stored order, plus the review list.

    `overrides` maps column name -> the fields a person set for it.
    `observed` maps column name -> the distinct values found in the stored table,
    used to spot codes the dictionary lacks and flags that show top-coding.
    `level` is the level of the table's rows, the rules' default ce:level.
    """
    overrides = overrides or {}
    observed = observed or {}
    row_key = row_key or []
    review = Review()
    flag_of = dictionary.flag_of()
    columns = list(columns)

    unknown = sorted(set(overrides) - {c.name for c in columns})
    if unknown:
        raise ValueError(f"overrides name columns that aren't stored: {unknown}")

    # Pass 1: what the sources say, then the general rules, then the overrides,
    # then what imputation sets inherit from their base column.
    targets: dict[str, str | None] = {}
    entries: dict[str, dict] = {}
    for col in columns:
        # A flag the dictionary doesn't link (its value column has no current
        # entry) can be declared in the overrides with ce:flagFor.
        declared = overrides.get(col.name, {}).get("customProperties", {})
        targets[col.name] = flag_of.get(col.name) or declared.get("ce:flagFor")
        entry = _generated_entry(col, dictionary, targets[col.name])
        _apply_rules(entry, row_key, weight_column)
        entries[col.name] = entry
    ruled = rules.assign(entries, dictionary, level)
    for name, override in overrides.items():
        _apply_override(entries[name], override)
    rules.inherit(entries, ruled)
    for reason, column, tag in ruled.review:
        review.add(reason, column, tag=tag)

    # Pass 2: what is still open goes on the review list.
    for col in columns:
        entry = entries[col.name]
        target = targets[col.name]
        custom = entry["customProperties"]

        # A flag that shows top-coding puts its value column on the review
        # list, until a person (or its imputation base) has settled ce:topcoded.
        # A flag's value column isn't always stored, so it may have no entry.
        decided = target in entries and isinstance(
            entries[target]["customProperties"].get("ce:topcoded"), bool
        )
        if (
            target is not None
            and not decided
            and observed.get(col.name, set()) & TOPCODE_FLAGS
        ):
            review.add(
                "Possibly top-coded (its flag shows T, U, V or W)",
                target,
                tag="review:topcoded",
            )
        if (
            col.name not in LOADER_COLUMNS
            and target is None
            and col.name not in dictionary.variables
            and "description" not in overrides.get(col.name, {})
        ):
            review.add(
                "Not in the data dictionary", col.name, tag="review:not-in-dictionary"
            )
        if "ce:role" not in custom:
            review.add("Role and scale not assigned", col.name, tag="review:role")
        for prop, value in custom.items():
            if has_verify(value):
                review.add(f"Still VERIFY: {prop}", col.name, tag="review:verify")
        if "enum" in entry and target is None:
            known = {str(e["value"]) for e in entry["enum"]}
            seen = observed.get(col.name, set())
            if "" in seen and "ce:missingCodes" not in custom:
                review.add(
                    'Blank values: candidate for ce:missingCodes [""]',
                    col.name,
                    tag="review:missing-codes",
                )
            extra = sorted(v for v in seen if v != "" and not same_code(v, known))
            if extra:
                review.add(
                    "Stored values not in the dictionary's codes",
                    col.name,
                    ", ".join(extra[:5]) + (" ..." if len(extra) > 5 else ""),
                    tag="review:unknown-codes",
                )

    # Tag after the loop: a flag column can raise a review item on its value
    # column, which may come earlier in the stored order.
    for entry in entries.values():
        added = review.tags.get(entry["name"])
        if added:
            tags = list(entry.get("tags", []))
            tags += [t for t in [REVIEW_TAG, *added] if t not in tags]
            entry["tags"] = tags
    return [_ordered(e) for e in entries.values()], review


def review_markdown(table: str, version: str, review: Review, total: int) -> str:
    lines = [
        f"# Review list: {table} sidecar {version}",
        "",
        f"Generated with the sidecar. {total} columns are described; the ones below "
        "still need a person's decision. To settle one, add it to the table's "
        "overrides file (`sidecars/overrides/`) and generate the sidecar again; "
        "the sidecar itself is never edited by hand.",
        "",
        "| Reason | Columns | Count |",
        "| --- | --- | --- |",
    ]
    ordered = sorted(review.items.items(), key=lambda kv: -len(kv[1]))
    for reason, cols in ordered:
        shown = ", ".join(review.label(reason, c) for c in cols[:12])
        more = f" and {len(cols) - 12} more" if len(cols) > 12 else ""
        lines.append(f"| {reason} | {shown}{more} | {len(cols)} |")
    lines.append("")
    for reason, cols in ordered:
        if len(cols) > 12:
            labels = ", ".join(review.label(reason, c) for c in cols)
            lines += [f"## {reason}", "", labels, ""]
    return "\n".join(lines)
