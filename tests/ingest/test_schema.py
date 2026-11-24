import pyarrow as pa
import pytest

from ce_ingest.errors import TypeChangeError
from ce_ingest.schema import check_schema, current_schema


class FakeWriter:
    """Holds partition schemas in memory, keyed by (year, quarter_code)."""

    def __init__(self, parts: dict[tuple[int, str], pa.Schema]):
        self.parts = parts

    def read_schema(self, survey, table, exclude=None):
        keep = [
            s
            for key, s in self.parts.items()
            if exclude is None or key != (exclude.survey_year, exclude.quarter_code)
        ]
        return pa.unify_schemas(keep) if keep else None


A = pa.schema([("newid", pa.string()), ("cost", pa.float64())])
B = pa.schema([("newid", pa.string()), ("ucc", pa.string())])


def test_current_schema_new_table(make_src):
    assert current_schema(FakeWriter({}), make_src("intrvw25/mtbi252")) is None


def test_current_schema_union(make_src):
    w = FakeWriter({(2025, "1x"): A, (2025, "2"): B})
    schema = current_schema(w, make_src("intrvw25/mtbi253"))
    assert schema.names == ["newid", "cost", "ucc"]


def test_current_schema_excludes_replaced_partition(make_src):
    w = FakeWriter({(2025, "1x"): A, (2025, "2"): B})
    schema = current_schema(w, make_src("intrvw25/mtbi252"))
    assert schema.names == ["newid", "cost"]


def test_check_schema_identical(make_src):
    assert check_schema(A, A, make_src("intrvw25/mtbi252")) == ([], [])


def test_check_schema_new_table(make_src):
    assert check_schema(A, None, make_src("intrvw25/mtbi252")) == ([], [])


def test_check_schema_added_and_dropped(make_src):
    assert check_schema(B, A, make_src("intrvw25/mtbi252")) == (["ucc"], ["cost"])


def test_check_schema_null_type_compatible(make_src):
    new = pa.schema([("newid", pa.string()), ("cost", pa.null())])
    assert check_schema(new, A, make_src("intrvw25/mtbi252")) == ([], [])


def test_check_schema_type_change(make_src):
    new = pa.schema([("newid", pa.string()), ("cost", pa.string())])
    with pytest.raises(TypeChangeError, match="'cost'.*mtbi251x"):
        check_schema(new, A, make_src("intrvw25/mtbi252"), earlier_source="mtbi251x")
