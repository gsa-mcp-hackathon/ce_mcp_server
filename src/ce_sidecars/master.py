"""Keep the master sidecar's list of detailed expenditure tables in step.

The master is written by hand, with comments, so it is changed as text and only
between marker lines: one marked block of `ce:tables` entries and one of
`ce:relationships` entries, both rewritten from the stored tables. Everything
outside the markers is left exactly as it was.
"""

import re

TABLE_ID_PREFIX = "ce-interview-"
FMLI = TABLE_ID_PREFIX + "fmli"
INDENT = "      "
_BLOCKS = {
    "ce:tables": "expn tables",
    "ce:relationships": "expn relationships",
}


def _begin(label: str) -> str:
    return f"{INDENT}# >>> {label}: written by `ce-sidecars sync-master`; do not edit"


def _end(label: str) -> str:
    return f"{INDENT}# <<< {label}"


def table_line(table: str, version: str) -> str:
    return (
        f"{INDENT}- {{sidecar: {TABLE_ID_PREFIX}{table}, version: {version}, "
        "rowRepresents: expn_record, carriesWeight: false}"
    )


def relationship_line(table: str) -> str:
    return (
        f"{INDENT}- {{parent: {FMLI}, child: {TABLE_ID_PREFIX}{table}, "
        "keys: {parent: [newid], child: [newid]}, cardinality: one_to_many, "
        "childRowsOptional: true, weightFrom: parent, "
        "note: No rows when the section did not apply.}"
    )


def _replace_block(lines: list[str], prop: str, body: list[str]) -> list[str]:
    label = _BLOCKS[prop]
    begin, end = _begin(label), _end(label)
    if begin in lines and end in lines:
        i, j = lines.index(begin), lines.index(end)
        return lines[:i] + ([begin, *body, end] if body else []) + lines[j + 1 :]
    if not body:
        return lines
    start = next(
        (i for i, line in enumerate(lines) if line.strip() == f"- property: {prop}"),
        None,
    )
    if start is None:
        raise ValueError(f"the master sidecar has no {prop} property")
    # the property's value runs until the next line indented less than its items
    last = start + 1
    for i in range(start + 2, len(lines)):
        line = lines[i]
        if not line.strip():
            continue
        if len(line) - len(line.lstrip()) < len(INDENT):
            break
        last = i
    return lines[: last + 1] + [begin, *body, end] + lines[last + 1 :]


def sync_master(text: str, versions: dict[str, str]) -> str:
    """The master's text with its marked blocks listing exactly `versions`:
    stored detailed expenditure table (`expn_rnt`) -> its sidecar version."""
    tables = sorted(versions)
    bad = [t for t in tables if not re.fullmatch(r"expn_[a-z0-9]+", t)]
    if bad:
        raise ValueError(f"not detailed expenditure tables: {bad}")
    lines = text.split("\n")
    lines = _replace_block(
        lines, "ce:tables", [table_line(t, versions[t]) for t in tables]
    )
    lines = _replace_block(
        lines, "ce:relationships", [relationship_line(t) for t in tables]
    )
    return "\n".join(lines)
