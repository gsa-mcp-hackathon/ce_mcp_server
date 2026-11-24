"""Schema evolution checks against what's already stored."""

import pyarrow as pa

from ce_ingest.errors import TypeChangeError
from ce_ingest.models import SourceFile
from ce_ingest.writers.base import Writer


def current_schema(writer: Writer, src: SourceFile) -> pa.Schema | None:
    """The live table's schema, leaving out the partition `src` will replace."""
    return writer.read_schema(src.survey, src.table, exclude=src)


def check_schema(
    new: pa.Schema,
    current: pa.Schema | None,
    src: SourceFile,
    earlier_source: str | None = None,
) -> tuple[list[str], list[str]]:
    """Compare by name; return sorted `(added_columns, dropped_columns)`.

    A shared column whose type changed raises `TypeChangeError`. An all-null
    (`null`-typed) column is compatible with any type. `earlier_source` names
    the file that set the stored type, for the error message.
    """
    if current is None:
        return [], []

    new_types = {f.name: f.type for f in new}
    cur_types = {f.name: f.type for f in current}

    for name in sorted(new_types.keys() & cur_types.keys()):
        a, b = cur_types[name], new_types[name]
        if a == b or pa.types.is_null(a) or pa.types.is_null(b):
            continue
        where = f" (stored type set by {earlier_source})" if earlier_source else ""
        raise TypeChangeError(
            f"{src.label}: column {name!r} is {b}, but the table has {a}{where}"
        )

    added = sorted(new_types.keys() - cur_types.keys())
    dropped = sorted(cur_types.keys() - new_types.keys())
    return added, dropped
