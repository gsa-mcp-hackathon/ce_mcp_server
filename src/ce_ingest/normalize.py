"""Column-name normalization."""

import re
from collections import defaultdict

import pyarrow as pa

from ce_ingest.errors import ColumnCollisionError
from ce_ingest.models import ColumnMeta

_SEPARATORS = re.compile(r"[\s\-]+")
_PUNCTUATION = re.compile(r"[^a-z0-9_]")


def normalize_name(name: str) -> str:
    """Trim, lowercase, each run of spaces/hyphens to one `_`, drop other punctuation.

    The words themselves are never changed, and underscores already in the name
    are kept: BLS names a flag column by replacing the variable's 5th character
    with `_`, so `FAM__IZE` (the flag for `FAM_SIZE`) becomes `fam__ize`, and a
    trailing `_` stays (`AMOUNT_`).
    """
    out = _SEPARATORS.sub("_", name.strip().lower())
    return _PUNCTUATION.sub("", out)


def normalize_columns(
    table: pa.Table, meta: list[ColumnMeta]
) -> tuple[pa.Table, list[ColumnMeta]]:
    """Rename every column with `normalize_name`, keeping the original in `meta`."""
    new_names = [normalize_name(n) for n in table.column_names]

    groups: dict[str, list[str]] = defaultdict(list)
    for original, new in zip(table.column_names, new_names):
        groups[new].append(original)
    collisions = {new: olds for new, olds in groups.items() if len(olds) > 1}
    if collisions:
        detail = "; ".join(
            f"{' and '.join(olds)} -> {new}" for new, olds in sorted(collisions.items())
        )
        raise ColumnCollisionError(f"columns collide after normalizing: {detail}")

    by_original = {m.original_name: m for m in meta}
    new_meta = [
        by_original[orig].model_copy(update={"name": new})
        for orig, new in zip(table.column_names, new_names)
        if orig in by_original
    ]
    return table.rename_columns(new_names), new_meta
