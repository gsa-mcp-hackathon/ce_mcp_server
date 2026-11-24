import pyarrow as pa
import pytest

from ce_ingest.errors import ColumnCollisionError
from ce_ingest.models import ColumnMeta
from ce_ingest.normalize import normalize_columns, normalize_name


@pytest.mark.parametrize(
    "raw, expected",
    [
        (" FINLWT21 ", "finlwt21"),
        ("Food-Home", "food_home"),
        ("A  B", "a_b"),
        ("AMOUNT_", "amount_"),
        ("Cost ($)", "cost_"),
        ("a__b", "a__b"),
        ("FAM__IZE", "fam__ize"),
    ],
)
def test_normalize_name(raw, expected):
    assert normalize_name(raw) == expected


def _meta(names):
    return [
        ColumnMeta(
            name=n,
            original_name=n,
            sas_type="numeric",
            length=8,
            label=None,
            format=None,
        )
        for n in names
    ]


def test_normalize_columns_lowercases():
    t = pa.table({"NEWID": ["1"], "FinLwt21": [1.0]})
    out, meta = normalize_columns(t, _meta(t.column_names))
    assert out.column_names == ["newid", "finlwt21"]
    assert [(m.name, m.original_name) for m in meta] == [
        ("newid", "NEWID"),
        ("finlwt21", "FinLwt21"),
    ]


def test_normalize_columns_collision():
    t = pa.table({"FINCBTAX": [1.0], "fincbtax": [2.0]})
    with pytest.raises(ColumnCollisionError, match="FINCBTAX and fincbtax"):
        normalize_columns(t, _meta(t.column_names))
