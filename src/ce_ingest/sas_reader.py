"""Read one SAS file into a `pyarrow.Table` with an explicit schema."""

import logging

import pyarrow as pa
import pyreadstat

from ce_ingest.errors import SasReadError
from ce_ingest.models import ColumnMeta, SasType, SourceFile

log = logging.getLogger(__name__)

_SAS_TYPES: dict[str, SasType] = {"double": "numeric", "string": "character"}
_ARROW_TYPES: dict[SasType, pa.DataType] = {
    "numeric": pa.float64(),
    "character": pa.string(),
}


def read_sas(
    src: SourceFile, encoding: str | None = None
) -> tuple[pa.Table, list[ColumnMeta], dict[str, int]]:
    """Read `src` and return the table, column metadata and null counts per column.

    SAS numerics become float64 (never downcast); characters stay strings exactly
    as stored, so codes keep leading zeros and blanks stay blank. Every SAS
    numeric missing value (`.`, `.A`-`.Z`, `._`) becomes null.
    """
    try:
        df, meta = pyreadstat.read_sas7bdat(
            str(src.path),
            encoding=encoding,
            disable_datetime_conversion=True,  # date-formatted numerics stay float64
        )
    except Exception as exc:  # pyreadstat raises several unrelated types
        raise SasReadError(f"{src.label}: can't read SAS file: {exc}") from exc

    if meta.number_rows is not None and meta.number_rows != len(df):
        raise SasReadError(
            f"{src.label}: read {len(df)} rows, header says {meta.number_rows}"
        )

    labels = meta.column_names_to_labels or {}
    columns: list[ColumnMeta] = []
    fields: list[pa.Field] = []
    for name in meta.column_names:
        rs_type = meta.readstat_variable_types[name]
        sas_type = _SAS_TYPES.get(rs_type)
        if sas_type is None:
            raise SasReadError(
                f"{src.label}: column {name!r} has unexpected type {rs_type!r}"
            )
        columns.append(
            ColumnMeta(
                name=name,
                original_name=name,
                sas_type=sas_type,
                length=meta.variable_storage_width.get(name),
                label=labels.get(name) or None,
                format=meta.original_variable_types.get(name) or None,
            )
        )
        fields.append(pa.field(name, _ARROW_TYPES[sas_type]))

    try:
        table = pa.Table.from_pandas(df, schema=pa.schema(fields), preserve_index=False)
    except (pa.ArrowInvalid, pa.ArrowTypeError) as exc:
        raise SasReadError(f"{src.label}: can't convert to Arrow: {exc}") from exc
    table = table.replace_schema_metadata(None)  # drop the pandas metadata blob

    missing = {name: table.column(name).null_count for name in table.column_names}
    log.debug("%s: %d rows, %d columns", src.label, table.num_rows, table.num_columns)
    return table, columns, missing
