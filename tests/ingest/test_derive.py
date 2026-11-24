import pyarrow as pa
import pytest

from ce_ingest.derive import (
    add_cuid,
    add_mo_scope,
    add_partition_columns,
    split_by_qyear,
)
from ce_ingest.errors import ColumnCollisionError, MissingKeyError, QuarterError

# ── add_cuid ─────────────────────────────────────────────────────────────────


def test_cuid_from_string_newid(make_src):
    t = pa.table({"newid": ["05666064", "14917823"]})
    out = add_cuid(t, make_src("intrvw25/mtbi252"))
    assert out["cuid"].to_pylist() == ["0566606", "1491782"]
    assert out.schema.field("cuid").type == pa.string()


def test_newid_padded_to_8_digits(make_src):
    t = pa.table({"newid": ["5666064", " 4917823 "]})
    out = add_cuid(t, make_src("intrvw25/mtbi252"))
    assert out["newid"].to_pylist() == ["05666064", "04917823"]
    assert out["cuid"].to_pylist() == ["0566606", "0491782"]


def test_cuid_from_numeric_newid(make_src):
    t = pa.table({"newid": [5666064.0]})
    out = add_cuid(t, make_src("intrvw25/mtbi252"))
    assert out["newid"].to_pylist() == ["05666064"]
    assert out["cuid"].to_pylist() == ["0566606"]
    assert out.schema.field("newid").type == pa.string()


def test_cuid_placed_after_newid(make_src, tiny_mtbi):
    out = add_cuid(tiny_mtbi, make_src("intrvw25/mtbi252"))
    assert out.column_names[:2] == ["newid", "cuid"]


def test_source_cuid_padded_and_kept(make_src, tiny_fmli):
    out = add_cuid(tiny_fmli, make_src("intrvw25/fmli252"))
    assert out["cuid"].to_pylist() == ["0566606", "0566607", "0566609", "0491782"]
    assert out.column_names == tiny_fmli.column_names


def test_derived_cuid_matches_source_cuid(make_src, tiny_fmli):
    derived = add_cuid(tiny_fmli.drop_columns(["cuid"]), make_src("intrvw25/fmli252"))
    source = add_cuid(tiny_fmli, make_src("intrvw25/fmli252"))
    assert derived["cuid"].to_pylist() == source["cuid"].to_pylist()


def test_source_cuid_mismatch_raises(make_src):
    t = pa.table({"newid": ["05666064"], "cuid": [566607.0]})
    with pytest.raises(MissingKeyError, match="differs"):
        add_cuid(t, make_src("intrvw25/fmli252"))


def test_cuid_no_newid_raises(make_src):
    with pytest.raises(MissingKeyError, match="no newid"):
        add_cuid(pa.table({"ucc": ["1"]}), make_src("intrvw25/mtbi252"))


def test_cuid_null_newid_raises(make_src):
    with pytest.raises(MissingKeyError, match="null on 1"):
        add_cuid(pa.table({"newid": ["05666064", None]}), make_src("intrvw25/mtbi252"))


@pytest.mark.parametrize("bad", ["49x7823", "123456789"])
def test_cuid_bad_newid_raises(make_src, bad):
    with pytest.raises(MissingKeyError, match="8 digits"):
        add_cuid(pa.table({"newid": [bad]}), make_src("intrvw25/mtbi252"))


# ── add_mo_scope ─────────────────────────────────────────────────────────────


def _months(values, type_=None):
    return pa.table({"qintrvmo": pa.array(values, type_)})


@pytest.mark.parametrize(
    "rel, expected",
    [("intrvw25/fmli251x", [0, 1, 2]), ("intrvw25/fmli261", [3, 2, 1])],
)
def test_mo_scope_first_quarter(make_src, rel, expected):
    out, nulls = add_mo_scope(_months(["01", "02", "03"]), make_src(rel))
    assert out["mo_scope"].to_pylist() == expected
    assert out.schema.field("mo_scope").type == pa.int32()
    assert nulls == 0


