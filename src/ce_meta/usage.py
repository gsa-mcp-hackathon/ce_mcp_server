"""How a request may use a column, read from its sidecar facts.

One table, used by every lookup tool and by request capture, so they cannot
disagree. It says what a column is, and never judges whether a request that
uses it is sound: a measure whose unit is still under review is still a measure,
with a note.
"""

from ce_meta.models import Usage
from ce_meta.sidecars import NEEDS_REVIEW, Column, has_verify

ROLE_USES: dict[str, tuple[Usage, ...]] = {
    "measure": ("measure", "filter"),
    "dimension": ("grouping", "filter"),
    "period": ("grouping", "filter"),
    "item_code": ("grouping", "filter"),
    # never analysis variables
    "identifier": (),
    "weight": (),
    "replicate_weight": (),
    "flag": (),
}

NO_SIDECAR = "no sidecar"
NO_ROLE = "role not assigned"
UNIT_OPEN = "unit under review"


QUANTITY_SCALES = ("ratio", "interval")


def is_quantity(column: Column) -> bool:
    """A dimension that is a number of something: it has a ratio or interval
    scale and a unit. Household size groups a result, and can be averaged."""
    return (
        column.get("ce:role") == "dimension"
        and column.get("ce:scale") in QUANTITY_SCALES
        and bool(column.get("ce:unit"))
    )


def usable_as(column: Column | None) -> list[Usage]:
    """The slots of a request this column can fill; empty when it can fill none."""
    if column is None:
        return []
    uses = list(ROLE_USES.get(column.get("ce:role"), ()))
    if is_quantity(column):
        uses.insert(0, "measure")
    return uses


def review_notes(column: Column | None) -> list[str]:
    """What a reader must know before relying on the column's facts."""
    if column is None:
        return [NO_SIDECAR]
    notes: list[str] = []
    if not column.get("ce:role"):
        notes.append(NO_ROLE)
    for prop, value in column.facts.items():
        if not has_verify(value):
            continue
        notes.append(UNIT_OPEN if prop == "ce:unit" else f"{prop} under review")
    if NEEDS_REVIEW in column.tags:
        notes.append(NEEDS_REVIEW)
    return notes