def test_mo_scope_numeric_qintrvmo(make_src):
    out, _ = add_mo_scope(_months([1.0, 2.0, 3.0]), make_src("intrvw25/fmli251x"))
    assert out["mo_scope"].to_pylist() == [0, 1, 2]


def test_mo_scope_middle_quarter(make_src):
    out, nulls = add_mo_scope(_months(["04", "05", None]), make_src("intrvw25/fmli252"))
    assert out["mo_scope"].to_pylist() == [3, 3, 3]
    assert nulls == 0


def test_mo_scope_fmld(make_src, tiny_fmld):
    out, _ = add_mo_scope(tiny_fmld, make_src("diary25/fmld251"))
    assert out["mo_scope"].to_pylist() == [3, 3]


def test_mo_scope_bad_month_is_null(make_src):
    out, nulls = add_mo_scope(
        _months(["02", None, "05", "", "ab"]), make_src("intrvw25/fmli251x")
    )
    assert out["mo_scope"].to_pylist() == [1, None, None, None, None]
    assert nulls == 4


def test_mo_scope_other_table_unchanged(make_src, tiny_mtbi):
    out, nulls = add_mo_scope(tiny_mtbi, make_src("intrvw25/memi251x"))
    assert out is tiny_mtbi
    assert nulls == 0


# ── add_partition_columns ────────────────────────────────────────────────────


def test_partition_columns(make_src, tiny_mtbi):
    out = add_partition_columns(tiny_mtbi, make_src("intrvw25/mtbi261"))
    assert out.column_names[-3:] == ["survey_year", "quarter", "quarter_code"]
    assert out.schema.field("survey_year").type == pa.int32()
    assert out.schema.field("quarter").type == pa.int32()
    assert out.schema.field("quarter_code").type == pa.string()
    assert set(out["survey_year"].to_pylist()) == {2026}
    assert set(out["quarter"].to_pylist()) == {1}
    assert set(out["quarter_code"].to_pylist()) == {"1"}


def test_partition_column_clash(make_src):
    with pytest.raises(ColumnCollisionError, match="quarter"):
        add_partition_columns(pa.table({"quarter": [1]}), make_src("intrvw25/mtbi252"))


# ── split_by_qyear ───────────────────────────────────────────────────────────


def test_split_by_qyear(make_src, tiny_expn):
    slices = split_by_qyear(tiny_expn, make_src("intrvw25/expn25/rnt25"))
    got = [
        (s.survey_year, s.quarter, s.quarter_code, t["cost"].to_pylist())
        for s, t in slices
    ]
    assert got == [
        (2025, 1, "1x", [1.0, 6.0]),
        (2025, 2, "2", [2.0]),
        (2025, 3, "3", [3.0]),
        (2025, 4, "4", [4.0]),
        (2026, 1, "1", [5.0]),
    ]
    assert all("qyear" in t.column_names for _, t in slices)
    assert all(s.kind == "multi_quarter" for s, _ in slices)


def test_split_numeric_qyear(make_src):
    t = pa.table({"qyear": [20252.0, 20261.0]})
    slices = split_by_qyear(t, make_src("intrvw25/expn25/rnt25"))
    assert [s.quarter_code for s, _ in slices] == ["2", "1"]


@pytest.mark.parametrize("bad", ["20255", "20241", None])
def test_split_bad_qyear_raises(make_src, bad):
    t = pa.table({"qyear": pa.array(["20252", bad], pa.string())})
    with pytest.raises(QuarterError):
        split_by_qyear(t, make_src("intrvw25/expn25/rnt25"))


def test_split_quarterly_is_one_slice(make_src, tiny_mtbi):
    src = make_src("intrvw25/mtbi252")
    assert split_by_qyear(tiny_mtbi, src) == [(src, tiny_mtbi)]
